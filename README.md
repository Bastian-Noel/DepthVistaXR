# DepthVista XR

Languages: **English** · [Français](readme/FR.md)

DepthVista XR captures a Windows display or application window, estimates its
depth, generates a stereoscopic image in real time, and displays it in an
OpenXR headset.

This build adapts **nunif/IW3** with a Dear PyGui interface, direct OpenXR
output, flat or curved virtual screens, multilingual UI, and VR controller
support.

## Requirements

- Windows 10 or Windows 11 64-bit.
- An OpenXR-compatible headset.
- An installed and active PC OpenXR runtime.
- For Meta Quest: Meta Quest Link and a Link cable or Air Link connection.
- An NVIDIA CUDA-compatible GPU is recommended.
- Recent graphics drivers.
- About 9 GB of disk space for the current package and installed models.

Release packages are portable and include Python under `runtime`. A source
checkout from GitHub intentionally excludes the runtime and downloaded model
weights.

## Quick installation

### Windows installer

Build the network installer with:

```powershell
winget install JRSoftware.InnoSetup
.\scripts\build-installer.ps1
```

The generated `dist\DepthVistaXR-Setup-<version>.exe` installs the application
for the current user, downloads the portable Python/CUDA runtime, creates
shortcuts, and registers a Windows uninstaller.

### Release package

1. Extract or copy the complete folder to a writable location such as
   `C:\DepthVista-XR`.
2. Keep `DepthVista-XR.bat`, `app`, `runtime`, and `scripts` together.
3. Install the PC software for your headset and activate its OpenXR runtime.
4. Connect the headset and enter PC VR mode.
5. Double-click `DepthVista-XR.bat`.

Avoid `Program Files` if Windows prevents the application from writing its
configuration or model files.

### GitHub source checkout

1. Clone or download this repository.
2. Run `install.bat` once. It downloads portable Python 3.12 and installs the
   dependencies from `requirements.txt`.
3. Run `DepthVista-XR.bat`.
4. Download optional models when required by following the **Models** section.

The generated `runtime`, model files, and local configuration are excluded
from Git and must not be committed.

## Meta Quest setup

1. Install and start Meta Quest Link on the PC.
2. Connect the Quest through USB Link or enable Air Link.
3. Set Meta Quest Link as the active OpenXR runtime when required.
4. Enter Quest Link from the headset.
5. Start `DepthVista-XR.bat` on the PC.
6. Select a source and click **Start OpenXR**.

If no OpenXR runtime is detected, start the headset software and verify its
OpenXR setting before restarting DepthVista XR.

## First use

In the **General** tab:

1. Select a performance profile.
2. Select **Full screen** or **Window**.
3. Choose the display or application window.
4. Verify the selected source in the preview.
5. Select the generated resolution; `1080` is the default.
6. Keep the automatically selected capture method initially.
7. Adjust 3D strength manually or use `1`, `1.5`, or `2`.
8. Configure the virtual screen in the **OpenXR** tab.
9. Click **Start OpenXR**.

During a session, the setup interface is replaced with a compact live-control
interface containing only settings that can be changed in real time.

## Interface languages

The **Language** selector is displayed at the top of the application.
Available languages:

- English — default
- French
- Spanish
- Simplified Chinese

The selected language is saved in `app\tmp\depthvista-xr.json` and restored on
the next launch.

## Profiles

| Profile | Depth model | 3D method | Depth resolution | Target FPS | Purpose |
|---|---|---|---:|---:|---|
| Smooth | Distill Any Depth Small | `mlbw_l2s` | 392 | 60 | Lower GPU load and maximum fluidity |
| Balanced | Video Depth Anything Stream Small | `row_flow_v3` | 512 | 60 | Recommended general-purpose setting |
| Best quality | Video Depth Anything Stream Small | `row_flow_v3_sym` | 720 | 30 | Better image quality with higher GPU load |

The **generated resolution** (`720`, `900`, or `1080`) is the height of the
stereoscopic image sent to OpenXR. **Depth resolution** controls the resolution
used by the depth-estimation model. They are separate settings.

## Capture methods

| Method | Operation | Advantage | Limitation |
|---|---|---|---|
| `wc_cuda` | Windows Capture directly toward CUDA | Fastest NVIDIA path with fewer CPU copies | Requires CUDA and `wc_cuda` |
| `wc_mp` | Windows Capture in a separate process | Good performance and process isolation | Additional copy compared with CUDA |
| `mss` | Memory-based display capture | Compatible and stable | Usually slower |
| `pil` | Pillow/ImageGrab capture | Simple fallback | Slowest method |

DepthVista XR selects `wc_cuda` when available, otherwise `wc_mp`, then `mss`.

## OpenXR settings

- **Projection**: flat or curved virtual screen.
- **Distance**: virtual distance from the viewer.
- **Screen width**: horizontal screen size in meters.
- **Curvature**: screen curvature angle.
- **Show H/G FPS**: headset/output, generation, and capture rates.
- **3D strength**: stereoscopic separation.
- **Convergence**: perceived depth-plane offset.

Source, model, 3D method, and generated resolution are selected before
starting. Distance, size, curvature, 3D strength, and controller settings can
be changed during the OpenXR session.

## Controller modes

### Desktop mode

- Visible pointer.
- Trigger: left click and drag.
- Grip without movement: optional right click.
- `A/X`: play or pause.
- `B/Y`: stop the session.
- Stick left/right: video left/right arrow.
- Stick up/down: zoom.
- Grip + stick left/right: change curvature.
- Grip + stick up/down: resize the screen.
- Stick click: recenter.

### Cinema mode

- No mouse pointer.
- Trigger: play or pause.
- Navigation, resizing, and recenter controls remain available.

## Models

All AI models are optional and retain their own licenses. Installed or
downloaded models are stored under:

`app\iw3\pretrained_models`

To download or repair supported models:

```bat
cd /d C:\DepthVista-XR\app
..\runtime\python\python.exe -X utf8 -m iw3.download_models
```

Replace `C:\DepthVista-XR` with the actual installation path. Downloads may be
large. Some models are restricted to non-commercial use; review `LICENSE`
before redistribution or commercial use.

## Project layout

```text
DepthVista-XR.bat       Main launcher
app/
  iw3/                  IW3 adapted for DepthVista XR
  nunif/                Shared nunif runtime required by IW3
  tmp/                  Local application configuration
runtime/
  python/               Portable Python and installed dependencies
scripts/
  setup-env.bat         Prepares the portable environment
  launch-debug.bat      Starts with a diagnostic console
licenses/               Preserved third-party licenses
readme/                 Additional documentation languages
LICENSE                 Project and model licensing scope
requirements.txt        Global Windows/CUDA dependencies
```

## Rebuilding the runtime

Normal users should keep the supplied `runtime` directory. For development or
repair:

1. Install or place 64-bit Python 3.12 in `runtime\python`.
2. Open PowerShell in the project root.
3. Install the dependencies:

```powershell
$python = ".\runtime\python\python.exe"
& $python -m pip install --upgrade pip
& $python -m pip install -r ".\requirements.txt"
```

The current build uses PyTorch 2.7.1 with CUDA 12.8, pyopenxr 1.1.5301,
glfw 2.10.0, PyOpenGL 3.1.10, and Dear PyGui 2.3.1.

## Troubleshooting

If the normal launcher closes without a message:

```bat
scripts\launch-debug.bat
```

Useful checks:

```bat
runtime\python\python.exe -X utf8 -c "import iw3.desktop.gui_dpg; print('Import OK')"
runtime\python\python.exe -X utf8 -c "from iw3.desktop.openxr_output import detect_openxr_runtime; print(detect_openxr_runtime())"
```

### Black screen or no headset image

- Confirm that the headset is already connected in PC VR mode.
- Verify the active OpenXR runtime.
- Test another known OpenXR application.
- Try **Full screen** instead of window capture.
- Try `wc_mp`, then `mss`.
- Close overlays and competing capture software.

### Black artifacts during movement

- Test generated resolution `720`, then `900`.
- Use **Smooth** or **Balanced**.
- Reduce depth resolution.
- Compare `wc_cuda` and `wc_mp`.
- Update the GPU driver and headset software.
- Check GPU temperature and memory pressure.

### Low performance

- Use the **Smooth** profile.
- Reduce generated resolution.
- Close GPU-heavy applications.
- Prefer `wc_cuda` on compatible NVIDIA hardware.
- Disable unnecessary overlays.

## DRM-protected video

DepthVista XR performs normal screen or window capture and does not bypass DRM.
Netflix, Prime Video, and other services may deliberately return a black area
to capture software. Behavior depends on the browser, hardware acceleration,
the service, and its DRM policy.

Disabling browser hardware acceleration can change capture behavior, but it is
not guaranteed and must not be used to circumvent technical protections. Use
the playback and offline features authorized by the content provider.

## Configuration reset

The main configuration file is:

`app\tmp\depthvista-xr.json`

To reset DepthVista XR preferences:

1. Close the application.
2. Rename or delete `app\tmp\depthvista-xr.json`.
3. Start `DepthVista-XR.bat`.

Do not remove `app\iw3\pretrained_models` unless models should be downloaded
again.

## Licenses

See `LICENSE` and the `licenses` directory. nunif, IW3, adapted XRPlay code,
third-party libraries, and optional models may use different licenses.
