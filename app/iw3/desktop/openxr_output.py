import ctypes
import json
import math
from os import path
import threading
import time
import traceback

import numpy as np
import torch

try:
    import winreg
except ImportError:
    winreg = None


VK_SPACE = 0x20
VK_LEFT = 0x25
VK_RIGHT = 0x27
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010


def detect_openxr_runtime():
    result = {
        "available": False,
        "runtime": "Not detected",
        "manifest": None,
    }
    if winreg is None:
        return result
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Khronos\OpenXR\1"
        ) as key:
            manifest, _ = winreg.QueryValueEx(key, "ActiveRuntime")
        result["manifest"] = manifest
        result["runtime"] = path.splitext(path.basename(manifest))[0]
        if path.isfile(manifest):
            with open(manifest, "r", encoding="utf-8") as manifest_file:
                manifest_data = json.load(manifest_file)
            result["runtime"] = manifest_data.get("runtime", {}).get(
                "name", result["runtime"]
            )
        result["available"] = True
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        pass
    return result


class LatestFrameSlot:
    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None
        self._timestamp = 0.0
        self._version = 0
        self._ready_event = None

    def submit(self, frame, timestamp, ready_event=None):
        with self._lock:
            self._frame = frame
            self._timestamp = timestamp
            self._ready_event = ready_event
            self._version += 1

    def latest(self, previous_version):
        with self._lock:
            if self._version == previous_version or self._frame is None:
                return None
            if self._ready_event is not None and not self._ready_event.query():
                return None
            return self._frame, self._timestamp, self._version


class OpenXROutput:
    def __init__(
        self,
        projection="curved",
        distance=2.0,
        width=3.0,
        height=0.0,
        curvature=30.0,
        show_fps=False,
        control_profile="cinema",
        pointer_enabled=True,
        right_click_enabled=True,
        pointer_rect=None,
        status_callback=None,
    ):
        self.settings_lock = threading.Lock()
        self.settings = {
            "projection": projection,
            "distance": distance,
            "width": width,
            "height": height,
            "curvature": curvature,
            "show_fps": show_fps,
            "control_profile": control_profile,
            "pointer_enabled": pointer_enabled,
            "right_click_enabled": right_click_enabled,
            "pointer_rect": pointer_rect,
        }
        self.status_callback = status_callback
        self.frame_slot = LatestFrameSlot()
        self.stop_event = threading.Event()
        self.enabled_event = threading.Event()
        self.enabled_event.set()
        self.recenter_event = threading.Event()
        self.closed_event = threading.Event()
        self.thread = None
        self.error = None
        self.session_focused = False
        self.frame_count = 0
        self.frame_times = []
        self.generation_times = []

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.closed_event.clear()
        self.thread = threading.Thread(
            target=self._run, name="iw3-openxr", daemon=False
        )
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=4.0)
        self.closed_event.set()

    def set_frame_data(self, frame_data):
        frame, timestamp = frame_data
        with torch.inference_mode():
            prepared = frame.detach()
            if prepared.dtype != torch.uint8:
                prepared = prepared.clamp(0, 1).mul(255).round().to(torch.uint8)
            if prepared.ndim == 3 and prepared.shape[0] == 3:
                prepared = prepared.permute(1, 2, 0).contiguous()
            elif prepared.ndim != 3 or prepared.shape[2] != 3:
                raise ValueError(
                    f"OpenXR expects RGB CHW/HWC tensor, got {tuple(prepared.shape)}"
                )
            if prepared.is_cuda:
                ready_event = torch.cuda.Event()
                ready_event.record(torch.cuda.current_stream(prepared.device))
            else:
                ready_event = None
        self.frame_slot.submit(prepared, timestamp, ready_event=ready_event)
        self.generation_times.append(time.perf_counter())

    def get_fps(self):
        now = time.perf_counter()
        self.frame_times = [
            frame_time for frame_time in self.frame_times if now - frame_time <= 1.0
        ]
        return float(len(self.frame_times))

    def get_generation_fps(self):
        now = time.perf_counter()
        self.generation_times = [
            frame_time
            for frame_time in self.generation_times
            if now - frame_time <= 1.0
        ]
        return float(len(self.generation_times))

    def is_closed(self):
        return self.closed_event.is_set()

    def set_enabled(self, enabled):
        if enabled:
            self.enabled_event.set()
        else:
            self.enabled_event.clear()
        self._status("Enabled" if enabled else "Paused")

    def recenter(self):
        self.recenter_event.set()

    def update_settings(self, **settings):
        with self.settings_lock:
            self.settings.update(settings)

    def get_error(self):
        return self.error

    def _status(self, state, detail=""):
        if self.status_callback is not None:
            self.status_callback(state, detail)

    def _run(self):
        renderer = None
        try:
            self._status("Starting", "Creating OpenXR/WGL context")
            renderer = OpenXRRenderer(self)
            renderer.run()
        except Exception as error:
            self.error = error
            self._status("Error", f"{error}\n{traceback.format_exc()}")
        finally:
            if renderer is not None:
                renderer.close()
            if self.error is None:
                self._status("Stopped", "")
            self.closed_event.set()


def send_virtual_key(virtual_key):
    user32 = ctypes.windll.user32
    user32.keybd_event(virtual_key, 0, 0, 0)
    user32.keybd_event(virtual_key, 0, KEYEVENTF_KEYUP, 0)


def set_mouse_position(x, y):
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def send_mouse_button(button, pressed):
    flags = {
        ("left", True): MOUSEEVENTF_LEFTDOWN,
        ("left", False): MOUSEEVENTF_LEFTUP,
        ("right", True): MOUSEEVENTF_RIGHTDOWN,
        ("right", False): MOUSEEVENTF_RIGHTUP,
    }
    ctypes.windll.user32.mouse_event(flags[(button, pressed)], 0, 0, 0, 0)


class OpenXRRenderer:
    """
    Windows OpenXR/OpenGL renderer adapted from XRPlay's OpenXRDevice and flat
    projector architecture. The XRPlay attribution is retained in licenses/.
    """

    def __init__(self, output):
        self.output = output
        self.xr = None
        self.glfw = None
        self.gl = None
        self.window = None
        self.hwnd = None
        self.hdc = None
        self.instance = None
        self.system_id = None
        self.session = None
        self.space = None
        self.session_running = False
        self.session_focused = False
        self.session_state = None
        self.view_config_type = None
        self.view_configs = None
        self.swapchains = []
        self.swapchain_images = []
        self.frame_state = None
        self.images_acquired = False
        self.acquired_swapchains = []
        self.source_texture = None
        self.source_size = None
        self.upload_pbo = None
        self.upload_pbo_size = 0
        self.cuda_resource = None
        self.cudart = None
        self.cuda_device_id = None
        self.cuda_interop_failed = False
        self.framebuffer = None
        self.program = None
        self.vao = None
        self.vbo = None
        self.ebo = None
        self.index_count = 0
        self.mesh_settings = None
        self.anchor_position = np.zeros(3, dtype=np.float32)
        self.anchor_yaw = 0.0
        self.anchor_pending = True
        self.submission_refs = None
        self.action_set = None
        self.hand_paths = {}
        self.controller_actions = {}
        self.controller_connected = {}
        self.controller_previous = {}
        self.controller_repeat = {}
        self.aim_spaces = {}
        self.pointer_uv = np.zeros(2, dtype=np.float32)
        self.pointer_active = False
        self.pointer_hand = None
        self.trigger_started = {}
        self.grip_used_as_modifier = {}
        self.mouse_buttons_down = set()
        self.screen_positions = None
        self.screen_uvs = None
        self.screen_indices = None

    def run(self):
        self._init_modules()
        self._init_gl()
        self._init_openxr()
        self._init_renderer()
        self.output._status("Ready", self._runtime_description())

        frame_version = -1
        current_frame = None
        while not self.output.stop_event.is_set():
            self.glfw.poll_events()
            if not self._poll_events():
                break
            if not self.session_running:
                time.sleep(0.01)
                continue

            try:
                self.frame_state = self.xr.wait_frame(
                    self.session, self.xr.FrameWaitInfo()
                )
                self.xr.begin_frame(self.session, self.xr.FrameBeginInfo())
            except Exception as error:
                if self._is_session_not_focused_error(error):
                    self.session_focused = False
                    self._status("Session", "Waiting for focus")
                    time.sleep(0.05)
                    continue
                raise
            if self.session_focused:
                self._poll_controller_actions()
            layers = []
            try:
                if self.frame_state.should_render:
                    view_state, views = self.xr.locate_views(
                        self.session,
                        self.xr.ViewLocateInfo(
                            view_configuration_type=self.view_config_type,
                            display_time=self.frame_state.predicted_display_time,
                            space=self.space,
                        ),
                    )
                    if self.anchor_pending or self.output.recenter_event.is_set():
                        self._set_anchor(views)
                        self.anchor_pending = False
                        self.output.recenter_event.clear()

                    latest = self.output.frame_slot.latest(frame_version)
                    if latest is not None:
                        current_frame, _, frame_version = latest
                        self._upload_frame(current_frame)

                    if (
                        self.output.enabled_event.is_set()
                        and self.source_texture is not None
                    ):
                        layers = self._render_views(views)
                        now = time.perf_counter()
                        self.output.frame_times.append(now)
                        self.output.frame_count += 1
            finally:
                self._release_images()
                self.xr.end_frame(
                    self.session,
                    self.xr.FrameEndInfo(
                        display_time=self.frame_state.predicted_display_time,
                        environment_blend_mode=self.xr.EnvironmentBlendMode.OPAQUE,
                        layers=layers,
                    ),
                )
                self.submission_refs = None

    def _init_modules(self):
        import glfw
        import xr
        from OpenGL import GL

        self.glfw = glfw
        self.xr = xr
        self.gl = GL
        self.view_config_type = xr.ViewConfigurationType.PRIMARY_STEREO

    def _init_gl(self):
        if not self.glfw.init():
            raise RuntimeError("GLFW initialization failed")
        self.glfw.window_hint(self.glfw.VISIBLE, self.glfw.FALSE)
        self.glfw.window_hint(self.glfw.CONTEXT_VERSION_MAJOR, 4)
        self.glfw.window_hint(self.glfw.CONTEXT_VERSION_MINOR, 5)
        self.glfw.window_hint(self.glfw.OPENGL_PROFILE, self.glfw.OPENGL_CORE_PROFILE)
        self.window = self.glfw.create_window(64, 64, "IW3 OpenXR", None, None)
        if not self.window:
            raise RuntimeError("OpenGL context creation failed")
        self.glfw.make_context_current(self.window)
        self.glfw.swap_interval(0)

    def _init_openxr(self):
        xr = self.xr
        self.instance = xr.create_instance(
            xr.InstanceCreateInfo(
                application_info=xr.ApplicationInfo(
                    application_name="IW3 Desktop OpenXR",
                    application_version=1,
                    engine_name="XRPlay OpenGL Adapter",
                    engine_version=1,
                    api_version=xr.XR_CURRENT_API_VERSION,
                ),
                enabled_extension_names=[xr.KHR_OPENGL_ENABLE_EXTENSION_NAME],
            )
        )
        self.system_id = xr.get_system(
            self.instance,
            xr.SystemGetInfo(form_factor=xr.FormFactor.HEAD_MOUNTED_DISPLAY),
        )
        self.view_configs = xr.enumerate_view_configuration_views(
            self.instance,
            self.system_id,
            self.view_config_type,
        )

        proc_addr = xr.get_instance_proc_addr(
            self.instance, "xrGetOpenGLGraphicsRequirementsKHR"
        )
        graphics_requirements = xr.GraphicsRequirementsOpenGLKHR()
        requirements_function = ctypes.cast(
            proc_addr,
            ctypes.CFUNCTYPE(
                ctypes.c_int,
                xr.Instance,
                xr.SystemId,
                ctypes.POINTER(xr.GraphicsRequirementsOpenGLKHR),
            ),
        )
        xr.check_result(
            requirements_function(
                self.instance,
                self.system_id,
                ctypes.byref(graphics_requirements),
            )
        )

        self.hwnd = self.glfw.get_win32_window(self.window)
        ctypes.windll.user32.GetDC.argtypes = [ctypes.c_void_p]
        ctypes.windll.user32.GetDC.restype = ctypes.c_void_p
        ctypes.windll.user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        ctypes.windll.user32.ReleaseDC.restype = ctypes.c_int
        ctypes.windll.opengl32.wglGetCurrentContext.restype = ctypes.c_void_p
        self.hdc = ctypes.windll.user32.GetDC(self.hwnd)
        hglrc = ctypes.windll.opengl32.wglGetCurrentContext()
        if not self.hdc or not hglrc:
            raise RuntimeError("Unable to obtain current WGL handles")
        graphics_binding = xr.GraphicsBindingOpenGLWin32KHR(h_dc=self.hdc, h_glrc=hglrc)
        self.session = xr.create_session(
            self.instance,
            xr.SessionCreateInfo(
                system_id=self.system_id,
                next=ctypes.cast(ctypes.pointer(graphics_binding), ctypes.c_void_p),
            ),
        )
        self.space = xr.create_reference_space(
            self.session,
            xr.ReferenceSpaceCreateInfo(
                reference_space_type=xr.ReferenceSpaceType.LOCAL,
                pose_in_reference_space=xr.Posef(),
            ),
        )
        self._init_controller_actions()

        formats = xr.enumerate_swapchain_formats(self.session)
        preferred_formats = [self.gl.GL_SRGB8_ALPHA8, self.gl.GL_RGBA8]
        swapchain_format = next(
            (fmt for fmt in preferred_formats if fmt in formats), None
        )
        if swapchain_format is None:
            raise RuntimeError(f"No compatible OpenGL swapchain format: {formats}")

        for view_config in self.view_configs:
            swapchain = xr.create_swapchain(
                self.session,
                xr.SwapchainCreateInfo(
                    usage_flags=xr.SwapchainUsageFlags.COLOR_ATTACHMENT_BIT,
                    format=swapchain_format,
                    sample_count=1,
                    width=view_config.recommended_image_rect_width,
                    height=view_config.recommended_image_rect_height,
                    face_count=1,
                    array_size=1,
                    mip_count=1,
                ),
            )
            images = xr.enumerate_swapchain_images(
                swapchain, xr.SwapchainImageOpenGLKHR
            )
            self.swapchains.append(swapchain)
            self.swapchain_images.append([image.image for image in images])

    def _init_renderer(self):
        gl = self.gl
        vertex_shader = self._compile_shader(
            gl.GL_VERTEX_SHADER,
            """
            #version 450 core
            layout(location = 0) in vec3 in_position;
            layout(location = 1) in vec2 in_uv;
            uniform mat4 mvp;
            out vec2 uv;
            void main() {
                uv = in_uv;
                gl_Position = mvp * vec4(in_position, 1.0);
            }
        """,
        )
        fragment_shader = self._compile_shader(
            gl.GL_FRAGMENT_SHADER,
            """
            #version 450 core
            in vec2 uv;
            uniform sampler2D source_texture;
            uniform float eye_offset;
            uniform float headset_fps;
            uniform float generation_fps;
            uniform int show_fps;
            uniform int pointer_active;
            uniform vec2 pointer_uv;
            out vec4 out_color;

            float segment(vec2 p, vec2 a, vec2 b) {
                vec2 pa = p - a;
                vec2 ba = b - a;
                float h = clamp(dot(pa, ba) / dot(ba, ba), 0.0, 1.0);
                return 1.0 - smoothstep(0.055, 0.085, length(pa - ba * h));
            }

            float digit(vec2 p, int value) {
                bool a = value != 1 && value != 4;
                bool b = value != 5 && value != 6;
                bool c = value != 2;
                bool d = value != 1 && value != 4 && value != 7;
                bool e = value == 0 || value == 2 || value == 6 || value == 8;
                bool f = value != 1 && value != 2 && value != 3 && value != 7;
                bool g = value != 0 && value != 1 && value != 7;
                float result = 0.0;
                if (a) result = max(result, segment(p, vec2(0.18, 0.90), vec2(0.82, 0.90)));
                if (b) result = max(result, segment(p, vec2(0.84, 0.86), vec2(0.84, 0.54)));
                if (c) result = max(result, segment(p, vec2(0.84, 0.46), vec2(0.84, 0.14)));
                if (d) result = max(result, segment(p, vec2(0.18, 0.10), vec2(0.82, 0.10)));
                if (e) result = max(result, segment(p, vec2(0.16, 0.46), vec2(0.16, 0.14)));
                if (f) result = max(result, segment(p, vec2(0.16, 0.86), vec2(0.16, 0.54)));
                if (g) result = max(result, segment(p, vec2(0.18, 0.50), vec2(0.82, 0.50)));
                return result;
            }

            float letter_h(vec2 p) {
                return max(
                    max(segment(p, vec2(0.16, 0.90), vec2(0.16, 0.10)),
                        segment(p, vec2(0.84, 0.90), vec2(0.84, 0.10))),
                    segment(p, vec2(0.18, 0.50), vec2(0.82, 0.50)));
            }

            float letter_g(vec2 p) {
                float result = segment(p, vec2(0.82, 0.90), vec2(0.18, 0.90));
                result = max(result, segment(p, vec2(0.16, 0.86), vec2(0.16, 0.14)));
                result = max(result, segment(p, vec2(0.18, 0.10), vec2(0.82, 0.10)));
                result = max(result, segment(p, vec2(0.84, 0.46), vec2(0.84, 0.14)));
                result = max(result, segment(p, vec2(0.50, 0.50), vec2(0.84, 0.50)));
                return result;
            }

            float number_mask(vec2 p, int value) {
                float result = 0.0;
                for (int index = 0; index < 3; index++) {
                    int divisor = index == 0 ? 100 : (index == 1 ? 10 : 1);
                    int digit_value = (value / divisor) % 10;
                    bool visible = index == 2 || value >= divisor;
                    vec2 digit_uv = vec2(p.x * 3.25 - float(index) * 1.08, p.y);
                    if (visible && digit_uv.x >= 0.0 && digit_uv.x <= 1.0) {
                        result = max(result, digit(digit_uv, digit_value));
                    }
                }
                return result;
            }

            void main() {
                vec2 eye_uv = vec2(uv.x * 0.5 + eye_offset, 1.0 - uv.y);
                vec3 color = texture(source_texture, eye_uv).rgb;
                if (show_fps != 0) {
                    vec2 overlay = vec2((uv.x - 0.805) / 0.175, (uv.y - 0.855) / 0.125);
                    if (overlay.x >= 0.0 && overlay.x <= 1.0 &&
                        overlay.y >= 0.0 && overlay.y <= 1.0) {
                        color = mix(color, vec3(0.0), 0.55);
                        vec2 row_uv = vec2((overlay.x - 0.20) / 0.78, fract(overlay.y * 2.0));
                        vec2 label_uv = vec2(overlay.x / 0.17, fract(overlay.y * 2.0));
                        if (overlay.y >= 0.5) {
                            int fps = int(clamp(round(headset_fps), 0.0, 999.0));
                            float mask = max(letter_h(label_uv), number_mask(row_uv, fps));
                            color = mix(color, vec3(0.2, 1.0, 0.25), mask);
                        } else {
                            int fps = int(clamp(round(generation_fps), 0.0, 999.0));
                            float mask = max(letter_g(label_uv), number_mask(row_uv, fps));
                            color = mix(color, vec3(0.2, 0.8, 1.0), mask);
                        }
                    }
                }
                if (pointer_active != 0) {
                    float pointer_distance = distance(uv, pointer_uv);
                    float outer = 1.0 - smoothstep(0.010, 0.014, pointer_distance);
                    float inner = 1.0 - smoothstep(0.003, 0.006, pointer_distance);
                    color = mix(color, vec3(1.0), outer);
                    color = mix(color, vec3(0.1, 0.6, 1.0), inner);
                }
                out_color = vec4(color, 1.0);
            }
        """,
        )
        self.program = gl.glCreateProgram()
        gl.glAttachShader(self.program, vertex_shader)
        gl.glAttachShader(self.program, fragment_shader)
        gl.glLinkProgram(self.program)
        if not gl.glGetProgramiv(self.program, gl.GL_LINK_STATUS):
            raise RuntimeError(
                gl.glGetProgramInfoLog(self.program).decode("utf-8", errors="replace")
            )
        gl.glDeleteShader(vertex_shader)
        gl.glDeleteShader(fragment_shader)

        self.vao = gl.glGenVertexArrays(1)
        self.vbo = gl.glGenBuffers(1)
        self.ebo = gl.glGenBuffers(1)
        self.framebuffer = gl.glGenFramebuffers(1)
        self.source_texture = gl.glGenTextures(1)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.source_texture)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MIN_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_MAG_FILTER, gl.GL_LINEAR)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_S, gl.GL_CLAMP_TO_EDGE)
        gl.glTexParameteri(gl.GL_TEXTURE_2D, gl.GL_TEXTURE_WRAP_T, gl.GL_CLAMP_TO_EDGE)

    def _compile_shader(self, shader_type, source):
        shader = self.gl.glCreateShader(shader_type)
        self.gl.glShaderSource(shader, source)
        self.gl.glCompileShader(shader)
        if not self.gl.glGetShaderiv(shader, self.gl.GL_COMPILE_STATUS):
            raise RuntimeError(
                self.gl.glGetShaderInfoLog(shader).decode("utf-8", errors="replace")
            )
        return shader

    def _poll_events(self):
        xr = self.xr
        while True:
            try:
                event_buffer = xr.poll_event(self.instance)
            except xr.exception.EventUnavailable:
                return True
            if event_buffer.type == xr.StructureType.EVENT_DATA_SESSION_STATE_CHANGED:
                event = ctypes.cast(
                    ctypes.byref(event_buffer),
                    ctypes.POINTER(xr.EventDataSessionStateChanged),
                ).contents
                self.session_state = event.state
                self.output._status("Session", xr.SessionState(event.state).name)
                if event.state == xr.SessionState.READY:
                    xr.begin_session(
                        self.session,
                        xr.SessionBeginInfo(
                            primary_view_configuration_type=self.view_config_type
                        ),
                    )
                    self.session_running = True
                elif event.state == xr.SessionState.FOCUSED:
                    self.session_focused = True
                elif event.state == xr.SessionState.STOPPING:
                    xr.end_session(self.session)
                    self.session_running = False
                    self.session_focused = False
                elif event.state in {
                    xr.SessionState.EXITING,
                    xr.SessionState.LOSS_PENDING,
                }:
                    return False
            elif event_buffer.type == xr.StructureType.EVENT_DATA_INSTANCE_LOSS_PENDING:
                return False

    def _is_session_not_focused_error(self, error):
        message = str(error).lower()
        return "focused state" in message and "session" in message

    def _init_controller_actions(self):
        xr = self.xr
        self.hand_paths = {
            "left": xr.string_to_path(self.instance, "/user/hand/left"),
            "right": xr.string_to_path(self.instance, "/user/hand/right"),
        }
        subaction_paths = list(self.hand_paths.values())
        self.action_set = xr.create_action_set(
            self.instance,
            xr.ActionSetCreateInfo(
                action_set_name="iw3_controls",
                localized_action_set_name="IW3 Controls",
                priority=0,
            ),
        )
        specs = {
            "stick": xr.ActionType.VECTOR2F_INPUT,
            "pause": xr.ActionType.BOOLEAN_INPUT,
            "stick_click": xr.ActionType.BOOLEAN_INPUT,
            "stop": xr.ActionType.BOOLEAN_INPUT,
            "grip": xr.ActionType.FLOAT_INPUT,
            "trigger": xr.ActionType.FLOAT_INPUT,
            "aim": xr.ActionType.POSE_INPUT,
        }
        for action_name, action_type in specs.items():
            self.controller_actions[action_name] = xr.create_action(
                self.action_set,
                xr.ActionCreateInfo(
                    action_name=action_name,
                    action_type=action_type,
                    subaction_paths=subaction_paths,
                    localized_action_name=action_name.replace("_", " ").title(),
                ),
            )

        profile_path = xr.string_to_path(
            self.instance,
            "/interaction_profiles/oculus/touch_controller",
        )
        bindings = []
        for hand, primary_button, secondary_button in (
            ("right", "a", "b"),
            ("left", "x", "y"),
        ):
            prefix = f"/user/hand/{hand}/input"
            bindings.extend(
                [
                    xr.ActionSuggestedBinding(
                        action=self.controller_actions["stick"],
                        binding=xr.string_to_path(
                            self.instance, f"{prefix}/thumbstick"
                        ),
                    ),
                    xr.ActionSuggestedBinding(
                        action=self.controller_actions["pause"],
                        binding=xr.string_to_path(
                            self.instance, f"{prefix}/{primary_button}/click"
                        ),
                    ),
                    xr.ActionSuggestedBinding(
                        action=self.controller_actions["stick_click"],
                        binding=xr.string_to_path(
                            self.instance, f"{prefix}/thumbstick/click"
                        ),
                    ),
                    xr.ActionSuggestedBinding(
                        action=self.controller_actions["stop"],
                        binding=xr.string_to_path(
                            self.instance, f"{prefix}/{secondary_button}/click"
                        ),
                    ),
                    xr.ActionSuggestedBinding(
                        action=self.controller_actions["grip"],
                        binding=xr.string_to_path(
                            self.instance, f"{prefix}/squeeze/value"
                        ),
                    ),
                    xr.ActionSuggestedBinding(
                        action=self.controller_actions["trigger"],
                        binding=xr.string_to_path(
                            self.instance, f"{prefix}/trigger/value"
                        ),
                    ),
                    xr.ActionSuggestedBinding(
                        action=self.controller_actions["aim"],
                        binding=xr.string_to_path(
                            self.instance, f"{prefix}/aim/pose"
                        ),
                    ),
                ]
            )
        xr.suggest_interaction_profile_bindings(
            self.instance,
            xr.InteractionProfileSuggestedBinding(
                interaction_profile=profile_path,
                suggested_bindings=bindings,
            ),
        )
        xr.attach_session_action_sets(
            self.session,
            xr.SessionActionSetsAttachInfo(action_sets=[self.action_set]),
        )
        self.aim_spaces = {
            hand: xr.create_action_space(
                self.session,
                xr.ActionSpaceCreateInfo(
                    action=self.controller_actions["aim"],
                    subaction_path=hand_path,
                ),
            )
            for hand, hand_path in self.hand_paths.items()
        }

    def _poll_controller_actions(self):
        xr = self.xr
        if self.action_set is None or not self.session_running:
            return
        xr.sync_actions(
            self.session,
            xr.ActionsSyncInfo(
                active_action_sets=[xr.ActiveActionSet(action_set=self.action_set)],
            ),
        )
        now = time.perf_counter()
        for hand, hand_path in self.hand_paths.items():
            get_info = lambda name: xr.ActionStateGetInfo(
                action=self.controller_actions[name],
                subaction_path=hand_path,
            )
            stick = xr.get_action_state_vector2f(self.session, get_info("stick"))
            pause = xr.get_action_state_boolean(self.session, get_info("pause"))
            stick_click = xr.get_action_state_boolean(
                self.session, get_info("stick_click")
            )
            stop = xr.get_action_state_boolean(self.session, get_info("stop"))
            grip = xr.get_action_state_float(self.session, get_info("grip"))
            trigger = xr.get_action_state_float(self.session, get_info("trigger"))
            connected = any(
                (
                    stick.is_active,
                    pause.is_active,
                    stick_click.is_active,
                    stop.is_active,
                    grip.is_active,
                    trigger.is_active,
                )
            )
            if connected != self.controller_connected.get(hand):
                self.controller_connected[hand] = connected
                detected = [
                    name for name, active in self.controller_connected.items() if active
                ]
                self.output._status(
                    "Controllers",
                    (
                        f"Detected: {', '.join(detected)}"
                        if detected
                        else "Waiting for controllers"
                    ),
                )
            if not connected:
                continue

            grip_pressed = grip.is_active and float(grip.current_state) >= 0.65
            profile = self._settings()["control_profile"]
            if profile == "desktop":
                self._update_pointer(hand, trigger, now)
            elif self.pointer_hand == hand:
                self.pointer_active = False
                self._release_pointer_click(hand)

            stick_moved = stick.is_active and (
                abs(stick.current_state.x) > 0.65
                or abs(stick.current_state.y) > 0.65
            )
            if grip_pressed and stick_moved:
                self.grip_used_as_modifier[hand] = True
            if stick.is_active:
                self._repeat_virtual_key(
                    hand,
                    "seek_right",
                    not grip_pressed and stick.current_state.x > 0.65,
                    VK_RIGHT,
                    now,
                )
                self._repeat_virtual_key(
                    hand,
                    "seek_left",
                    not grip_pressed and stick.current_state.x < -0.65,
                    VK_LEFT,
                    now,
                )
                adjustments = (
                    (
                        "distance_up",
                        not grip_pressed and stick.current_state.y > 0.65,
                        "distance",
                        -0.1,
                        0.5,
                        10.0,
                    ),
                    (
                        "distance_down",
                        not grip_pressed and stick.current_state.y < -0.65,
                        "distance",
                        0.1,
                        0.5,
                        10.0,
                    ),
                    (
                        "width_up",
                        grip_pressed and stick.current_state.y > 0.65,
                        "width",
                        0.1,
                        0.5,
                        10.0,
                    ),
                    (
                        "width_down",
                        grip_pressed and stick.current_state.y < -0.65,
                        "width",
                        -0.1,
                        0.5,
                        10.0,
                    ),
                    (
                        "curve_right",
                        grip_pressed and stick.current_state.x > 0.65,
                        "curvature",
                        5.0,
                        0.0,
                        120.0,
                    ),
                    (
                        "curve_left",
                        grip_pressed and stick.current_state.x < -0.65,
                        "curvature",
                        -5.0,
                        0.0,
                        120.0,
                    ),
                )
                for (
                    action_name,
                    active,
                    setting,
                    delta,
                    minimum,
                    maximum,
                ) in adjustments:
                    self._repeat_setting_adjustment(
                        hand, action_name, active, setting, delta, minimum, maximum, now
                    )
            else:
                for action_name in (
                    "seek_right",
                    "seek_left",
                    "distance_up",
                    "distance_down",
                    "width_up",
                    "width_down",
                    "curve_right",
                    "curve_left",
                ):
                    self.controller_previous[f"{hand}:{action_name}"] = False

            pause_pressed = pause.is_active and bool(pause.current_state)
            if profile == "cinema":
                pause_pressed = pause_pressed or (
                    trigger.is_active and float(trigger.current_state) >= 0.65
                )
            pause_key = f"{hand}:pause"
            if pause_pressed and not self.controller_previous.get(pause_key, False):
                send_virtual_key(VK_SPACE)
            self.controller_previous[pause_key] = pause_pressed

            recenter_pressed = stick_click.is_active and bool(stick_click.current_state)
            recenter_key = f"{hand}:recenter"
            if recenter_pressed and not self.controller_previous.get(
                recenter_key, False
            ):
                self.output.recenter()
            self.controller_previous[recenter_key] = recenter_pressed

            grip_key = f"{hand}:grip"
            previous_grip = self.controller_previous.get(grip_key, False)
            if (
                profile == "desktop"
                and previous_grip
                and not grip_pressed
                and self._settings()["right_click_enabled"]
                and not self.grip_used_as_modifier.pop(hand, False)
                and self.pointer_active
            ):
                send_mouse_button("right", True)
                send_mouse_button("right", False)
            elif not grip_pressed:
                self.grip_used_as_modifier.pop(hand, None)
            self.controller_previous[grip_key] = grip_pressed

            stop_pressed = stop.is_active and bool(stop.current_state)
            stop_key = f"{hand}:stop"
            if stop_pressed and not self.controller_previous.get(stop_key, False):
                self.output._status(
                    "Controller", f"{hand.title()} secondary button: stopping OpenXR"
                )
                self.output.stop_event.set()
            self.controller_previous[stop_key] = stop_pressed

    def _update_pointer(self, hand, trigger, now):
        settings = self._settings()
        if not settings["pointer_enabled"]:
            self.pointer_active = False
            self._release_pointer_click(hand)
            return
        try:
            location = self.xr.locate_space(
                space=self.aim_spaces[hand],
                base_space=self.space,
                time=self.frame_state.predicted_display_time,
            )
        except Exception:
            return
        valid_flags = (
            self.xr.SpaceLocationFlags.POSITION_VALID_BIT
            | self.xr.SpaceLocationFlags.ORIENTATION_VALID_BIT
        )
        if location.location_flags & valid_flags != valid_flags:
            return

        origin = np.array(
            (
                location.pose.position.x,
                location.pose.position.y,
                location.pose.position.z,
            ),
            dtype=np.float32,
        )
        orientation = location.pose.orientation
        direction = self._rotate_vector(
            np.array((0.0, 0.0, -1.0), dtype=np.float32),
            np.array(
                (
                    orientation.x,
                    orientation.y,
                    orientation.z,
                    orientation.w,
                ),
                dtype=np.float32,
            ),
        )
        hit = self._intersect_screen(origin, direction)
        if hit is None:
            if self.pointer_hand == hand:
                self.pointer_active = False
                self._release_pointer_click(hand)
            return

        self.pointer_active = True
        self.pointer_hand = hand
        self.pointer_uv[:] = hit
        pointer_rect = settings.get("pointer_rect")
        if pointer_rect is not None:
            left, top, right, bottom = pointer_rect
            set_mouse_position(
                left + hit[0] * max(right - left - 1, 1),
                top + (1.0 - hit[1]) * max(bottom - top - 1, 1),
            )

        trigger_pressed = trigger.is_active and float(trigger.current_state) >= 0.65
        trigger_key = f"{hand}:pointer_trigger"
        previous_trigger = self.controller_previous.get(trigger_key, False)
        if trigger_pressed != previous_trigger:
            send_mouse_button("left", trigger_pressed)
            if trigger_pressed:
                self.mouse_buttons_down.add("left")
            else:
                self.mouse_buttons_down.discard("left")

        self.controller_previous[trigger_key] = trigger_pressed

    def _release_pointer_click(self, hand):
        trigger_key = f"{hand}:pointer_trigger"
        if self.controller_previous.get(trigger_key, False):
            send_mouse_button("left", False)
            self.mouse_buttons_down.discard("left")
        self.controller_previous[trigger_key] = False

    @staticmethod
    def _rotate_vector(vector, quaternion):
        xyz = quaternion[:3]
        w = quaternion[3]
        return (
            vector
            + 2.0 * np.cross(xyz, np.cross(xyz, vector) + w * vector)
        )

    def _intersect_screen(self, origin, direction):
        if self.screen_positions is None:
            return None
        nearest_distance = float("inf")
        nearest_uv = None
        for triangle in self.screen_indices.reshape(-1, 3):
            vertices = self.screen_positions[triangle]
            edge1 = vertices[1] - vertices[0]
            edge2 = vertices[2] - vertices[0]
            pvec = np.cross(direction, edge2)
            determinant = np.dot(edge1, pvec)
            if abs(determinant) < 1e-7:
                continue
            inverse = 1.0 / determinant
            tvec = origin - vertices[0]
            u = np.dot(tvec, pvec) * inverse
            if u < 0.0 or u > 1.0:
                continue
            qvec = np.cross(tvec, edge1)
            v = np.dot(direction, qvec) * inverse
            if v < 0.0 or u + v > 1.0:
                continue
            distance = np.dot(edge2, qvec) * inverse
            if 0.0 < distance < nearest_distance:
                nearest_distance = distance
                barycentric = np.array((1.0 - u - v, u, v), dtype=np.float32)
                nearest_uv = barycentric @ self.screen_uvs[triangle]
        return nearest_uv

    def _repeat_setting_adjustment(
        self, hand, action_name, active, setting, delta, minimum, maximum, now
    ):
        key = f"{hand}:{action_name}"
        previous = self.controller_previous.get(key, False)
        next_repeat = self.controller_repeat.get(key, 0.0)
        if active and (not previous or now >= next_repeat):
            with self.output.settings_lock:
                value = float(self.output.settings[setting])
                value = max(minimum, min(maximum, value + delta))
                self.output.settings[setting] = round(value, 2)
                settings = dict(self.output.settings)
            self.output._status(
                "Settings",
                f"distance={settings['distance']:.1f};"
                f"width={settings['width']:.1f};"
                f"curvature={settings['curvature']:.0f}",
            )
            self.controller_repeat[key] = now + (0.3 if not previous else 0.12)
        self.controller_previous[key] = active

    def _repeat_virtual_key(self, hand, action_name, active, virtual_key, now):
        key = f"{hand}:{action_name}"
        previous = self.controller_previous.get(key, False)
        next_repeat = self.controller_repeat.get(key, 0.0)
        if active and (not previous or now >= next_repeat):
            send_virtual_key(virtual_key)
            self.controller_repeat[key] = now + (1.0 if not previous else 0.12)
        self.controller_previous[key] = active

    def _upload_frame(self, frame):
        if frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError(f"OpenXR expects RGB HWC tensor, got {tuple(frame.shape)}")
        if frame.is_cuda and not self.cuda_interop_failed:
            try:
                self._upload_frame_cuda(frame)
                return
            except Exception as error:
                self.cuda_interop_failed = True
                self._delete_upload_pbo()
                self.output._status(
                    "Transfer",
                    f"CUDA-OpenGL interop unavailable, using CPU fallback: {error}",
                )

        with torch.inference_mode():
            upload = frame.detach().contiguous().cpu().numpy()
        height, width, _ = upload.shape
        gl = self.gl
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.source_texture)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        if self.source_size != (width, height):
            gl.glTexImage2D(
                gl.GL_TEXTURE_2D,
                0,
                gl.GL_RGB8,
                width,
                height,
                0,
                gl.GL_RGB,
                gl.GL_UNSIGNED_BYTE,
                upload,
            )
            self.source_size = (width, height)
        else:
            gl.glTexSubImage2D(
                gl.GL_TEXTURE_2D,
                0,
                0,
                0,
                width,
                height,
                gl.GL_RGB,
                gl.GL_UNSIGNED_BYTE,
                upload,
            )

    def _upload_frame_cuda(self, frame):
        upload = frame.detach().contiguous()

        height, width, _ = upload.shape
        device_id = upload.device.index or 0
        self._ensure_upload_pbo(upload.nbytes, device_id)

        gl = self.gl
        gl.glBindBuffer(gl.GL_PIXEL_UNPACK_BUFFER, self.upload_pbo)
        mapped_ptr = self.cudart.map_resource(self.cuda_resource)
        try:
            self.cudart.memcpy_d2d(mapped_ptr, upload.data_ptr(), upload.nbytes)
        finally:
            self.cudart.unmap_resource(self.cuda_resource)

        gl.glBindTexture(gl.GL_TEXTURE_2D, self.source_texture)
        gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
        if self.source_size != (width, height):
            gl.glTexImage2D(
                gl.GL_TEXTURE_2D,
                0,
                gl.GL_RGB8,
                width,
                height,
                0,
                gl.GL_RGB,
                gl.GL_UNSIGNED_BYTE,
                None,
            )
            self.source_size = (width, height)
        else:
            gl.glTexSubImage2D(
                gl.GL_TEXTURE_2D,
                0,
                0,
                0,
                width,
                height,
                gl.GL_RGB,
                gl.GL_UNSIGNED_BYTE,
                None,
            )
        gl.glBindBuffer(gl.GL_PIXEL_UNPACK_BUFFER, 0)

    def _ensure_upload_pbo(self, size, device_id):
        if (
            self.upload_pbo is not None
            and self.upload_pbo_size == size
            and self.cuda_device_id == device_id
        ):
            return

        self._delete_upload_pbo()
        from .local_viewer import _CUDART

        gl = self.gl
        self.upload_pbo = gl.glGenBuffers(1)
        gl.glBindBuffer(gl.GL_PIXEL_UNPACK_BUFFER, self.upload_pbo)
        gl.glBufferData(
            gl.GL_PIXEL_UNPACK_BUFFER,
            size,
            None,
            gl.GL_STREAM_DRAW,
        )
        gl.glBindBuffer(gl.GL_PIXEL_UNPACK_BUFFER, 0)

        self.cudart = _CUDART(device_id)
        self.cuda_resource = self.cudart.register_buffer(self.upload_pbo)
        self.upload_pbo_size = size
        self.cuda_device_id = device_id
        self.output._status("Transfer", "CUDA-OpenGL direct upload enabled")

    def _delete_upload_pbo(self):
        if self.cuda_resource is not None and self.cudart is not None:
            try:
                self.cudart.unregister_resource(self.cuda_resource)
            except Exception:
                pass
        self.cuda_resource = None
        self.cudart = None
        self.cuda_device_id = None
        self.upload_pbo_size = 0
        if self.upload_pbo is not None and self.gl is not None:
            try:
                self.gl.glBindBuffer(self.gl.GL_PIXEL_UNPACK_BUFFER, 0)
                self.gl.glDeleteBuffers(1, [self.upload_pbo])
            except Exception:
                pass
        self.upload_pbo = None

    def _render_views(self, views):
        xr = self.xr
        gl = self.gl
        settings = self._settings()
        self._update_mesh(settings)
        projection_views = []
        self.images_acquired = True

        gl.glUseProgram(self.program)
        gl.glBindVertexArray(self.vao)
        gl.glActiveTexture(gl.GL_TEXTURE0)
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.source_texture)
        gl.glUniform1i(gl.glGetUniformLocation(self.program, "source_texture"), 0)
        gl.glUniform1f(
            gl.glGetUniformLocation(self.program, "headset_fps"),
            self.output.get_fps(),
        )
        gl.glUniform1f(
            gl.glGetUniformLocation(self.program, "generation_fps"),
            self.output.get_generation_fps(),
        )
        gl.glUniform1i(
            gl.glGetUniformLocation(self.program, "show_fps"),
            1 if settings["show_fps"] else 0,
        )
        gl.glUniform1i(
            gl.glGetUniformLocation(self.program, "pointer_active"),
            1 if self.pointer_active else 0,
        )
        gl.glUniform2f(
            gl.glGetUniformLocation(self.program, "pointer_uv"),
            float(self.pointer_uv[0]),
            float(self.pointer_uv[1]),
        )
        gl.glDisable(gl.GL_DEPTH_TEST)
        gl.glDisable(gl.GL_CULL_FACE)

        for eye_index, (view, view_config, swapchain, images) in enumerate(
            zip(views, self.view_configs, self.swapchains, self.swapchain_images)
        ):
            image_index = xr.acquire_swapchain_image(
                swapchain, xr.SwapchainImageAcquireInfo()
            )
            self.acquired_swapchains.append(swapchain)
            xr.wait_swapchain_image(
                swapchain,
                xr.SwapchainImageWaitInfo(timeout=xr.INFINITE_DURATION),
            )
            gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.framebuffer)
            gl.glFramebufferTexture2D(
                gl.GL_FRAMEBUFFER,
                gl.GL_COLOR_ATTACHMENT0,
                gl.GL_TEXTURE_2D,
                images[image_index],
                0,
            )
            gl.glViewport(
                0,
                0,
                view_config.recommended_image_rect_width,
                view_config.recommended_image_rect_height,
            )
            gl.glClearColor(0.0, 0.0, 0.0, 1.0)
            gl.glClear(gl.GL_COLOR_BUFFER_BIT | gl.GL_DEPTH_BUFFER_BIT)
            mvp = self._projection_matrix(view.fov, 0.05, 100.0) @ self._view_matrix(
                view.pose
            )
            gl.glUniformMatrix4fv(
                gl.glGetUniformLocation(self.program, "mvp"),
                1,
                gl.GL_TRUE,
                mvp.astype(np.float32),
            )
            gl.glUniform1f(
                gl.glGetUniformLocation(self.program, "eye_offset"),
                0.5 * eye_index,
            )
            gl.glDrawElements(
                gl.GL_TRIANGLES, self.index_count, gl.GL_UNSIGNED_INT, None
            )
            projection_views.append(
                xr.CompositionLayerProjectionView(
                    pose=view.pose,
                    fov=view.fov,
                    sub_image=xr.SwapchainSubImage(
                        swapchain=swapchain,
                        image_rect=xr.Rect2Di(
                            offset=xr.Offset2Di(0, 0),
                            extent=xr.Extent2Di(
                                view_config.recommended_image_rect_width,
                                view_config.recommended_image_rect_height,
                            ),
                        ),
                    ),
                )
            )

        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, 0)
        gl.glFlush()
        projection_view_array = (
            xr.CompositionLayerProjectionView * len(projection_views)
        )(*projection_views)
        projection_layer = xr.CompositionLayerProjection(
            space=self.space,
            view_count=len(projection_views),
            views=projection_view_array,
        )
        base_layer = ctypes.cast(
            ctypes.byref(projection_layer),
            ctypes.POINTER(xr.CompositionLayerBaseHeader),
        )
        self.submission_refs = (
            projection_views,
            projection_view_array,
            projection_layer,
            base_layer,
        )
        return [base_layer]

    def _release_images(self):
        if not self.images_acquired:
            return
        for swapchain in self.acquired_swapchains:
            try:
                self.xr.release_swapchain_image(
                    swapchain,
                    self.xr.SwapchainImageReleaseInfo(),
                )
            except Exception:
                pass
        self.acquired_swapchains = []
        self.images_acquired = False

    def _settings(self):
        with self.output.settings_lock:
            return dict(self.output.settings)

    def _update_mesh(self, settings):
        mesh_key = (
            settings["projection"],
            settings["distance"],
            settings["width"],
            settings["height"],
            settings["curvature"],
            self.source_size,
            self.anchor_position.tobytes(),
            self.anchor_yaw,
        )
        if mesh_key == self.mesh_settings:
            return
        source_width, source_height = self.source_size
        eye_aspect = (source_width * 0.5) / source_height
        width = max(float(settings["width"]), 0.1)
        height = float(settings["height"])
        if height <= 0:
            height = width / eye_aspect
        distance = max(float(settings["distance"]), 0.1)
        curvature = (
            float(settings["curvature"]) if settings["projection"] == "curved" else 0.0
        )
        curvature_radians = math.radians(max(min(curvature, 150.0), 0.0))
        segments = 64 if curvature_radians > 0 else 1
        vertices = []
        indices = []

        yaw_cos = math.cos(self.anchor_yaw)
        yaw_sin = math.sin(self.anchor_yaw)
        radius = width / curvature_radians if curvature_radians > 1e-5 else 0.0
        for segment in range(segments + 1):
            u = segment / segments
            if curvature_radians > 1e-5:
                angle = (u - 0.5) * curvature_radians
                local_x = radius * math.sin(angle)
                local_z = -distance + radius * (1.0 - math.cos(angle))
            else:
                local_x = (u - 0.5) * width
                local_z = -distance
            world_x = self.anchor_position[0] + local_x * yaw_cos + local_z * yaw_sin
            world_z = self.anchor_position[2] - local_x * yaw_sin + local_z * yaw_cos
            for y, v in ((-height * 0.5, 0.0), (height * 0.5, 1.0)):
                vertices.extend(
                    (
                        world_x,
                        self.anchor_position[1] + y,
                        world_z,
                        u,
                        v,
                    )
                )
        for segment in range(segments):
            base = segment * 2
            indices.extend((base, base + 1, base + 2, base + 2, base + 1, base + 3))

        vertices = np.asarray(vertices, dtype=np.float32)
        indices = np.asarray(indices, dtype=np.uint32)
        self.screen_positions = vertices.reshape(-1, 5)[:, :3].copy()
        self.screen_uvs = vertices.reshape(-1, 5)[:, 3:5].copy()
        self.screen_indices = indices.copy()
        gl = self.gl
        gl.glBindVertexArray(self.vao)
        gl.glBindBuffer(gl.GL_ARRAY_BUFFER, self.vbo)
        gl.glBufferData(
            gl.GL_ARRAY_BUFFER, vertices.nbytes, vertices, gl.GL_DYNAMIC_DRAW
        )
        gl.glBindBuffer(gl.GL_ELEMENT_ARRAY_BUFFER, self.ebo)
        gl.glBufferData(
            gl.GL_ELEMENT_ARRAY_BUFFER, indices.nbytes, indices, gl.GL_DYNAMIC_DRAW
        )
        stride = 5 * vertices.itemsize
        gl.glEnableVertexAttribArray(0)
        gl.glVertexAttribPointer(0, 3, gl.GL_FLOAT, False, stride, ctypes.c_void_p(0))
        gl.glEnableVertexAttribArray(1)
        gl.glVertexAttribPointer(
            1, 2, gl.GL_FLOAT, False, stride, ctypes.c_void_p(3 * vertices.itemsize)
        )
        self.index_count = len(indices)
        self.mesh_settings = mesh_key

    def _set_anchor(self, views):
        if not views:
            return
        positions = np.asarray(
            [
                (view.pose.position.x, view.pose.position.y, view.pose.position.z)
                for view in views
            ],
            dtype=np.float32,
        )
        self.anchor_position = positions.mean(axis=0)
        orientation = views[0].pose.orientation
        self.anchor_yaw = math.atan2(
            2.0 * (orientation.w * orientation.y + orientation.x * orientation.z),
            1.0 - 2.0 * (orientation.y * orientation.y + orientation.z * orientation.z),
        )
        self.mesh_settings = None
        self.output._status(
            "Recentered", "Virtual screen anchored to current headset pose"
        )

    def _view_matrix(self, pose):
        rotation = self._quaternion_matrix(
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
        )
        translation = np.eye(4, dtype=np.float32)
        translation[:3, 3] = (
            -pose.position.x,
            -pose.position.y,
            -pose.position.z,
        )
        inverse_rotation = np.eye(4, dtype=np.float32)
        inverse_rotation[:3, :3] = rotation.T
        return inverse_rotation @ translation

    @staticmethod
    def _quaternion_matrix(x, y, z, w):
        return np.asarray(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ],
            dtype=np.float32,
        )

    @staticmethod
    def _projection_matrix(fov, near_z, far_z):
        left = math.tan(fov.angle_left) * near_z
        right = math.tan(fov.angle_right) * near_z
        bottom = math.tan(fov.angle_down) * near_z
        top = math.tan(fov.angle_up) * near_z
        matrix = np.zeros((4, 4), dtype=np.float32)
        matrix[0, 0] = 2 * near_z / (right - left)
        matrix[1, 1] = 2 * near_z / (top - bottom)
        matrix[0, 2] = (right + left) / (right - left)
        matrix[1, 2] = (top + bottom) / (top - bottom)
        matrix[2, 2] = -(far_z + near_z) / (far_z - near_z)
        matrix[2, 3] = -(2 * far_z * near_z) / (far_z - near_z)
        matrix[3, 2] = -1
        return matrix

    def _runtime_description(self):
        runtime = detect_openxr_runtime()
        renderer = self.gl.glGetString(self.gl.GL_RENDERER).decode(
            "utf-8", errors="replace"
        )
        sizes = [
            f"{view.recommended_image_rect_width}x{view.recommended_image_rect_height}"
            for view in self.view_configs
        ]
        return f"{runtime['runtime']} | OpenGL {renderer} | eyes {', '.join(sizes)}"

    def close(self):
        for button in tuple(self.mouse_buttons_down):
            send_mouse_button(button, False)
        self.mouse_buttons_down.clear()
        if self.xr is not None:
            for aim_space in self.aim_spaces.values():
                try:
                    self.xr.destroy_space(aim_space)
                except Exception:
                    pass
        self.aim_spaces.clear()
        self._release_images()
        if self.gl is not None and self.window is not None:
            try:
                self._delete_upload_pbo()
                if self.program:
                    self.gl.glDeleteProgram(self.program)
                if self.source_texture:
                    self.gl.glDeleteTextures([self.source_texture])
                if self.framebuffer:
                    self.gl.glDeleteFramebuffers(1, [self.framebuffer])
                if self.vbo:
                    self.gl.glDeleteBuffers(1, [self.vbo])
                if self.ebo:
                    self.gl.glDeleteBuffers(1, [self.ebo])
                if self.vao:
                    self.gl.glDeleteVertexArrays(1, [self.vao])
            except Exception:
                pass
        if self.xr is not None:
            if self.space:
                try:
                    self.xr.destroy_space(self.space)
                except Exception:
                    pass
            for swapchain in self.swapchains:
                try:
                    self.xr.destroy_swapchain(swapchain)
                except Exception:
                    pass
            if self.session:
                if self.session_running:
                    try:
                        self.xr.end_session(self.session)
                    except Exception:
                        pass
                try:
                    self.xr.destroy_session(self.session)
                except Exception:
                    pass
            if self.action_set:
                try:
                    self.xr.destroy_action_set(self.action_set)
                except Exception:
                    pass
            if self.instance:
                try:
                    self.xr.destroy_instance(self.instance)
                except Exception:
                    pass
        if self.glfw is not None:
            if self.hwnd and self.hdc:
                try:
                    ctypes.windll.user32.ReleaseDC(self.hwnd, self.hdc)
                except Exception:
                    pass
            if self.window is not None:
                try:
                    self.glfw.destroy_window(self.window)
                except Exception:
                    pass
            self.glfw.terminate()
