# naub host helper (Windows V1)

The small host-side app that makes naub's **per-app volume** work — the
deej-style half of the project. It bridges the device and Windows audio:

- **Device → audio:** knob turns (`vol`) set the mapped app's WASAPI volume;
  encoder presses (`mute`) mute it; Button 1 toggles system mute.
- **Audio → device:** pushes each channel's **app name**, **program icon**
  (extracted from the executable, dithered to 1-bpp for the e-paper), and the
  **current level/mute** so the panel matches reality.

Speaks [`../PROTOCOL.md`](../PROTOCOL.md) (proto=1) over the device's USB CDC
**data** port. Windows-only for V1; the audio/icon code is isolated in
`audio_windows.py` / `icons_windows.py` so Linux/macOS adapters slot in later.

## Install

```powershell
cd host
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

(Python 3.9+ recommended.)

## Configure

Copy the example and edit the channel → process mapping (deej-style):

```powershell
copy config.example.json config.json
```

```json
"channels": {
  "0": { "label": "Spotify", "match": ["spotify.exe"] },
  "1": { "label": "Browser", "match": ["chrome.exe", "msedge.exe"] },
  "3": { "label": "System",  "match": ["__system__"] }
}
```

- Channels **0–2** are the app knobs on V1; channel **3** is the system/master knob.
- `match` is one or more process names (case-insensitive). First running match wins.
- `__system__` rides the default output device (master volume).
- `label` is optional; omit it to use the app's process name.
- `"port": "auto"` probes for the device; set a fixed `"COM7"` to skip probing.

## Run

```powershell
python run.py
```

Turn an app knob and the mapped app's volume should move; the panel shows the
app icon, name, level bar, and number. Press a knob to mute (icon gets an X).
Click the system knob to open the **global EQ** menu (bass/mid/treble on the app
knobs); this requires [Equalizer APO](https://sourceforge.net/projects/equalizerapo/)
installed and assigned to your output device.

Helpers:

```powershell
python run.py --list-ports     # see all serial ports
python run.py --probe          # find just the naub data port
python run.py --port COM7      # force a port
```

## How it works (quick map)

| File | Role |
|---|---|
| `run.py` | CLI entry: load config, run the app, reconnect on drop. |
| `naub_host/app.py` | Orchestrator: event handling + periodic reconcile. |
| `naub_host/serial_link.py` | CDC framing + **data-port autodetect** (probe `hello` → `naub` banner). |
| `naub_host/audio_windows.py` | WASAPI per-app + system volume/mute (pycaw). |
| `naub_host/eq_windows.py` | Global 3-band tone control via Equalizer APO (optional; no-ops if absent). |
| `naub_host/icons_windows.py` | Exe icon → 32×32 1-bpp dithered → packed bytes. |
| `naub_host/protocol.py` | proto=1 encode/decode. |
| `naub_host/config.py` | Load/normalize the JSON config. |

The host **never echoes** state the device reported: device-originated `vol`/
`mute` flow one way (into audio), host-originated `level`/`icon`/`label` flow the
other (onto the panel). That keeps the loop from oscillating — see PROTOCOL.md §8.

## Source selection & persistence

- **Cycling offers only real audio apps.** Hold a knob and turn to reassign its
  source; the candidate list is every app that has actually produced audio this
  session — so a paused or idle media app stays pickable, not just whatever is
  rendering at that instant (the system endpoint is its own pinned knob). Apps
  that merely hold a silent session — File Explorer, CAD tools — never appear,
  nor does a momentary UI/error ding (the ~2s poll doesn't sample sub-second
  blips), nor the Windows *system sounds* session. Note: sources are **unique per
  knob**, so an app already assigned to one channel won't be offered on another.
- **Selections are sticky.** Once a channel is bound to an app, it **stays** on
  the panel (name + icon + bar) even after that app is paused, closed, or the
  device disconnects — so you can re-open the app and the knob still drives it.
  A channel only changes when you cycle it to something else. Volume control
  simply no-ops while the bound app has no audio session.
- **Survives reboot.** The current selections are saved to `naub_state.json`
  (next to your `config.json`, git-ignored) and reloaded on startup. The
  `config.json` `match` lists are now just the **initial seed** for a fresh
  channel; after that, the live selection wins. Delete `naub_state.json` to reset
  back to the config defaults.

## Troubleshooting

- **"no naub device found"** — confirm `firmware/boot.py` is on the board and you
  **power-cycled** it (two serial ports must enumerate). Try `python run.py --probe`.
- **Volume doesn't move** — the app must be actively playing/holding an audio
  session for Windows to expose it; check `match` names against Task Manager.
- **No icon (just a letter)** — the exe's icon couldn't be read; the host falls
  back to a text glyph. System/master has no exe, so it always shows a letter.
- **Port is busy** — close other serial monitors (Mu, PuTTY) holding the data port.
