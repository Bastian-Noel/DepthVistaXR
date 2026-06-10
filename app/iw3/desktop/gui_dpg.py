import json
import os
from os import path
import queue
import threading
import traceback

import dearpygui.dearpygui as dpg
import numpy as np
from PIL import Image, ImageDraw, ImageGrab
import torch

from .gui import HAS_WINDOWS_CAPTURE, HAS_WINDOWS_CAPTURE_CUDA
from .screenshot_process import get_window_rect_by_title
from .translations import LANGUAGES, translate
from .utils import (
    create_parser,
    enum_window_names,
    get_monitor_size_list,
    iw3_desktop_main,
    set_state_args,
)


APP_NAME = "DepthVista XR"
CONFIG_PATH = path.join(path.dirname(__file__), "..", "..", "tmp", "depthvista-xr.json")
PREVIEW_WIDTH = 480
PREVIEW_HEIGHT = 270

PROFILES = {
    "fluid": {
        "depth_model": "Distill_Any_S",
        "method": "mlbw_l2s",
        "depth_resolution": 392,
        "fps": 60,
    },
    "balanced": {
        "depth_model": "VDA_Stream_S",
        "method": "row_flow_v3",
        "depth_resolution": 512,
        "fps": 60,
    },
    "quality": {
        "depth_model": "VDA_Stream_S",
        "method": "row_flow_v3_sym",
        "depth_resolution": 720,
        "fps": 30,
    },
}

PROFILE_KEYS = {
    "fluid": "profile_fluid",
    "balanced": "profile_balanced",
    "quality": "profile_quality",
}
SOURCE_KEYS = {"screen": "source_screen", "window": "source_window"}
PROJECTION_KEYS = {"curved": "projection_curved", "flat": "projection_flat"}
CONTROL_KEYS = {"desktop": "control_desktop", "cinema": "control_cinema"}


class EventBridge:
    def __init__(self, events):
        self.events = events

    def update(self, estimated_fps, screenshot_fps, streaming_fps, screen_size):
        self.events.put(
            (
                "fps",
                {
                    "estimated": estimated_fps,
                    "capture": screenshot_fps,
                    "output": streaming_fps,
                    "screen": screen_size,
                },
            )
        )

    def set_url(self, _url):
        pass


class DepthVistaApp:
    def __init__(self):
        self.events = queue.Queue()
        self.worker = None
        self.stop_event = threading.Event()
        self.args_lock = threading.Lock()
        self.depth_model = None
        self.current_args = None
        self.running = False
        self.config = self._load_config()
        self.language = self.config.get("language", "en")
        if self.language not in LANGUAGES:
            self.language = "en"
        self.preview_requested = threading.Event()
        self.preview_stop = threading.Event()
        self.preview_thread = None
        self.preview_selection = None
        self.last_preview_time = 0.0

    def _load_config(self):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as config_file:
                return json.load(config_file)
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        values = {
            tag: dpg.get_value(tag)
            for tag in (
                "profile",
                "source_type",
                "source",
                "output_resolution",
                "projection",
                "distance",
                "screen_width",
                "curvature",
                "show_fps",
                "control_profile",
                "right_click_enabled",
                "divergence",
                "convergence",
                "depth_model",
                "method",
                "depth_resolution",
                "capture_backend",
            )
            if dpg.does_item_exist(tag)
        }
        values.update(
            {
                "language": self.language,
                "profile": self.selected_id("profile", PROFILE_KEYS, "balanced"),
                "source_type": self.selected_id("source_type", SOURCE_KEYS, "screen"),
                "projection": self.selected_id(
                    "projection", PROJECTION_KEYS, "curved"
                ),
                "control_profile": self.selected_id(
                    "control_profile", CONTROL_KEYS, "desktop"
                ),
            }
        )
        os.makedirs(path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as config_file:
            json.dump(values, config_file, indent=2, ensure_ascii=False)

    def value(self, name, default):
        return self.config.get(name, default)

    def choice_value(self, name, choices, default):
        value = self.value(name, default)
        return value if value in choices else default

    def t(self, key, **values):
        return translate(self.language, key, **values)

    def labels(self, keys):
        return [self.t(key) for key in keys.values()]

    def label_for(self, value, keys, default):
        if value not in keys:
            value = default
        return self.t(keys[value])

    def selected_id(self, tag, keys, default):
        if not dpg.does_item_exist(tag):
            return default
        selected = dpg.get_value(tag)
        for value, key in keys.items():
            if selected == self.t(key):
                return value
        legacy = {
            "Fluide": "fluid",
            "Équilibré": "balanced",
            "Meilleure qualité": "quality",
            "Écran complet": "screen",
            "Fenêtre": "window",
            "Incurvé": "curved",
            "Plat": "flat",
            "Bureau": "desktop",
            "Cinéma": "cinema",
        }
        return legacy.get(selected, selected if selected in keys else default)

    def build(self):
        dpg.create_context()
        self.load_unicode_font()
        with dpg.theme(tag="stop_button_theme"):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, (180, 45, 45))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (220, 60, 60))
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (140, 30, 30))
        with dpg.texture_registry(show=False):
            dpg.add_dynamic_texture(
                PREVIEW_WIDTH,
                PREVIEW_HEIGHT,
                [0.06, 0.07, 0.08, 1.0] * (PREVIEW_WIDTH * PREVIEW_HEIGHT),
                tag="source_preview_texture",
            )
        dpg.create_viewport(title=APP_NAME, width=780, height=760, min_width=650)
        icon_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "assets", "depthvista-xr.ico"
        )
        dpg.set_viewport_small_icon(icon_path)
        dpg.set_viewport_large_icon(icon_path)

        with dpg.window(tag="main_window", label=APP_NAME):
            dpg.add_text(self.t("tagline"), tag="tagline")
            dpg.add_combo(
                list(LANGUAGES.values()),
                default_value=LANGUAGES[self.language],
                tag="language",
                label=self.t("language"),
                callback=self.change_language,
                width=220,
            )
            dpg.add_separator()
            with dpg.tab_bar(tag="setup_tabs"):
                with dpg.tab(label=self.t("general"), tag="tab_general"):
                    profile_id = self.value("profile", "balanced")
                    dpg.add_combo(
                        self.labels(PROFILE_KEYS),
                        default_value=self.label_for(
                            profile_id, PROFILE_KEYS, "balanced"
                        ),
                        tag="profile",
                        label=self.t("profile"),
                        callback=self.apply_profile,
                        width=300,
                    )
                    source_type = self.value("source_type", "screen")
                    dpg.add_combo(
                        self.labels(SOURCE_KEYS),
                        default_value=self.label_for(
                            source_type, SOURCE_KEYS, "screen"
                        ),
                        tag="source_type",
                        label=self.t("source"),
                        callback=self.refresh_sources,
                        width=300,
                    )
                    dpg.add_combo(
                        [],
                        tag="source",
                        label=self.t("source_picker"),
                        width=520,
                        callback=self.request_preview,
                    )
                    with dpg.group(horizontal=True):
                        dpg.add_button(
                            label=self.t("refresh_sources"),
                            tag="refresh_sources",
                            callback=self.refresh_sources,
                        )
                        dpg.add_button(
                            label=self.t("refresh_preview"),
                            tag="refresh_preview",
                            callback=self.request_preview,
                        )
                    dpg.add_image(
                        "source_preview_texture",
                        width=PREVIEW_WIDTH,
                        height=PREVIEW_HEIGHT,
                    )
                    dpg.add_text(self.t("preview_selected"), tag="preview_status")
                    dpg.add_combo(
                        ["720", "900", "1080"],
                        default_value=str(self.value("output_resolution", "1080")),
                        tag="output_resolution",
                        label=self.t("generated_resolution"),
                        width=180,
                    )
                    dpg.add_combo(
                        self.capture_backends(),
                        default_value=self.value(
                            "capture_backend", self.default_capture_backend()
                        ),
                        tag="capture_backend",
                        label=self.t("capture"),
                        width=250,
                    )
                    with dpg.group(horizontal=True):
                        dpg.add_input_float(
                            tag="divergence",
                            default_value=float(self.value("divergence", 1.0)),
                            min_value=0.0,
                            max_value=10.0,
                            step=0.1,
                            width=90,
                            callback=self.divergence_changed,
                        )
                        dpg.add_text(self.t("depth_strength"), tag="depth_strength")
                        for preset in (1.0, 1.5, 2.0):
                            dpg.add_button(
                                label=f"{preset:g}",
                                callback=self.set_divergence,
                                user_data=preset,
                            )

                with dpg.tab(label="OpenXR", tag="tab_openxr"):
                    projection = self.value("projection", "curved")
                    dpg.add_combo(
                        self.labels(PROJECTION_KEYS),
                        default_value=self.label_for(
                            projection, PROJECTION_KEYS, "curved"
                        ),
                        tag="projection",
                        label=self.t("projection"),
                        width=220,
                        callback=self.update_openxr_settings,
                    )
                    dpg.add_slider_float(
                        label=self.t("distance"),
                        tag="distance",
                        default_value=float(self.value("distance", 2.0)),
                        min_value=0.5,
                        max_value=10.0,
                        format="%.1f",
                        callback=self.update_openxr_settings,
                    )
                    dpg.add_slider_float(
                        label=self.t("screen_width"),
                        tag="screen_width",
                        default_value=float(self.value("screen_width", 3.0)),
                        min_value=0.5,
                        max_value=10.0,
                        format="%.1f",
                        callback=self.update_openxr_settings,
                    )
                    dpg.add_slider_int(
                        label=self.t("curvature"),
                        tag="curvature",
                        default_value=int(self.value("curvature", 30)),
                        min_value=0,
                        max_value=120,
                        callback=self.update_openxr_settings,
                    )
                    dpg.add_checkbox(
                        label=self.t("show_fps"),
                        tag="show_fps",
                        default_value=bool(self.value("show_fps", False)),
                        callback=self.update_openxr_settings,
                    )

                with dpg.tab(label=self.t("controllers"), tag="tab_controllers"):
                    control_profile = self.value("control_profile", "desktop")
                    dpg.add_combo(
                        self.labels(CONTROL_KEYS),
                        default_value=self.label_for(
                            control_profile, CONTROL_KEYS, "desktop"
                        ),
                        tag="control_profile",
                        label=self.t("configuration"),
                        width=330,
                        callback=self.control_profile_changed,
                    )
                    dpg.add_checkbox(
                        label=self.t("right_click"),
                        tag="right_click_enabled",
                        default_value=bool(self.value("right_click_enabled", True)),
                        callback=self.update_openxr_settings,
                    )
                    dpg.add_spacer(height=8)
                    dpg.add_text(self.t("desktop_help"), tag="desktop_help")
                    dpg.add_text(self.t("cinema_help"), tag="cinema_help")
                    dpg.add_text(self.t("buttons_help"), tag="buttons_help")
                    dpg.add_text(self.t("stick_help"), tag="stick_help")

                with dpg.tab(label=self.t("advanced"), tag="tab_advanced"):
                    dpg.add_input_float(
                        label=self.t("convergence"),
                        tag="convergence",
                        default_value=float(self.value("convergence", 0.5)),
                        min_value=-10.0,
                        max_value=10.0,
                        width=180,
                    )
                    dpg.add_combo(
                        ["Distill_Any_S", "Any_V2_S", "VDA_Stream_S", "VDA_Stream_Metric_S"],
                        default_value=self.value("depth_model", "VDA_Stream_S"),
                        tag="depth_model",
                        label=self.t("depth_model"),
                        width=270,
                    )
                    dpg.add_combo(
                        ["mlbw_l2s", "mlbw_l2", "mlbw_l4", "row_flow_v3", "row_flow_v3_sym", "row_flow_v2", "forward_fill"],
                        default_value=self.value("method", "row_flow_v3"),
                        tag="method",
                        label=self.t("method_3d"),
                        width=270,
                    )
                    dpg.add_input_int(
                        label=self.t("depth_resolution"),
                        tag="depth_resolution",
                        default_value=int(self.value("depth_resolution", 512)),
                        min_value=192,
                        max_value=8190,
                        width=180,
                    )

                with dpg.tab(label=self.t("state"), tag="tab_state"):
                    dpg.add_text(self.t("stopped"), tag="status")
                    dpg.add_text(self.t("fps_empty"), tag="fps_status")
                    dpg.add_input_text(
                        tag="log",
                        multiline=True,
                        readonly=True,
                        width=-1,
                        height=330,
                    )

            dpg.add_separator(tag="setup_separator")
            with dpg.group(horizontal=True, tag="setup_controls"):
                dpg.add_button(
                    label=self.t("start_openxr"), tag="start", callback=self.start
                )

            with dpg.group(tag="active_interface", show=False):
                dpg.add_text(
                    self.t("active_session"),
                    color=(80, 220, 120),
                    tag="active_title",
                )
                dpg.add_text(self.t("starting"), tag="active_status")
                dpg.add_text(self.t("fps_empty"), tag="active_fps")
                dpg.add_separator()
                dpg.add_combo(
                    self.labels(PROJECTION_KEYS),
                    tag="active_projection",
                    label=self.t("projection"),
                    callback=self.active_setting_changed,
                    width=220,
                )
                dpg.add_slider_float(
                    label=self.t("distance"),
                    tag="active_distance",
                    min_value=0.5,
                    max_value=10.0,
                    format="%.1f",
                    callback=self.active_setting_changed,
                )
                dpg.add_slider_float(
                    label=self.t("screen_width"),
                    tag="active_screen_width",
                    min_value=0.5,
                    max_value=10.0,
                    format="%.1f",
                    callback=self.active_setting_changed,
                )
                dpg.add_slider_int(
                    label=self.t("curvature"),
                    tag="active_curvature",
                    min_value=0,
                    max_value=120,
                    callback=self.active_setting_changed,
                )
                with dpg.group(horizontal=True):
                    dpg.add_input_float(
                        tag="active_divergence",
                        min_value=0.0,
                        max_value=10.0,
                        step=0.1,
                        width=90,
                        callback=self.active_divergence_changed,
                    )
                    dpg.add_text(
                        self.t("depth_strength"), tag="active_depth_strength"
                    )
                    for preset in (1.0, 1.5, 2.0):
                        dpg.add_button(
                            label=f"{preset:g}",
                            callback=self.set_divergence,
                            user_data=preset,
                        )
                dpg.add_combo(
                    self.labels(CONTROL_KEYS),
                    tag="active_control_profile",
                    label=self.t("controller_configuration"),
                    width=260,
                    callback=self.active_setting_changed,
                )
                dpg.add_checkbox(
                    label=self.t("right_click"),
                    tag="active_right_click",
                    callback=self.active_setting_changed,
                )
                dpg.add_checkbox(
                    label=self.t("show_fps"),
                    tag="active_show_fps",
                    callback=self.active_setting_changed,
                )
                dpg.add_spacer(height=12)
                dpg.add_button(
                    label=self.t("stop_openxr"),
                    tag="stop",
                    callback=self.stop,
                    width=300,
                    height=55,
                )
                dpg.bind_item_theme("stop", "stop_button_theme")

        self.refresh_sources()
        self.control_profile_changed()
        self.start_preview_thread()
        dpg.set_primary_window("main_window", True)
        dpg.setup_dearpygui()
        dpg.show_viewport()

    def load_unicode_font(self):
        font_path = r"C:\Windows\Fonts\msyh.ttc"
        if not path.isfile(font_path):
            font_path = r"C:\Windows\Fonts\segoeui.ttf"
        if not path.isfile(font_path):
            return
        with dpg.font_registry():
            dpg.add_font(font_path, 18, tag="unicode_font")
        dpg.bind_font("unicode_font")

    def change_language(self, _sender, selected_language):
        profile = self.selected_id("profile", PROFILE_KEYS, "balanced")
        source_type = self.selected_id("source_type", SOURCE_KEYS, "screen")
        projection = self.selected_id("projection", PROJECTION_KEYS, "curved")
        control = self.selected_id("control_profile", CONTROL_KEYS, "desktop")
        self.language = next(
            (
                code
                for code, language_name in LANGUAGES.items()
                if language_name == selected_language
            ),
            "en",
        )
        self.config["language"] = self.language
        self.update_translations(profile, source_type, projection, control)
        self.refresh_sources()
        self._save_config()

    def update_translations(self, profile, source_type, projection, control):
        labels = {
            "language": "language",
            "profile": "profile",
            "source_type": "source",
            "source": "source_picker",
            "output_resolution": "generated_resolution",
            "capture_backend": "capture",
            "projection": "projection",
            "distance": "distance",
            "screen_width": "screen_width",
            "curvature": "curvature",
            "show_fps": "show_fps",
            "control_profile": "configuration",
            "right_click_enabled": "right_click",
            "convergence": "convergence",
            "depth_model": "depth_model",
            "method": "method_3d",
            "depth_resolution": "depth_resolution",
            "active_projection": "projection",
            "active_distance": "distance",
            "active_screen_width": "screen_width",
            "active_curvature": "curvature",
            "active_control_profile": "controller_configuration",
            "active_right_click": "right_click",
            "active_show_fps": "show_fps",
        }
        for tag, key in labels.items():
            dpg.configure_item(tag, label=self.t(key))
        tab_labels = {
            "tab_general": "general",
            "tab_controllers": "controllers",
            "tab_advanced": "advanced",
            "tab_state": "state",
        }
        for tag, key in tab_labels.items():
            dpg.configure_item(tag, label=self.t(key))
        button_labels = {
            "refresh_sources": "refresh_sources",
            "refresh_preview": "refresh_preview",
            "start": "start_openxr",
            "stop": "stop_openxr",
        }
        for tag, key in button_labels.items():
            dpg.configure_item(tag, label=self.t(key))
        text_values = {
            "tagline": "tagline",
            "preview_status": "preview_selected",
            "depth_strength": "depth_strength",
            "desktop_help": "desktop_help",
            "cinema_help": "cinema_help",
            "buttons_help": "buttons_help",
            "stick_help": "stick_help",
            "active_title": "active_session",
            "active_depth_strength": "depth_strength",
        }
        for tag, key in text_values.items():
            dpg.set_value(tag, self.t(key))
        dpg.configure_item("profile", items=self.labels(PROFILE_KEYS))
        dpg.set_value("profile", self.label_for(profile, PROFILE_KEYS, "balanced"))
        dpg.configure_item("source_type", items=self.labels(SOURCE_KEYS))
        dpg.set_value(
            "source_type", self.label_for(source_type, SOURCE_KEYS, "screen")
        )
        for tag in ("projection", "active_projection"):
            dpg.configure_item(tag, items=self.labels(PROJECTION_KEYS))
            dpg.set_value(
                tag, self.label_for(projection, PROJECTION_KEYS, "curved")
            )
        for tag in ("control_profile", "active_control_profile"):
            dpg.configure_item(tag, items=self.labels(CONTROL_KEYS))
            dpg.set_value(tag, self.label_for(control, CONTROL_KEYS, "desktop"))
        if not self.running:
            dpg.set_value("status", self.t("stopped"))
            dpg.set_value("fps_status", self.t("fps_empty"))
        self.control_profile_changed()

    def capture_backends(self):
        backends = ["pil", "mss"]
        if HAS_WINDOWS_CAPTURE:
            backends.append("wc_mp")
        if HAS_WINDOWS_CAPTURE_CUDA:
            backends.append("wc_cuda")
        return backends

    def default_capture_backend(self):
        if HAS_WINDOWS_CAPTURE_CUDA:
            return "wc_cuda"
        if HAS_WINDOWS_CAPTURE:
            return "wc_mp"
        return "mss"

    def refresh_sources(self, *_args):
        if not dpg.does_item_exist("source"):
            return
        if self.selected_id("source_type", SOURCE_KEYS, "screen") == "window":
            items = enum_window_names()
        else:
            items = [
                self.t(
                    "screen_item", index=index + 1, width=width, height=height
                )
                for index, (width, height) in enumerate(get_monitor_size_list())
            ]
        dpg.configure_item("source", items=items)
        previous = self.value("source", "")
        dpg.set_value("source", previous if previous in items else (items[0] if items else ""))
        self.request_preview()

    def request_preview(self, *_args):
        if not self.running:
            self.preview_selection = (
                self.selected_id("source_type", SOURCE_KEYS, "screen"),
                dpg.get_value("source"),
                list(dpg.get_item_configuration("source")["items"]),
            )
            self.preview_requested.set()

    def start_preview_thread(self):
        if self.preview_thread is not None:
            return
        self.preview_thread = threading.Thread(
            target=self.preview_loop,
            name="depthvista-preview",
            daemon=True,
        )
        self.preview_thread.start()
        self.preview_requested.set()

    def preview_loop(self):
        while not self.preview_stop.is_set():
            self.preview_requested.wait(timeout=2.0)
            self.preview_requested.clear()
            if self.preview_stop.is_set() or self.running:
                continue
            try:
                image = self.capture_preview()
                self.events.put(("preview", self.preview_to_texture(image)))
            except Exception as error:
                image = Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), "#20242a")
                draw = ImageDraw.Draw(image)
                draw.text(
                    (14, 14),
                    self.t("preview_error", error=error),
                    fill="#f0f0f0",
                )
                self.events.put(("preview_error", self.preview_to_texture(image)))

    def capture_preview(self):
        if self.preview_selection is None:
            raise RuntimeError(self.t("no_source"))
        source_type, source, items = self.preview_selection
        if source_type == "window":
            rect = get_window_rect_by_title(source)
            if rect is None:
                raise RuntimeError(self.t("window_missing"))
            bbox = (
                rect["left"],
                rect["top"],
                rect["left"] + rect["width"],
                rect["top"] + rect["height"],
            )
        else:
            monitor_index = next(
                (index for index, item in enumerate(items) if item == source),
                0,
            )
            import win32api

            bbox = win32api.EnumDisplayMonitors()[monitor_index][2]
        return ImageGrab.grab(
            bbox=bbox,
            include_layered_windows=True,
            all_screens=True,
        )

    @staticmethod
    def preview_to_texture(image):
        image = image.convert("RGB")
        image.thumbnail((PREVIEW_WIDTH, PREVIEW_HEIGHT), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), "#101215")
        offset = (
            (PREVIEW_WIDTH - image.width) // 2,
            (PREVIEW_HEIGHT - image.height) // 2,
        )
        canvas.paste(image, offset)
        rgba = np.asarray(canvas.convert("RGBA"), dtype=np.float32) / 255.0
        return rgba.ravel().tolist()

    def apply_profile(self, *_args):
        profile = PROFILES[self.selected_id("profile", PROFILE_KEYS, "balanced")]
        dpg.set_value("depth_model", profile["depth_model"])
        dpg.set_value("method", profile["method"])
        dpg.set_value("depth_resolution", profile["depth_resolution"])

    def set_divergence(self, _sender, _app_data, value):
        dpg.set_value("divergence", float(value))
        if dpg.does_item_exist("active_divergence"):
            dpg.set_value("active_divergence", float(value))
        self.divergence_changed()

    def adjust_divergence(self, _sender, _app_data, delta):
        source = "active_divergence" if self.running else "divergence"
        value = max(0.0, min(10.0, float(dpg.get_value(source)) + float(delta)))
        self.set_divergence(None, None, round(value, 2))

    def divergence_changed(self, *_args):
        value = float(dpg.get_value("divergence"))
        if dpg.does_item_exist("active_divergence"):
            dpg.set_value("active_divergence", value)
        if self.current_args is not None:
            with self.args_lock:
                self.current_args.divergence = value

    def active_divergence_changed(self, *_args):
        dpg.set_value("divergence", float(dpg.get_value("active_divergence")))
        self.divergence_changed()

    def control_profile_changed(self, *_args):
        desktop = (
            self.selected_id("control_profile", CONTROL_KEYS, "desktop")
            == "desktop"
        )
        if dpg.does_item_exist("right_click_enabled"):
            dpg.configure_item("right_click_enabled", show=desktop)
        self.update_openxr_settings()

    def sync_active_interface(self):
        values = {
            "active_projection": dpg.get_value("projection"),
            "active_distance": dpg.get_value("distance"),
            "active_screen_width": dpg.get_value("screen_width"),
            "active_curvature": dpg.get_value("curvature"),
            "active_divergence": dpg.get_value("divergence"),
            "active_control_profile": dpg.get_value("control_profile"),
            "active_right_click": dpg.get_value("right_click_enabled"),
            "active_show_fps": dpg.get_value("show_fps"),
        }
        for tag, value in values.items():
            dpg.set_value(tag, value)
        desktop = (
            self.selected_id("active_control_profile", CONTROL_KEYS, "desktop")
            == "desktop"
        )
        dpg.configure_item("active_right_click", show=desktop)

    def set_session_interface(self, active):
        dpg.configure_item("setup_tabs", show=not active)
        dpg.configure_item("setup_separator", show=not active)
        dpg.configure_item("setup_controls", show=not active)
        dpg.configure_item("active_interface", show=active)
        if active:
            self.sync_active_interface()

    def active_setting_changed(self, *_args):
        dpg.set_value("projection", dpg.get_value("active_projection"))
        dpg.set_value("distance", dpg.get_value("active_distance"))
        dpg.set_value("screen_width", dpg.get_value("active_screen_width"))
        dpg.set_value("curvature", dpg.get_value("active_curvature"))
        dpg.set_value("control_profile", dpg.get_value("active_control_profile"))
        dpg.set_value("right_click_enabled", dpg.get_value("active_right_click"))
        dpg.set_value("show_fps", dpg.get_value("active_show_fps"))
        desktop = (
            self.selected_id("active_control_profile", CONTROL_KEYS, "desktop")
            == "desktop"
        )
        dpg.configure_item("active_right_click", show=desktop)
        dpg.configure_item("right_click_enabled", show=desktop)
        self.update_openxr_settings()

    def build_args(self):
        parser = create_parser()
        args = parser.parse_args([])
        source_type = self.selected_id("source_type", SOURCE_KEYS, "screen")
        source = dpg.get_value("source")
        if source_type == "window":
            args.window_name = source or None
            args.monitor_index = 0
        else:
            args.window_name = None
            args.monitor_index = max(
                0,
                next(
                    (
                        index
                        for index, item in enumerate(dpg.get_item_configuration("source")["items"])
                        if item == source
                    ),
                    0,
                ),
            )

        args.openxr = True
        args.local_viewer = False
        args.openxr_projection = self.selected_id(
            "projection", PROJECTION_KEYS, "curved"
        )
        args.openxr_distance = float(dpg.get_value("distance"))
        args.openxr_width = float(dpg.get_value("screen_width"))
        args.openxr_height = 0.0
        args.openxr_curvature = float(dpg.get_value("curvature"))
        args.openxr_show_fps = bool(dpg.get_value("show_fps"))
        args.openxr_control_profile = self.selected_id(
            "control_profile", CONTROL_KEYS, "desktop"
        )
        args.openxr_pointer = args.openxr_control_profile == "desktop"
        args.openxr_right_click = bool(dpg.get_value("right_click_enabled"))
        args.stream_height = int(dpg.get_value("output_resolution"))
        profile = PROFILES[self.selected_id("profile", PROFILE_KEYS, "balanced")]
        args.stream_fps = profile["fps"]
        args.depth_model = dpg.get_value("depth_model")
        args.method = dpg.get_value("method")
        args.resolution = int(dpg.get_value("depth_resolution"))
        args.divergence = float(dpg.get_value("divergence"))
        args.convergence = float(dpg.get_value("convergence"))
        args.convergence_mode = "constant"
        args.synthetic_view = "both"
        args.screenshot = dpg.get_value("capture_backend")
        args.gpu = [0] if torch.cuda.is_available() else [-1]
        args.edge_dilation = [2, 0]
        args.preserve_screen_border = True
        args.foreground_scale = 0.0
        args.ema_normalize = True
        args.ema_decay = 0.9
        args.autocrop = None
        args.compile = False
        args.crop_top = args.crop_left = args.crop_right = args.crop_bottom = 0
        args.disable_draw_cursor = False
        args.pad_mode = None
        args.full_sbs = True
        args.half_sbs = False
        set_state_args(
            args,
            args_lock=self.args_lock,
            stop_event=self.stop_event,
            fps_event=EventBridge(self.events),
            depth_model=self.depth_model,
        )
        args.state["openxr_status_callback"] = (
            lambda state, detail="": self.events.put(("status", (state, detail)))
        )
        return args

    def start(self):
        if self.running:
            return
        self._save_config()
        self.stop_event.clear()
        try:
            args = self.build_args()
        except Exception:
            self.append_log(traceback.format_exc())
            return
        self.running = True
        self.current_args = args
        dpg.set_value("status", self.t("starting"))
        dpg.set_value("active_status", self.t("starting"))
        self.set_session_interface(True)

        def worker():
            try:
                result = iw3_desktop_main(args, init_wxapp=False)
                self.depth_model = result.state["depth_model"]
            except Exception:
                self.events.put(("error", traceback.format_exc()))
            finally:
                self.events.put(("stopped", None))

        self.worker = threading.Thread(target=worker, name="depthvista-worker", daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        dpg.set_value("status", self.t("stopping"))
        dpg.set_value("active_status", self.t("stopping"))

    def append_log(self, message):
        current = dpg.get_value("log")
        dpg.set_value("log", (current + message + "\n")[-20000:])

    def poll_events(self):
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "fps":
                fps_text = self.t(
                    "fps",
                    output=payload["output"],
                    estimated=payload["estimated"],
                    capture=payload["capture"],
                )
                dpg.set_value("fps_status", fps_text)
                dpg.set_value("active_fps", fps_text)
            elif event == "status":
                state, detail = payload
                dpg.set_value("status", state)
                dpg.set_value("active_status", state)
                if state == "Settings":
                    values = dict(
                        item.split("=", 1)
                        for item in detail.split(";")
                        if "=" in item
                    )
                    if "distance" in values:
                        dpg.set_value("distance", float(values["distance"]))
                        dpg.set_value("active_distance", float(values["distance"]))
                    if "width" in values:
                        dpg.set_value("screen_width", float(values["width"]))
                        dpg.set_value("active_screen_width", float(values["width"]))
                    if "curvature" in values:
                        dpg.set_value("curvature", int(float(values["curvature"])))
                        dpg.set_value(
                            "active_curvature", int(float(values["curvature"]))
                        )
                if detail:
                    self.append_log(f"{state}: {detail}")
            elif event == "error":
                self.append_log(payload)
                dpg.set_value("status", self.t("error"))
            elif event == "stopped":
                self.running = False
                self.current_args = None
                dpg.set_value("status", self.t("stopped"))
                self.set_session_interface(False)
                self.request_preview()
            elif event in {"preview", "preview_error"}:
                dpg.set_value("source_preview_texture", payload)
                dpg.set_value(
                    "preview_status",
                    self.t("preview_updated")
                    if event == "preview"
                    else self.t("preview_unavailable"),
                )

    def update_openxr_settings(self, *_args):
        if self.current_args is None:
            return
        output = self.current_args.state.get("openxr_output")
        if output is None:
            return
        output.update_settings(
            projection=self.selected_id(
                "projection", PROJECTION_KEYS, "curved"
            ),
            distance=float(dpg.get_value("distance")),
            width=float(dpg.get_value("screen_width")),
            curvature=float(dpg.get_value("curvature")),
            show_fps=bool(dpg.get_value("show_fps")),
            control_profile=self.selected_id(
                "control_profile", CONTROL_KEYS, "desktop"
            ),
            pointer_enabled=(
                self.selected_id(
                    "control_profile", CONTROL_KEYS, "desktop"
                )
                == "desktop"
            ),
            right_click_enabled=bool(dpg.get_value("right_click_enabled")),
        )

    def run(self):
        self.build()
        while dpg.is_dearpygui_running():
            self.poll_events()
            dpg.render_dearpygui_frame()
        self.stop_event.set()
        self.preview_stop.set()
        self.preview_requested.set()
        self._save_config()
        dpg.destroy_context()


def main():
    DepthVistaApp().run()


if __name__ == "__main__":
    main()
