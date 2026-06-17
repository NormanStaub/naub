# Example: dithered-preview value rendering

The most refined of the bring-up examples. Two encoders, each shown as a vertical
bar with a **live numeric readout** and a **dithered "preview"** of the pending
change, plus encoder press-blobs and a Button 1 toggle. Tuned around e-paper's
~0.5 s refresh so it feels responsive despite the physics.

Builds on `../dual_encoder_blobs/`.

## Hardware

- Raspberry Pi Pico 2 (RP2350), CircuitPython 10.2.x (ARM build)
- 2× Bourns PEC11R rotary encoders (rotation + push switch)
- 1× momentary button (Button 1)
- Waveshare 2.9" e-Paper Module V3 (SSD1680, 296×128)

Wiring (per repo `WIRING.md`): ENC1 A/B/SW = GP0/GP1/GP2, ENC2 A/B/SW =
GP3/GP4/GP5, Button 1 = GP6; display DC/CS/CLK/DIN/RST/BUSY = GP8/GP9/GP10/
GP11/GP12/GP13, VCC = 3V3(OUT). Libs: `adafruit_display_text/` + `adafruit_ticks.mpy`.

## What it demonstrates

**The value-rendering method (the interesting part):**
- **Turning a knob updates live** — both the number (white box overlaid mid-bar)
  and the bar move with every detent.
- The bar shows your **starting level in solid black**, and the **pending change
  as a sparse gray dither** (dot grid). So you always see where you came from,
  and the change previews live.
- **On settle** (knob stopped for `SETTLE_S`), the dithered region **commits to
  solid black**.
- Increase → dither grows above the solid; decrease → solid shrinks and the gap
  up to your original level shows dithered.

**Why this shape:** e-paper does ~0.5 s per refresh and ~2 refreshes/sec max.
Pure "live number, bar on settle" makes the bar land ~1 s after you stop (two
sequential refreshes). Updating bar+number together (one refresh) lands it in
~0.5 s; the dither keeps the original level legible while the change is in flight.

**Other elements:**
- Encoder **press-blobs** (momentary filled circle above each bar while the knob
  switch is held).
- **Button 1 toggle** — a square box that latches on/off each press.
- **Idle-deferred ghost cleanup:** the full-screen refresh that clears partial-
  refresh ghosting only fires after `FULL_AFTER` partials *and* `IDLE_FULL_S`
  seconds of no interaction — so it never flashes mid-use.

## Tunables (top of the draw / loop sections)

- `DITHER_STEP` (2) — preview density: higher = sparser/lighter dots, 2 ≈ 25%.
- `SETTLE_S` (0.125) — idle gap before the bar commits to solid. (Note: values
  below ~0.5 s have little effect — the refresh duration dominates.)
- `FULL_AFTER` (60) / `IDLE_FULL_S` (12) — how rarely the ghost-sweep full
  refresh runs. Rarer = fewer flashes + fewer "black-relax" artifacts, slightly
  more ghosting between cleanups.
- `STEP` (2) — encoder units per detent.

## Notes / findings

- e-paper "relax" artifact: the first partial after a full refresh slightly
  lightens deep blacks, recovering over a few refreshes — inherent to the
  full↔partial waveform density difference, mitigated by rarer full refreshes.
- See `../encoder_bar_epaper/README.md` for the full partial-refresh latency
  characterization (~0.5 s floor, temperature-bound, custom-LUT dead end).
- Button 1 uses the illuminated button's **blue (NO switch) wires** — not the
  red/black LED wires.
