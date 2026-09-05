# naub V2 firmware toolkit

Everything learned from the display/grayscale/motion bring-up experiments
(2026-09, Windows host), packaged as reusable modules for building the real
UI. CircuitPython 10.3.0 on the Waveshare RP2350-Tiny.

```
device/   modules that live on the CIRCUITPY drive (pure CircuitPython)
  boot.py      enables the 2nd USB CDC (data) port -- needs a POWER CYCLE
  st7565.py    ST7565R driver: full-frame, partial-span, FRC-friendly
  link.py      host<->device line protocol over the data port
  naubhw.py    board wiring: display, backlight, 4 encoders, 9 switches
  gfx.py       1-bit page framebuffer + text/icon drawing (runs on PC too)
  carousel.py  app-switcher carousel widget (pure Python -> previews on PC)
  mixer.py     the mixer screen: 3 columns, volume bars, 8-band dot meters
  ccfont.py    C&C Red Alert font (TINY h8 full ASCII, SML h16, BIG digits h27)
demos/    ready-to-flash examples (each becomes code.py) + generated data
  demo_mixer.py       the mixer screen + carousel picker  <- the main UI
  demo_switcher.py    scrollable app switcher   <- the first real UI screen
  demo_app_icons.py   open-app icons in 4-plane FRC grayscale (static screen)
  demo_gray_modes.py  8 grayscale techniques on one knob (the experiment)
  demo_ball.py        bouncing ball, dirty-span updates (refresh/motion test)
host/     PC-side tools (Python 3 + Pillow, pyserial optional)
  deploy.py       python deploy.py mixer|switch|icons|gray|ball -> copies to K:, watches serial
  frc.py          THE reusable piece: ink levels / PIL image -> FRC bitplanes
  naub_bridge.py  streams real audio to the device; applies knob moves back
  appmeter.py     per-app audio level / volume / mute from WASAPI
  appscan.py      enumerate open apps + extract their icons (Windows)
  gen_switcher.py regenerates demos/switcher_data.py (movable per-tier icons)
  gen_appicons.py regenerates demos/appicons.py (one baked screen)
  gen_grad.py     regenerates demos/graygrad.py (gradient planes)
  gen_font.py     TTF -> ccfont.py (extract repo-root c_c_red_alert_inet.zip
                  into host/font_src/ first)
  preview_mixer.py / preview_switcher.py / preview_gray.py    previews
  draw_logos.py / gen_icon.py                                 binary icons
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
| `col_offset` | derived | **must follow the orientation** — see below |

**`col_offset` depends on `orient_180`.** The ST7565R has 132 columns of RAM but
this panel wires only 128 of them, as SEG4..SEG131. With ADC reversed (`0xA1`,
the non-flipped case) column address 0 lands on SEG131, so the window starts at
0; with ADC normal (`0xA0`, the flipped case) it lands on SEG0 and the window
must start at **4**. Hard-coding 0 shifts the image 4px and paints 4 columns of
stale RAM at one edge — invisible on screens with blank margins, glaring on one
that draws edge to edge, which is how the mixer screen finally exposed it.
Leave `col_offset=None` and the driver derives it.

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

## The mixer screen (`mixer.py` + `demo_mixer.py`)

Three app columns, each with a volume bar, a name, and a dot level meter for
what that app is playing; a footer with the preset and system volume.

```
| ####----- | ######--- | ########## |  volume bar    page 0
| CHROME    | MUSIC     | SPOTIFY    |  name, marquee page 1
| -   - -   | -     -   |            |  8 bands per column, standing on
| :   : :   | :     :   |            |  the floor and bouncing UP, each
| : : : : : | : :   : : |            |  with its own peak marker riding
| : : : : : | : : : : : |            |  its leading edge
| PRESET 1  |||  SYS. ############   |  footer        page 7
```

Knobs: the three top knobs are the three columns (turn = volume, click = open
the carousel to pick that column's app, hold 0.6 s = mute); the lower-right
knob is system volume.

### What Windows can and cannot tell us

- **Per-app level: yes.** Every WASAPI session exposes
  `IAudioMeterInformation::GetPeakValue()` — a real amplitude trace of that app
  alone, at ~0.05 ms per session, so 100 Hz polling is free. pycaw does not
  wrap the interface; `host/appmeter.py` declares it.
- **Per-app spectrum: no.** There is no per-session tap — WASAPI loopback
  captures the endpoint *mix*. A per-app FFT would need code injected into
  every process. So the band shape is **faked** from the one amplitude we do
  get — see "The meter" below.

Peak is linear amplitude, which looks dull on a small panel because speech and
music sit low in the range, so `appmeter.levels()` applies a gentle curve
(`curve=0.45`); pass `1.0` for raw.

### The meter

Each column is a **mixer meter with 8 bands**. Every band stands on the floor,
rises with its share of the level, and carries a short line at its leading edge
— the **peak hold**, which jumps straight to any new peak and then settles back
down smoothly, detaching from the falling band. The dot texture is anchored to
the bottom too, so the dots stay put as a band grows instead of crawling.

- **Ballistics** are fast-attack / slow-release on both (`mixer.FALL`,
  `mixer.PEAK_FALL`, tuned for a 16 ms tick); the peak falls ~6x slower than
  the band. That asymmetry is what makes a meter look alive rather than jittery.
- **Height is in pixels, not rows.** A band fills any of 0..40 px, so motion
  moves at the finest step the panel has. The dot texture comes from the *fill
  pattern* (2px lit, 2px clear) rather than from quantising the height.
- **The spectrum is faked**, because the host can only report one amplitude per
  app. Bands get a gentle tilt (`TILT`, lows louder) times a per-band random
  walk that is *smoothed* rather than re-rolled each frame (`NOISE_KEEP`,
  `NOISE_GAIN`) — so they drift and bounce independently instead of flickering
  like static.

Every band height is precomputed as page byte masks (`BAND_PAGES`), so drawing
a band is a few ORs into a per-column scratch buffer that is slice-copied
straight into the framebuffer. The scratch overwrites every byte of a column,
so there is no clear pass, and the separators sit in the 1px gaps *between*
columns where the hot path never touches them — chrome is drawn once.

### Binary, and therefore single-plane

**This screen is drawn pure binary**: every element is fully inked or absent,
because a part-duty area of any size visibly flickers. So the bar's range is a
1px baseline rather than a gray track, and a muted bar is a hollow outline
rather than a dimmer fill.

Being binary means **one framebuffer suffices** (`Mixer(font, n=1)`, the
default): nothing to cycle, so no FRC shimmer at all, and a quarter of the
drawing work. Measured **391 fps — i.e. 391 Hz of real refresh**, against
100 Hz of gray cycle when the same screen was drawn across 4 planes. Pass
`n=4` if you reintroduce gray elements. The carousel still uses 4 planes, so
`demo_mixer.py` re-reads `len(views)` whenever it switches screens.

### Why it is cheap to animate

Same page-grid discipline as the carousel:

- dot rows sit **2 per page** (2 px dot, 4 px pitch), so lighting one is a
  single `|=` on a single byte — 8 dots x 10 rows x 3 columns with no
  per-pixel calls and no cross-page shifting;
- names render once into a one-page `gfx.text_strip()` and the **marquee is a
  byte-slice window** into it, so scrolling a long name is free — this is why
  `ccfont.TINY` is exactly 8 px tall;
- **age is drawn as gray**: older trace rows go into fewer FRC planes
  (`mixer._KEEP`), and a muted column's bar drops to half duty. Same trick as
  the carousel's unfocused icons, no extra pixel data.

Measured with the trace running at ~11 Hz: **516 fps** (vs 552 idle) — the
whole animated screen costs about 7%.

`ccfont.TINY` is rasterized at size 11 and cropped from row 3, giving 6 px caps
inside one page, and is drawn with `spacing=0` (`mixer.SPACING`). Rendering at
a size whose *metrics* are 8 rows yields only 4 px caps (illegible); going
bigger pushes most app names past the 40 px column and forces them all to
marquee.

```
python host/appmeter.py             # see the live per-app levels
python host/preview_mixer.py        # WYSIWYG check; --live drives it from real audio
python host/deploy.py mixer
```

## Live audio: the host link

`demo_mixer.py` runs either way — it simulates levels until a host talks to it,
then switches to real audio. To go live:

```
python host/deploy.py mixer     # ships device/boot.py too
#  ... FULLY POWER CYCLE the board (unplug/replug) ...
python host/naub_bridge.py      # streams real levels, applies knob moves
python host/naub_bridge.py --ports   # if it cannot find the data port
```

- **Why a second port.** Host->device writes on the *console* port hang on this
  board (bring-up finding), so `device/boot.py` enables a second USB CDC
  interface — the data port — and everything the host says goes over that.
  USB descriptors are fixed at enumeration, so boot.py needs a **power cycle**,
  not a reload. Until then `Link.ready` is False and the demo simulates.
- **Finding the port.** Windows reports the interface number inconsistently:
  on this board the console has `location=None` and the data port
  `location='1-10.3:x.2'`, while other setups tag the hwid `MI_00`/`MI_02`.
  `naub_bridge.find_ports()` handles both and otherwise just takes the lowest
  interface as console, highest as data.
- **Protocol** is V1's line protocol (PROTOCOL.md) plus one verb:
  `meter <v0> <v1> <v2>`, the live per-app levels, 0..100, pushed at `--rate`.
  Knob moves come back as `vol <ch> <0..100>` and `mute <ch> <0|1>`; channel 3
  is the system/master, matching V1's channel numbering.
- **The host owns the column names** while connected — it picks the three most
  active sessions. The carousel picker still works, but a manual choice is
  overwritten next time the host's top three changes. Worth revisiting.

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
