# Example: encoder-driven volume bar on e-paper

A CircuitPython bring-up example for the naub V1 prototype: one rotary encoder
drives a 0–100 level bar + numeric readout on the 2.9" e-paper display, using a
**hand-written SSD1680 driver with windowed partial refresh**.

This is a *bench/validation* example (proves the pin map, encoder decode, and
display partial-refresh), not the final firmware. It deliberately uses no
display driver library — the SSD1680 is driven directly so the refresh waveform
is under our control.

## Hardware

- Raspberry Pi Pico 2 (RP2350), CircuitPython 10.2.x (ARM build)
- 1× Bourns PEC11R rotary encoder (Encoder 1 position)
- Waveshare 2.9" e-Paper Module V3 (SSD1680, 296×128, B/W)

### Wiring (per repo `WIRING.md`)

| Signal | Pico | Phys pin |
|--------|------|----------|
| Encoder A   | GP0  | 1  |
| Encoder B   | GP1  | 2  |
| Encoder C   | GND  | 3  |
| Display DC  | GP8  | 11 |
| Display CS  | GP9  | 12 |
| Display CLK | GP10 | 14 |
| Display DIN | GP11 | 15 |
| Display RST | GP12 | 16 |
| Display BUSY| GP13 | 17 |
| Display VCC | 3V3(OUT) | 36 |
| Display GND | GND  | 38 |

The encoder switch (GP2) and Encoders 2–4 / buttons are not used by this example.

## Install

1. Flash CircuitPython 10.2.x for the **Raspberry Pi Pico 2** (ARM build) via
   BOOTSEL → drag the `.uf2` onto the `RP2350` drive.
2. Copy `code.py` to the root of the `CIRCUITPY` drive.
3. Copy these libraries to `CIRCUITPY/lib/` (from the matching Adafruit
   CircuitPython 10.x bundle):
   - `adafruit_display_text/`  ← used only to rasterize text into the framebuffer
   - `adafruit_ticks.mpy`      ← dependency of `adafruit_display_text`

   > Note: no `adafruit_ssd1680` needed — the panel is driven manually in `code.py`.

## How it works

- A 1-bpp framebuffer (`fb`) is maintained in a **landscape (296×128)** logical
  space; `px()` maps to the panel's native 128×296 RAM (rotation via `MNX/MNY`).
- Text is rasterized into `fb` via `bitmap_label` (terminalio font); digits 0–9
  are pre-rendered once.
- Drawing is **incremental**: only the changed band of the bar is repainted, and
  the number cell is cleared+redrawn. (This was a real latency win — see below.)
- Refresh uses the GxEPD2 `GxEPD2_290_T94` recipe for this SSD1680 panel:
  - **Full** refresh: `0x22 0xF7` (boot, and a periodic ghost-sweep every 40 updates)
  - **Partial** refresh: `0x22 0xFC`, **windowed** to just the bar/number gate
    lines (`GY0..GY1`) so the rest of the panel is never driven and stays clean.

## Tunables

- `STEP` — units per detent (default 2 → ~50 detents full range)
- `GY0/GY1` — native gate-line window for the partial refresh region
- `MNX/MNY` — orientation flips (set for this panel; flip if mirrored)

## Performance notes / findings (so you don't re-learn these the hard way)

Measured on this panel:

- **Clean partial refresh ≈ 498 ms**, and it is **flat** regardless of how many
  pixels changed — e-paper refresh time is set by the **waveform duration**
  (frame count × frame period), *not* the changed area. Targeting fewer pixels
  does **not** speed up the refresh; it only reduces SPI data (a couple ms).
- **Incremental drawing** cut the per-update Python draw from ~90–170 ms (it
  scaled with bar height!) down to ~20–30 ms. This was the only latency win
  that mattered.
- **Custom fast waveforms are a dead end on this panel.** A custom LUT (`0x32`)
  with fewer frames did go faster (~395 ms at 6 frames) but caused **whole-screen
  ghosting/flicker** — the analog power-cycle + VCOM behavior per update can't be
  contained by the gate window, and the speedup was modest (it's frame-*rate*
  limited, ~15 Hz). Avoiding the global disturbance would need scope-level
  VCOM/voltage tuning. Not worth it for a volume indicator.
- Net: **~525 ms/clean update is the practical floor** here. The trade-off for
  e-paper's zero-power hold + sunlight readability.

If sub-100 ms / fluid motion is ever required, that's a different display tech
(small OLED/LCD), not a tuning problem.
