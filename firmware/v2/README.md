# naub V2 firmware toolkit

Everything learned from the display/grayscale/motion bring-up experiments
(2026-09, Windows host), packaged as reusable modules for building the real
UI. CircuitPython 10.3.0 on the Waveshare RP2350-Tiny.

```
device/   modules that live on the CIRCUITPY drive (pure CircuitPython)
  st7565.py    ST7565R driver: full-frame, partial-span, FRC-friendly
  naubhw.py    board wiring: display, backlight, 4 encoders, 9 switches
  gfx.py       1-bit page framebuffer + text/icon drawing (runs on PC too)
  ccfont.py    C&C Red Alert bitmap font (SML full ASCII h16, BIG digits h27)
demos/    ready-to-flash examples (each becomes code.py) + generated data
  demo_app_icons.py   open-app icons in 4-plane FRC grayscale  <- UI starting point
  demo_gray_modes.py  8 grayscale techniques on one knob (the experiment)
  demo_ball.py        bouncing ball, dirty-span updates (refresh/motion test)
host/     PC-side tools (Python 3 + Pillow, pyserial optional)
  deploy.py       python deploy.py ball|gray|icons  -> copies to K:, watches serial
  frc.py          THE reusable piece: ink levels / PIL image -> FRC bitplanes
  gen_appicons.py regenerates demos/appicons.py from currently-open apps
  gen_grad.py     regenerates demos/graygrad.py (gradient planes)
  gen_font.py     TTF -> ccfont.py (extract repo-root c_c_red_alert_inet.zip
                  into host/font_src/ first)
  preview_gray.py / draw_logos.py / gen_icon.py   previews & binary icons
```

## Display: ST7565R (EA DOGM128W-6, 128x64 STN)

Pins: A0=GP8 CS=GP9 SCL=GP10 SI=GP11 RST=GP12, backlight FET on GP13 (PWM).
Init = EA "single supply 3.0/3.3 V" sequence (see `st7565.py`). Config knobs
on the constructor / `naubhw.display()`:

| knob | default | notes |
|---|---|---|
| `baudrate` | 8 MHz | verified clean on hardware; 4 MHz also fine |
| `contrast` | 31 | user-tuned for FRC grays at BL 80% (26 tested at BL 100%) |
| `orient_180` | True | panel is mounted upside-down in the case |
| `col_offset` | 0 | this panel needs none (some 132-col clones need 4) |

Framebuffer: `bytearray(128*8)`, `index = page*128 + x`, bit `1<<(y&7)`,
LSB = top. `gfx.py` is pure Python and runs identically on CPython, so PC
previews are pixel-exact (WYSIWYG workflow — always preview before flashing).

### Measured performance (RP2350-Tiny, CircuitPython 10.3.0)

| operation | rate |
|---|---|
| full-frame `show_pages()` @ 4 MHz | ~337 fps |
| full-frame `show_pages()` @ 8 MHz | ~554 fps |
| partial `span()` (16px sprite, 2 pages) @ 8 MHz | ~2200 updates/s |

Keep hot loops allocation-free (prebuilt page commands, `make_views()`
memoryviews, reused buffers) — GC pauses show up as visible flicker.

## Grayscale: 4-plane FRC (the picked recipe)

The ST7565R has no hardware grayscale, but the STN panel's slow LC response
(tens of ms) acts as a temporal low-pass: cycle N bitplanes fast and a pixel
on in k of N frames reads as gray k/N. `demo_gray_modes.py` compares 8
techniques; **the winner on hardware was "GRAD 4F": N=4 planes (5 levels),
Bayer-dithered residual, phase rotated by (x+y)%4, contrast 31.** At 8 MHz
that cycles at ~138 Hz — a faint shimmer/static remains (the user likes it as
a texture; per-pixel random phase would randomize it, untested). 6 planes
(7 levels) shimmer more; 2 planes are steadiest but only 3 levels.

Encode on the PC with `host/frc.py`:

```python
import frc
planes = frc.encode_image(pil_image, n=4)        # or frc.encode(ink_rows)
frc.emit_module('demos/mydata.py', planes)       # device-importable bytes
frc.avg_image(planes).save('preview.png')        # what the panel will show
```

Display on the device (see `demo_app_icons.py` — this loop IS the UI core):

```python
views = [disp.make_views(fb) for fb in mydata.PLANES]
while True:
    disp.show_pages(views[i]); i = (i + 1) % len(views)
    # ...poll encoders between frames...
```

`frc.encode(..., ox=, oy=, planes=)` composites into existing planes with
screen-global dither coordinates, so regions tile seamlessly — build mixed
screens from parts.

## Motion

`demo_ball.py`: dirty-span partial updates hit ~2200 updates/s, so bandwidth
is never the limit — **the LC response is**. Fast motion motion-blurs/ghosts
(user-confirmed at 8 MHz: "ok albeit some ghosting"). Rule of thumb: keep
animated elements under ~300-500 px/s or embrace the smear. Partial updates
leave enough budget to cycle FRC planes AND move a sprite in the same loop.

## Inputs (all four encoders verified on hardware)

`naubhw.encoders()` -> ENC1 top-left, ENC2 top-middle, ENC3 top-right,
ENC4 lower-right. Non-negotiable lessons baked into `naubhw.py`:

- **`divisor=2`**, not the rotaryio default 4 — the PEC11L-4215F-S0015 is
  30-detent/15-PPR; divisor 4 skips every other detent.
- **GP16 is spent** (module's onboard WS2812). **GP17-GP23 are back-pad pins
  absent from `board.*`** — use `microcontroller.pin.GPIOxx` (ENC4 A/B/SW,
  ENC3_SW, BTN3-5). Already handled in `naubhw.py`.
- All switches/encoders ride internal pull-ups; there are deliberately no
  discrete pull-ups on the board (PCB_DESIGN.md 2.1).

## Host <-> device workflow (Windows)

- CIRCUITPY mounts as a drive (K: here) and read/write **works** on Windows
  (it did not on macOS — see BRINGUP_LOG.md). This is the deploy path.
- Copy order: support modules first, **code.py last** — every write triggers
  auto-reload; only the last one should run. `deploy.py` does this.
- Serial console (COM9): **read-only.** Host->device writes hang/time out on
  this board. Listen for prints/tracebacks; never write. If opening the port
  manually, set `write_timeout` and only ever `read()`.
