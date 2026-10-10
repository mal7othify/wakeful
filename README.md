# Wakeful

A minimal **Keep Awake** utility for Linux, macOS (Apple Silicon) and Windows.

* **Native mode (default):** asks the OS not to go idle through its
  power-management API. Nothing moves, nothing is typed, no focus changes,
  zero CPU while enabled.
* **Simulate activity (opt-in):** additionally nudges the cursor one pixel and
  straight back every 60 seconds so Slack, Teams and similar apps keep showing
  you as active. Never clicks, never types.

```
Keep Awake

[ ON ]

Status: Active
The computer will remain awake while this is enabled.

[x] Simulate activity (keeps Slack / Teams showing you as active)
```

Python 3.10+ and the standard library only. No third-party runtime dependencies.

## Install and run

**Download a build** from the Releases page: `wakeful-windows-x86_64.exe`,
`wakeful-macos-arm64.dmg`, or `wakeful-linux-x86_64.tar.gz`. The builds are
unsigned: Windows SmartScreen shows a warning once ("More info" → "Run anyway"),
and on macOS right-click the app → Open the first time.

**From source** with [uv](https://docs.astral.sh/uv/) (fetches a Python with tkinter if needed):

```sh
uv run wakeful                 # window
uv run wakeful --headless      # terminal only; Ctrl+C stops it
uvx --from git+https://github.com/mal7othify/wakeful wakeful   # no checkout needed
```

With a system Python that has tkinter: `PYTHONPATH=src python3 -m wakeful`.
If you see "Wakeful needs tkinter": `sudo apt install python3-tk` (Debian/Ubuntu),
`sudo dnf install python3-tkinter` (Fedora), `sudo pacman -S tk` (Arch), or use `--headless`.

Options:

| Flag | Meaning |
|------|---------|
| `--headless` | no window; enable immediately, stop on Ctrl+C / SIGTERM |
| `--mode native\|mouse` | override the configured mode for this run |
| `--interval SECONDS` | nudge interval in mouse mode (default 60) |
| `--config PATH` | use another config file |
| `-v` | debug logging |

## How it works

| OS | Native keep-awake | Simulated activity |
|----|-------------------|--------------------|
| Linux | `systemd-inhibit --what=idle`, plus `gnome-session-inhibit` on GNOME or `kde-inhibit` on KDE. X11 and Wayland. Only `idle` is inhibited, so manual lock, suspend, logout and shutdown keep working. | XTest extension (`libXtst`) via `ctypes`. Works on X11 and, through XWayland, on GNOME and KDE Wayland sessions. |
| macOS | IOKit `IOPMAssertionCreateWithName("PreventUserIdleDisplaySleep")` via `ctypes`, in-process (what `caffeinate -d` does, minus the child process). Visible in `pmset -g assertions`. | Quartz `CGEventPost` via `ctypes`. Needs **Accessibility** permission; macOS prompts the first time, or add Wakeful (or the terminal it runs from) under System Settings → Privacy & Security → Accessibility. |
| Windows | `SetThreadExecutionState(ES_CONTINUOUS \| ES_SYSTEM_REQUIRED \| ES_DISPLAY_REQUIRED)` via `ctypes`. Scoped to the process; no registry, power plan or admin access. | `SendInput` with two relative moves (+1 px, −1 px) in one batch. |

On every platform the OS drops the native request automatically if the process
dies, so a crash cannot leave the machine stuck awake. On Linux each inhibitor
helper wraps a `cat` that reads a pipe Wakeful holds; closing the pipe (or
dying) ends the inhibitor. Cleanup also runs on window close,
SIGINT/SIGTERM/SIGHUP, exceptions escaping the main loop, and interpreter exit.

Native mode has no timers at all. Mouse mode uses one sleeping thread that
wakes once per interval.

## Configuration

`config.json` in the per-user config directory
(`~/.config/wakeful/`, `~/Library/Application Support/wakeful/`, `%APPDATA%\wakeful\`):

```json
{
  "keep_awake": {
    "enabled": false,
    "mode": "native",
    "interval_seconds": 60
  }
}
```

`enabled` and `mode` are written whenever you change them in the window and
restored on the next launch. `mode` is `native` or `mouse`.

## Tests

```sh
uv run python -m unittest discover -s tests -v
# or: uv run --with pytest pytest
```

82 tests cover the service state machine, the activity scheduler, every
platform backend and cursor mover (through fake native APIs and fake
processes), backend selection and the config file. They need no desktop
session and run on any OS; CI runs them on Linux, macOS and Windows.

## Build a standalone executable

Pushing a tag such as `v0.1.0` runs `.github/workflows/release.yml`, which
builds all three binaries and attaches them to a GitHub Release. Locally, on
the target OS (PyInstaller does not cross-compile):

```sh
uv sync --group build
uv run pyinstaller --paths src --onefile --windowed --name wakeful --icon assets/icon.ico src/wakeful/__main__.py    # Windows
uv run pyinstaller --paths src --onefile --name wakeful src/wakeful/__main__.py                                    # Linux
uv run pyinstaller --paths src --windowed --name Wakeful --icon assets/icon.icns src/wakeful/__main__.py          # macOS -> dist/Wakeful.app
```

Code signing is not set up. A signed, notarized macOS build needs an Apple
Developer ID; a signed Windows build needs a code-signing certificate.

## Project layout

```
src/wakeful/
  app.py                    tkinter window, headless mode, signal handling, entry point
  config.py                 JSON config
  icon.py                   window / dock icon, embedded as base64 PNG
  keep_awake/
    service.py              KeepAwakeService: start / stop / is_active / close
    backend.py              backend protocol + error types
    activity.py             MouseActivityBackend (timer thread) + CompositeBackend
    platform/
      __init__.py           create_backend(mode): native, or native + activity
      linux.py  macos.py  windows.py   one native backend and one cursor mover each
tests/                      unit tests (no OS desktop needed)
assets/                     icon.png / .ico / .icns and make_icon.py, which renders them
.github/workflows/          ci.yml (tests on 3 OSes), release.yml (tagged builds)
```

The rest of the app only touches `KeepAwakeService` and `create_backend`, so
the feature can be lifted out or swapped without affecting anything else.

## Limitations

* **Linux without systemd, GNOME or KDE** (e.g. XFCE on a non-systemd distro):
  no native inhibitor is available and native mode reports an error. Mouse
  mode still works wherever X11/XWayland is present. Adding another desktop
  is one line in `DESKTOP_INHIBITORS`.
* **Pure Wayland without XWayland**: simulated activity is unavailable (no
  portable way to inject input).
* **Windows**: a lock-screen policy enforced by Group Policy
  (`InactivityTimeoutSecs`) still applies in native mode.
* **macOS**: simulated activity requires the Accessibility permission; without
  it the feature stays off and says so. The permission attaches to the app
  bundle, or to the terminal/IDE when run from source. Because the builds are
  not signed with a Developer ID, every update looks like a new app to macOS:
  remove Wakeful from the Accessibility list and add it again after updating
  (or `tccutil reset Accessibility io.github.mal7othify.wakeful`). Signing with a
  Developer ID certificate makes the permission survive updates.
* Mouse mode moves the cursor by one pixel and back once per interval. If you
  are mid-drag at that exact moment you may notice it.

## License

[MIT](LICENSE).
