# naubd — native Windows daemon

A from-scratch Rust port of the [Python host](../host), built to run as a
**background tray service**: low footprint (~15 MB RAM, ~325 KB exe), no console
window, auto-start on login, robust USB device discovery.

It speaks the same [`../PROTOCOL.md`](../PROTOCOL.md) (proto=1) over the device's
USB CDC data port and is feature-equivalent to the Python host: per-app WASAPI
volume/mute, program icons (Floyd–Steinberg dithered to 1-bpp), sticky channel
selections persisted across reboot, active-audio-app cycle pool, auto-reconnect.

## Build

```powershell
cd naubd
cargo build --release        # -> target\release\naubd.exe
```

Needs the stable MSVC Rust toolchain (`rustup default stable-msvc`) and the VS
Build Tools (for the linker).

## Install (background app)

1. Copy `naubd.exe` and a `config.json` to a stable folder, **or** put
   `config.json` in `%APPDATA%\naub\`. Config discovery order: next to the exe →
   `%APPDATA%\naub\config.json` → current dir → bundled `config.example.json`.
2. Start it once (double-click, or run it) — a tray icon appears.
3. Right-click the tray icon → **Start with Windows** to auto-launch on login.

`config.json` is the same format as the Python host — copy `config.example.json`
and edit the channel → process mapping. Sticky selections are saved to
`naub_state.json` and the log to `naubd.log`, both beside the config.

### Tray menu

- **Start with Windows** — toggles the `HKCU\…\Run\naubd` entry (checkable).
- **Quit** — removes the tray icon and exits.

(Config changes take effect on restart; there's no live reload yet.)

## Command line

Release builds are windowless; these print to the terminal when launched from one.

```powershell
naubd.exe                 # default: tray icon + background daemon
naubd.exe --console       # run in the foreground with live logs (debugging)
naubd.exe --config PATH   # foreground with a specific config
naubd.exe --install       # enable start-with-Windows (same as the menu toggle)
naubd.exe --uninstall     # disable it
naubd.exe --list-ports    # list serial ports
naubd.exe --probe         # find the naub data port by USB VID/PID
naubd.exe --audio         # dump current audio sessions (WASAPI sanity check)
naubd.exe --icon EXE_PATH # ASCII-preview an exe's dithered 1-bpp icon
```

## Layout

| File | Role |
|---|---|
| `protocol.rs` | proto=1 encode/decode (+ tests) |
| `link.rs` | USB CDC serial + VID/PID autodetect (no COM-port probing) |
| `audio.rs` | WASAPI per-app + system volume/mute via windows-rs COM |
| `icons.rs` | exe icon → grayscale → Floyd–Steinberg → 1-bpp pack |
| `config.rs` / `state.rs` | JSON config + sticky-selection persistence |
| `app.rs` | orchestrator: events, reconcile, sticky, cycle, repaint dedupe |
| `tray.rs` | tray icon, menu, autostart (HKCU Run key) |
| `logging.rs` | stdout (debug) + file log |

The Python host in [`../host`](../host) remains as the cross-platform reference;
`naubd` is the Windows-native path. Linux/macOS would each get their own audio
adapter (PipeWire / a virtual-device approach) behind the same protocol.
