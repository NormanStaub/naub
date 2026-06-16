# Example: dual encoder bars + press-blobs

Builds on `../encoder_bar_epaper/` — drives **two** rotary encoders, each as an
independent 0–100 vertical bar, plus a **press indicator** ("blob") above each
bar that fills solid while that encoder's push-switch is held.

Demonstrates **multi-region partial refresh**: both bars, both numbers, and both
blobs live in one windowed gate-line region; "naub" and the label never refresh.

## Hardware

- Raspberry Pi Pico 2 (RP2350), CircuitPython 10.2.x (ARM build)
- 2× Bourns PEC11R rotary encoders (with push switch)
- Waveshare 2.9" e-Paper Module V3 (SSD1680, 296×128)

### Wiring (per repo `WIRING.md`)

| Signal | Pico | Phys pin |
|--------|------|----------|
| ENC1 A | GP0  | 1  |
| ENC1 B | GP1  | 2  |
| ENC1 SW | GP2 | 4  |
| ENC2 A | GP3  | 5  |
| ENC2 B | GP4  | 6  |
| ENC2 SW | GP5 | 7  |
| Display DC | GP8 | 11 |
| Display CS | GP9 | 12 |
| Display CLK | GP10 | 14 |
| Display DIN | GP11 | 15 |
| Display RST | GP12 | 16 |
| Display BUSY | GP13 | 17 |
| Display VCC | 3V3(OUT) | 36 |

Each encoder: A/B → quadrature, SW + GND → push switch (active-low, internal
pull-up). Encoder commons (C) and SW grounds tie to the common ground bus.

## Install

Same as `../encoder_bar_epaper/`: flash CircuitPython 10.2.x for the Pico 2,
copy `code.py` to the `CIRCUITPY` root, and copy these libs to `CIRCUITPY/lib/`:
- `adafruit_display_text/`
- `adafruit_ticks.mpy`

## Behavior & layout

- **Right bar = Encoder 1, left bar = Encoder 2.** Turn each knob → its bar fills
  (0–100, `STEP=2` units/detent), with the value shown above.
- **Hold an encoder's knob** → the hollow ring above its bar fills into a solid
  blob; release → back to a ring.
- Because e-paper partial refresh is ~0.5 s, the blob appears ~0.5 s into a hold
  and clears ~0.5 s after release — **hold for a beat** to see it (a quick tap is
  too fast).

## Notes

- Refresh: clean OTP partial (`0xFC`), windowed to native gate lines `GY0..GY1 =
  0..100` (covers both bars + blobs; leaves the left-side text untouched).
- Drawing is incremental per bar (`shown_fill`), digits pre-rendered once.
- See `../encoder_bar_epaper/README.md` for the full partial-refresh latency
  findings (~0.5 s floor, temperature-bound, custom-LUT dead end).
- Button 1 (GP6) is intentionally not used here (was miswired during bring-up).
