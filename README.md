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
| [`PROTOCOL.md`](PROTOCOL.md) | **Host ⇄ device CDC protocol** (proto=1) — per-app volume + program icons |
| [`V1_BUILD.md`](V1_BUILD.md) | V1 hardware build outline |
| [`BOM.md`](BOM.md) | Bill of materials |
| [`WIRING.md`](WIRING.md) | Pin-by-pin wiring & pinout spec |

## Software

| Dir | Purpose |
|-----|---------|
| [`firmware/`](firmware/) | CircuitPython firmware — CDC protocol + e-paper with icons |
| [`host/`](host/) | Windows host helper (Python) — WASAPI per-app volume + icon push |

## Status

Early hardware bring-up (V1 prototype). Hardware and firmware are intended to be
open source.
