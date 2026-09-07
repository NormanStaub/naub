# naub

An open-source USB hardware mixer for **independent application and system audio
control**. Turn a knob to set per-app or system volume; press to mute. Built on a
Raspberry Pi Pico 2 (RP2350) with rotary encoders and an e-paper display.

- **System volume** works driverless via USB HID Consumer Control (Windows /
  macOS / Linux).
- **Per-app volume** runs through a small host helper over USB CDC serial
  (Windows WASAPI · Linux PipeWire · macOS via virtual audio driver).

## Project docs

| Doc | Purpose |
|-----|---------|
| [`WRITEUP.md`](WRITEUP.md) | **Developer handoff** — current state, decisions, gotchas, roadmap |
| [`PROTOCOL.md`](PROTOCOL.md) | **Host ⇄ device CDC protocol** — proto=2 (V2 mixer, meters, EQ); proto=1 is the retired V1 |
| [`firmware/v2/STATUS.md`](firmware/v2/STATUS.md) | **V2 state** — what landed, what is unfinished |
| [`V1_BUILD.md`](V1_BUILD.md) | V1 hardware build outline |
| [`BOM_V2.md`](BOM_V2.md) | Bill of materials (V2; [`BOM.md`](BOM.md) is V1) |
| [`WIRING.md`](WIRING.md) | Pin-by-pin wiring & pinout spec |

## Software

| Dir | Purpose |
|-----|---------|
| [`firmware/v2/`](firmware/v2/) | **Current** CircuitPython firmware — 128×64 LCD, mixer + EQ + config screens |
| [`naubd/`](naubd/) | **The always-on Windows daemon** (Rust) — meters, per-app volume, EQ. Runs at login |
| [`firmware/v2/host/`](firmware/v2/host/) | V2 host-side tooling — the development bridge, deploy, asset generators |
| [`firmware/`](firmware/) | V1 firmware (e-paper) — retired |
| [`host/`](host/) | V1 Windows helper (Python) — **archived**, superseded by `naubd/` |

## Status

Hardware bring-up. **V2 is the live line**: its firmware and the `naubd` daemon
are what run day to day. V1 (e-paper panel, proto=1, the Python host in
`host/`) is retired and kept only for reference. Hardware and firmware are
intended to be open source.
