# naubd — native daemon, Windows and macOS (V2)

> **macOS is written but unproven:** type-checked for both Mac targets from
> Windows, never linked or run. Test it with
> [`macos/TESTING.md`](macos/TESTING.md); see [macOS](#macos) below. Everything
> else on this page describes Windows.

The **always-on background process**: low footprint, no console window,
auto-start on login, robust USB device discovery. It speaks
[`../PROTOCOL.md`](../PROTOCOL.md) **proto=2** over the device's USB CDC data
port and drives the V2 mixer panel from this PC's real audio.

Without it the device has no audio to show and no way to apply a knob: the panel
is a display, the daemon is the thing that talks to WASAPI. It reconnects by
itself when the board reloads (every `deploy.py` does) or is replugged.

It is a port of [`../firmware/v2/host/naub_bridge.py`](../firmware/v2/host),
which remains the development bridge — run that in a terminal when you are
working on firmware and want its per-frame chatter. Both keep their column
assignments in the same `pins.json`, so a machine can move between them without
losing its columns.

**V1 is retired.** This daemon no longer speaks proto=1: `cycle`, `icon` and
`iconclear` are gone, `btn` is momentary rather than latched, and the
channel→process config that drove the V1 e-paper panel has been dropped. The V1
Python host in [`../host`](../host) is archived and should not be wired up.

## Build

```powershell
cd naubd
cargo build --release        # -> target\release\naubd.exe
```

Needs the stable MSVC Rust toolchain (`rustup default stable-msvc`) and the VS
Build Tools (for the linker). `cargo test` covers the wire format, the filter
syntax, the pin file and the FFT.

## Install (background app)

1. Copy `naubd.exe` somewhere stable. A `config.json` is **optional** — see
   below.
2. Start it once (double-click, or run it) — a tray icon appears.
3. Right-click the tray icon → **Start with Windows** to auto-launch on login.

### Tray menu

- **Start with Windows** — toggles the `HKCU\…\Run\naubd` entry (checkable).
- **Quit** — removes the tray icon and exits.

## Configuration

There is almost none, by design. The V2 panel picks its own source from the app
switcher and the daemon remembers it, so there is no mapping to write down.

| What | Where | Note |
|---|---|---|
| Column assignments | `%LOCALAPPDATA%\naub\pins.json` | Written by the daemon, shared with the Python bridge. Survives a reload, a restart, and the app being closed. |
| Port / meter rate | `config.json` (optional) | Discovery order: next to the exe → `%APPDATA%\naub\config.json` → current dir. Absent means defaults. |
| EQ state | the Equalizer APO config dir | `naub-eq.txt`, plus one guarded `Include:` line in the user's `config.txt`. `--eq-remove` takes both back out. |
| App icons | `%LOCALAPPDATA%\naub\icons\` | One file per app, 808 bytes, redone when the exe changes. Safe to delete: they are made again the next time the app plays. |
| Log | `naubd.log`, beside the config | |

## Command line

Release builds are windowless. PowerShell does **not** wait for a
GUI-subsystem process, so pipe through `Start-Process -Wait` (or use the debug
build) if a diagnostic seems to print nothing.

```powershell
naubd.exe                 # default: tray icon + background daemon
naubd.exe --console       # run in the foreground with the full per-event log
naubd.exe --config PATH   # foreground with a specific config
naubd.exe --install       # enable start-with-Windows (same as the menu toggle)
naubd.exe --uninstall     # disable it
naubd.exe --list-ports    # list serial ports
naubd.exe --probe         # find the naub data port by USB VID/PID
naubd.exe --sessions      # what audio does Windows see, grouped as columns
naubd.exe --icon EXE      # draw the icon an exe would get on the panel
naubd.exe --eq            # is there an EQ engine, and would it be heard?
naubd.exe --eq-attach     # put Equalizer APO on the current output (UAC prompt)
naubd.exe --eq-remove     # take naub back out of the Equalizer APO config
naubd.exe --spectrum      # watch the loopback capture, no device needed
```

Equalizer APO attaches per output device, and Windows quietly re-creates
devices under new IDs (a driver update did it here), which detaches it: the
panel's EQ goes INERT within a second. The tray menu then offers **Attach EQ to
&lt;device&gt;**, which is `--eq-attach`: one UAC prompt, the same registry layout
Equalizer APO's DeviceSelector writes (so its uninstaller still restores the
original effects), and an audio-service restart instead of a reboot, about a
second of silence. Measured: 1.05 s end to end, and the audio engine loaded
`EqualizerAPO.dll` on the next sound. Endpoints with no `FxProperties` key, or
the pre-8.1 LFX/GFX layout, are refused with a pointer to DeviceSelector.

`--sessions` is the one to reach for when an app is listed but its knob does
nothing: the usual answer is that the program holds several sessions and only
one of them is audible.

## Layout

| File | Role |
|---|---|
| `protocol.rs` | proto=2 encode/decode (+ tests) |
| `link.rs` | USB CDC serial + VID/PID autodetect. Asserts **DTR** — CircuitPython gates the data port on it |
| `audio.rs` | WASAPI per-app + system volume, mute and **peak metering** across every render endpoint |
| `sysmeter.rs` | WASAPI **loopback capture** + its own radix-2 FFT, for the EQ screen's spectrum |
| `eqapo.rs` | the three-band parametric EQ, written into Equalizer APO |
| `pins.rs` | column assignments, in `%LOCALAPPDATA%` |
| `config.rs` | the little that is left to configure |
| `bridge.rs` | orchestrator: meters, apps, columns, EQ, reconnect |
| `tray.rs` | tray icon, menu, autostart (HKCU Run key) |
| `logging.rs` | stdout (debug) + file log |

## Cost

Measured on the development machine, both attached to the same board with
nothing playing:

| | RAM | CPU |
|---|---|---|
| `naubd` | 14 MB | ~0.65% of one core |
| `naub_bridge.py` | 39 MB | ~3.0% of one core |

Most of that gap is not the language. The Python bridge's loop ends in
`time.sleep(0.002)`, so it wakes at whatever the Windows timer granularity
happens to be — measured at 408 Hz, set by whatever *else* on the PC has raised
the timer resolution — and pays per-iteration cost every time, regardless of
`--rate`. This daemon parks on the serial read instead and sleeps to the next
meter deadline, so its wake rate is its own business.

## macOS

Same binary shape and the same bridge, over a platform with no per-app peak and
no per-app volume API ([`../firmware/v2/MACOS_PORT.md`](../firmware/v2/MACOS_PORT.md)).
Linux would need its own `audio` / `sysmeter` / `eqapo` / `tray` the same way.

| File | Role |
|---|---|
| `audio_mac.rs` | process taps: one tap, private aggregate and IOProc per app, on a worker thread; system volume; `--doctor` |
| `dsp.rs` | the real-time sample work and the EQ's biquads. No Core Audio, so `cargo test` covers it anywhere |
| `appkey.rs` | executable path → column key (outermost `.app`, aliased to the Windows key where they differ) |
| `tray_mac.rs` | menu-bar item, `SMAppService` login item |
| `macos/` | `Info.plist` (embedded by `build.rs`, since a missing usage string means silent capture), `build-app.sh`, `TESTING.md` |

Differences from Windows that are on purpose:

- **An app at 100% with a flat EQ is only metered.** It still plays straight to
  the device, with no added latency. Turning its knob mutes it at the source and
  plays a gain-scaled copy; turning it back undoes that.
- **It needs a grant:** System Audio Recording, and refusal looks like silence.
  Nothing is muted until a real sample has been seen, so the failure is dead
  meters and INERT, not silent apps.
- **naub runs the EQ itself.** There is no Equalizer APO for a Mac, and the
  re-render path is the insertion point the OS lacks, so three biquads run there
  (`--eq-attach` does not exist). System sounds are not tapped, so they are not
  EQ'd. This departs from "naub writes no DSP" and can be reversed by having
  `dsp::EqChain` do nothing.
- **Knob positions persist in `volumes.json`** beside `pins.json`, because
  nothing in macOS remembers them.

```sh
cargo build --release          # bare binary; the grant goes to your terminal
macos/build-app.sh             # target/naub.app; the grant is naub's own
./target/release/naubd --doctor   # the first thing to run when audio does nothing
```
