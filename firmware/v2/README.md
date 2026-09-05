# naub V2 firmware toolkit

Everything learned from the display/grayscale/motion bring-up experiments
(2026-09, Windows host), packaged as reusable modules for building the real
UI. CircuitPython 10.3.0 on the Waveshare RP2350-Tiny.

```
device/   modules that live on the CIRCUITPY drive (pure CircuitPython)
  st7565.py    ST7565R driver: full-frame, partial-span, FRC-friendly
  naubhw.py    board wiring: display, backlight, 4 encoders, 9 switches
  gfx.py       1-bit page framebuffer + text/icon drawing (runs on PC too)
  carousel.py  app-switcher carousel widget (pure Python -> previews on PC)
  ccfont.py    C&C Red Alert bitmap font (SML full ASCII h16, BIG digits h27)
demos/    ready-to-flash examples (each becomes code.py) + generated data
  demo_switcher.py    scrollable app switcher   <- the first real UI screen
  demo_app_icons.py   open-app icons in 4-plane FRC grayscale (static screen)
  demo_gray_modes.py  8 grayscale techniques on one knob (the experiment)
  demo_ball.py        bouncing ball, dirty-span updates (refresh/motion test)
host/     PC-side tools (Python 3 + Pillow, pyserial optional)
  deploy.py       python deploy.py ball|gray|icons|switch -> copies to K:, watches serial
  frc.py          THE reusable piece: ink levels / PIL image -> FRC bitplanes
  appscan.py      enumerate open apps + extract their icons (Windows)
  gen_switcher.py regenerates demos/switcher_data.py (movable per-tier icons)
  gen_appicons.py regenerates demos/appicons.py (one baked screen)
  gen_grad.py     regenerates demos/graygrad.py (gradient planes)
  gen_font.py     TTF -> ccfont.py (extract repo-root c_c_red_alert_inet.zip
                  into host/font_src/ first)
  preview_switcher.py / preview_gray.py / draw_logos.py / gen_icon.py  previews
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
| `carousel.render()` (5 icons, 4 planes) | 1.9 ms (~1 frame) |
| `carousel.set_focus()` (16px label via `gfx.text`) | 16 ms (~2 gray cycles) |
| free RAM with the switcher loaded (8 apps, 3 tiers) | 371 kB |

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

## The app switcher (`carousel.py` + `demo_switcher.py`)

The first real UI screen: a shelf of app icons, the focused one large and
black, its neighbours smaller and grayer, scrolled with the top-left knob.

```
    [ic]   [  ICON  ]   [ic]      icons, bottom-aligned on page 4
           . . o . . .            position rail   (page 5)
              CHROME              app name        (pages 6-7)
```

Two decisions make it cheap enough to animate underneath the FRC cycling:

- **Page-aligned size tiers.** `TIERS = ((40,0),(32,1),(24,2))` — every icon
  size is a multiple of 8 sitting on a page boundary, so drawing one is
  `fb[o:o+w] = src[s:s+w]`, no bit shifting. Horizontal position is free
  either way (x *is* the byte index), so the scroll is a pure byte copy:
  1.9 ms for a whole 5-icon, 4-plane frame.
- **Dimming by plane subset.** An unfocused icon is drawn into only 3 (or 2)
  of the 4 FRC planes, which lowers its duty and therefore its gray. The
  depth cue costs no extra pixel data — see `_KEEP` / `ink_of()`.

Tier and ink are chosen from the *fractional* distance to the focus, so
easing `pos` toward an integer target produces the zoom for free. Measured
on hardware: 552 fps at rest, 517 fps while actively scrolling.

`gfx.text` is the one slow thing (16 ms — per-pixel Python), so
`demo_switcher.py` only redraws the name once the scroll is nearly settled
(`SETTLED`); redrawing it per detent would stall a fast spin.

Regenerate the icons for whatever you have open, then flash:

```
python host/gen_switcher.py        # -> demos/switcher_data.py (~41 kB, 12.8 kB of pixels)
python host/preview_switcher.py    # WYSIWYG check; --anim for the transition
python host/deploy.py switch
```

`carousel.py` imports nothing but `gfx`, so `preview_switcher.py` drives the
exact code the device runs — always preview before flashing.

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
