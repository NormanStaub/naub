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
  mixer.py     the mixer screen: 3 columns, 8-band dot meters, the mode bar
  drawer.py    the app switcher: a dropdown over the meters (pure Python too)
  media.py     transport keys over USB HID -- no library, no host software
  ccfont.py    C&C Red Alert font (TINY h8 full ASCII, SML h16, BIG digits h27)
demos/    ready-to-flash examples (each becomes code.py) + generated data
  demo_mixer.py       the mixer screen + the app-switcher dropdown  <- the UI
  demo_app_icons.py   open-app icons in 4-plane FRC grayscale (static screen)
  demo_gray_modes.py  8 grayscale techniques on one knob (the experiment)
  demo_ball.py        bouncing ball, dirty-span updates (refresh/motion test)
host/     PC-side tools (Python 3 + Pillow, pyserial optional)
  deploy.py       python deploy.py mixer|icons|gray|ball -> copies to K:, watches serial
  frc.py          THE reusable piece: ink levels / PIL image -> FRC bitplanes
  naub_bridge.py  streams real audio to the device; applies knob moves back
  appmeter.py     per-app audio level / volume / mute from WASAPI
  appscan.py      enumerate open apps + extract their icons (Windows)
  gen_switcher.py regenerates demos/switcher_data.py (movable per-tier icons)
  gen_appicons.py regenerates demos/appicons.py (one baked screen)
  gen_grad.py     regenerates demos/graygrad.py (gradient planes)
  gen_font.py     TTF -> ccfont.py (extract repo-root c_c_red_alert_inet.zip
                  into host/font_src/ first)
  preview_mixer.py / preview_drawer.py / preview_gray.py      previews
  gdifont.py      render text with Windows GDI -- reaches BITMAP (.FON) faces
  preview_fonts.py  candidate faces at panel scale -> fonts_preview.png
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
| `drawer.compose()` open / mid-slide | 0.41 ms / 4.2 ms |
| `mixer.draw_dots()` (24 bands + peak markers) | 1.2 ms |
| whole UI, drawer open and scrolling | 390 fps (97 Hz gray cycle) |
| whole UI, mixer screen, meter every frame | 160 fps |
| free RAM with mixer + switcher loaded (8 apps, 2 tiers) | 358 kB |

Keep hot loops allocation-free (prebuilt page commands, `make_views()`
memoryviews, reused buffers) — GC pauses show up as visible flicker. The trap
worth naming: **a bytearray slice on the right of an assignment allocates a
copy.** `fb[o:o+128] = pan[p*128:(p+1)*128]` measured 4.6 ms for one drawer
frame; hoisting the right-hand side into prebuilt memoryviews took the same
work to 0.41 ms. Slice the *destination*, never the source.

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
what that app is playing, over a two-page mode bar.

```
| ####----- | ######--- | ########## |  volume bar    page 0
| CHROME    | MUSIC     | SPOTIFY    |  name, marquee page 1
| -   - -   | -     -   |            |  7 bands per column, standing on
| :   : :   | :     -   |            |  the floor and bouncing UP, each
| : : : : : | : : : : : |            |  with its own peak marker riding
|------------------------------------|  its leading edge   pages 2-6
| APP CTRL |||       SYS. #########   |  mode bar      page 7
```

**Both text bars are exactly one page tall**, each carrying its rule on the row
that divides it from the meter: the names bar has its rule at the bottom of its
own page (y15), the mode bar takes the last row of the meter band (y55). That
is why `draw_dots` clears the meter's final page *to* the rule — the line above
the mode bar then costs nothing. The mode bar's label is pushed down one row
(`FOOT_SHIFT`) so it clears that rule, mirroring the 1 px the names bar leaves
above its own.

### Controls

| input | action |
|---|---|
| ENC1-3 turn | that column's app volume |
| ENC1-3 click | open the app switcher for that column; click again to assign |
| ENC1-3 hold 0.6 s | mute / unmute that column |
| ENC4 turn / click | system volume / system mute |
| BTN1 / BTN2 | previous / next mode |
| BTN3 / BTN4 / BTN5 | `\|<<` `>\|\|` `>>\|` — media transport, over HID |

BTN3/4/5 are the bottom row of keyswitches (SW7/8/9, GP20/21/22), left to
right. They go out as **USB HID Consumer Control**, not over the line protocol,
so they work with no host software running — see `device/media.py` and
PROTOCOL.md §11. Verified with a `WH_KEYBOARD_LL` hook: all three land as
`MEDIA_PREV_TRACK` / `MEDIA_PLAY_PAUSE` / `MEDIA_NEXT_TRACK`.

### The mode bar

Two pages tall, so the text has room to breathe, and it names the current
**mode** rather than a fixed preset. The `|||` between the label and `SYS.` is
not decoration — it is the position in the mode list:

```
   APP CTRL |||          mode 1 of 4
 | EQ        ||          mode 2
|| CUSTOM 1   |          mode 3
||| CUSTOM 2             mode 4
```

There is always one tick fewer than there are modes, and the current mode
splits them: ticks left of the label are the modes behind you, ticks right the
ones ahead. Flipping a mode redraws the bar and that is the entire transition —
there is nothing to animate, which is why it feels instant.

**The ticks sit on a fixed lattice** and never move to accommodate the text —
a position indicator that shifted with the length of the name would indicate
nothing. Think of it as `n` slots in a row: `n-1` hold a tick and one holds the
label, and advancing a mode swaps the label with the tick on its right. The
label is left-justified in a field as wide as the longest mode name, so exactly
two things move: one tick hops to the other side, and the label's leading edge
steps right by exactly the space that tick now occupies (`TICK_PITCH`, 3 px).
With the current list every tick is on x = 1, 4, 7 (behind) or x = 45, 48, 51
(ahead), whatever the label says.

**That hop is animated, and the animation falls out of the geometry** rather
than being bolted on. Every tick has exactly two homes, one per side of the
label, and is on the left one precisely when it is behind the current mode. So
give each a crossed-ness from 0 (right) to 1 (left), ease it toward its target
and place it between its two homes: one mode step wipes a single line across
the label, and a wrap from the last mode to the first sweeps all of them back
at once. The label's own x is the **sum** of those crossed-nesses times the
pitch, so it slides in step with the lines pushing it — there is no separate
label tween to keep in sync. `Mixer.set_mode()` starts it, `step_foot()` runs
one frame and returns False when it settles; 15 frames at the 16 ms tick, so
about a quarter of a second.

Only **APP CTRL** is implemented. `EQ` has its verbs defined (PROTOCOL.md §10)
but no V2 screen yet, and `CUSTOM 1/2` are placeholders; flipping to them moves
the ticks and the label and leaves the mixer up. The switcher opens only in
APP CTRL, since that is the mode it belongs to.

The mode bar is the one thing on this screen that is **not** page-aligned: its
text and its system-volume bar straddle pages 6 and 7 so they sit centred in
the taller bar, which costs a shift per column byte. That is fine — it is
redrawn only when something in it changes, never in the frame loop.

### What Windows can and cannot tell us

- **Per-app level: yes.** Every WASAPI session exposes
  `IAudioMeterInformation::GetPeakValue()` — a real amplitude trace of that app
  alone, at ~0.05 ms per session, so 100 Hz polling is free. pycaw does not
  wrap the interface; `host/appmeter.py` declares it.
- **But the peak is the *raw stream*.** Measured on this box: it does not move
  when the session volume changes, nor when the endpoint volume does — it is
  what the app rendered, before anybody's fader. A meter built on it alone
  would sit still while you turn the knob, which is not what a mixer meter
  means. `levels()` therefore scales by the session's **own** volume and reads
  zero when muted. The **system** volume is deliberately left out: that is not
  this app getting quieter.
- **Curve first, fader second.** The gamma is applied to the peak and the
  volume multiplies the result, not the other way round. The curve exists to
  lift quiet program material into view; running the fader through it too would
  compress the knob (half volume would read as 0.73 of full height instead of
  half). Verified against a steady tone: 0.75 / 0.50 / 0.25 on the fader give
  exactly ×0.75 / ×0.50 / ×0.25 on the meter, mute gives zero, and moving the
  system fader changes nothing.
- **Per-app spectrum: no.** There is no per-session tap — WASAPI loopback
  captures the endpoint *mix*. A per-app FFT would need code injected into
  every process. So the band shape is **faked** from the one amplitude we do
  get — see "The meter" below.

Peak is linear amplitude, which looks dull on a small panel because speech and
music sit low in the range, so `appmeter.levels()` applies a gentle curve
(`curve=0.45`); pass `1.0` for raw.

### The meter

Each column is a **mixer meter with 7 bands**. Every band stands on the floor,
rises with its share of the level, and carries a short line at its leading edge
— the **peak hold**, which jumps straight to any new peak and then settles back
down smoothly, detaching from the falling band. The dot texture is anchored to
the bottom too, so the dots stay put as a band grows instead of crawling.

- **Ballistics** are fast-attack / slow-release on both (`mixer.FALL`,
  `mixer.PEAK_FALL`, tuned for a 16 ms tick); the peak falls ~6x slower than
  the band. That asymmetry is what makes a meter look alive rather than jittery.
- **The run is centred in its column**, which constrains the numbers: it is
  `(NBAND-1) * BAND_PITCH + BAND_W` wide and the leftover has to be *even* for
  the margins to match. **7 bands at pitch 6** span 38 of the 42 px column, so
  the margin is exactly 2 either side. (8 bands at pitch 5 span 37, leaving an
  odd 5 — 2 px one side and 3 the other, which is what made the meter look
  adrift in its column.)
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
`n=4` if you reintroduce gray elements. The switcher's icons are still FRC
grayscale, so `demo_mixer.py` re-reads `len(views)` whenever the dropdown opens
or closes: 1 plane for the mixer, 4 while the drawer is out.

### Why it is cheap to animate

- every possible band height is precomputed as page byte masks (`BAND_PAGES`),
  so drawing a band is a few `|=` into a per-column scratch buffer that is
  slice-copied straight into the framebuffer;
- names render once into a one-page `gfx.text_strip()` and the **marquee is a
  byte-slice window** into it, so scrolling a long name is free — this is why
  `ccfont.TINY` is exactly 8 px tall;
- the scratch overwrites every byte of a column, so there is no clear pass, and
  the separators sit in the 1 px gaps *between* columns where the hot path
  never touches them — chrome is drawn once, at construction.

A full meter redraw (24 bands + peak markers) costs 1.2 ms against a 2.7 ms
single-plane frame, and it only runs on the 16 ms tick.

### The font

`ccfont.TINY` is **Small Fonts at height 8** — a Windows *raster* face, drawn by
hand at fixed pixel sizes, not an outline scaled down. Cropped from row 1 it
gives 5 px caps inside one page and renders SPOTIFY in 29 px, and it is drawn
with `spacing=0` (`mixer.SPACING`).

That distinction is the whole game at this scale. An outline face rasterised to
a 5–6 px cap height loses strokes and joins others no matter how well hinted —
Tahoma's `A` arrives with no crossbar — while a raster face has every pixel
where its designer put it. PIL/FreeType **cannot open a `.FON` at all**, which
is why the first survey never considered them; `host/gdifont.py` renders them
through GDI instead, and `gen_font.py --list` shows which faces are raster
(`gdi`) and which are outlines (`ttf`).

A raster face must be asked for a height it actually ships, or GDI scales a
strike it does not have and the problem comes straight back; `gen_font.py`
refuses rather than doing that. Small Fonts ships 5, 6, 8, 10–13 and 16.

TINY is generated **all-caps** (lowercase from its uppercase form): eight rows
will not hold ascenders, caps and descenders together at a legible cap height,
and the UI is uppercase throughout anyway.

```
python host/gen_font.py --list          # what faces are on offer
python host/gen_font.py --face tahoma   # ... try another
python host/preview_fonts.py            # all of them, side by side, at scale
```

```
python host/appmeter.py             # see the live per-app levels
python host/preview_mixer.py        # WYSIWYG check; --live drives it from real audio
python host/deploy.py mixer
```

## Live audio: the host link

**The bridge is not optional, and it is meant to stay running.** The panel is a
display; the host is the thing that talks to WASAPI. Without
`host/naub_bridge.py` there are no levels to show *and no way to apply a knob* —
turning one moves the on-screen bar and nothing else.

```
python host/deploy.py mixer     # ships device/boot.py too
#  ... FULLY POWER CYCLE the board (unplug/replug) ...
python host/naub_bridge.py      # leave this running
python host/naub_bridge.py --ports   # if it cannot find the data port
```

- **The levels are real, and the device will not pretend otherwise.** With no
  host talking, the meters sit **flat** rather than inventing a signal: a meter
  that moves when nothing is playing is a liar, and it hides the one thing you
  need to know, which is that the bridge is not running. `demo_mixer.SIMULATE`
  turns the fake envelope back on for UI work. The device also drops back to
  flat after `LIVE_S` (1 s) without a `meter` line, so a bridge that dies shows
  up immediately instead of freezing the meters at whatever was last playing.
- **Only the band *shape* is invented** — see "The meter" above. The amplitude
  driving all 7 bands is the app's real WASAPI peak.
- **The bridge reconnects by itself.** Every `deploy.py` reloads the board and
  drops the CDC link; a bridge that died there would leave the panel dark and
  silently stop applying knob moves, which looks exactly like a firmware bug and
  is not one. It re-finds the port, reopens it and re-pushes the whole state
  (`Bridge.resync`). `--once` opts out.

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
- **Protocol** is V1's line protocol (PROTOCOL.md §11) plus four verbs:
  `meter <v0> <v1> <v2>` (column levels), `apps <A,B,C>` (every app with a live
  session — the switcher's list) and `ameter <v0> <v1> ...` (one level per entry
  in it), all pushed at `--rate`; `pick <ch> <APP>` comes back when the switcher
  assigns one. Knob moves still come back as `vol <ch> <0..100>` and
  `mute <ch> <0|1>`; channel 3 is the system/master, matching V1.
- **The host owns the column names, except where you pinned them.** It fills
  the columns from the most active sessions, but a `pick` pins that column and a
  pinned column is never reassigned by the host's churn
  (`naub_bridge.Bridge.columns()`). This was the open question left by the
  previous pass; the switcher is what closed it.

## The app switcher (`drawer.py`)

Not a separate screen: a **dropdown** that slides down out of one column's name
slot and covers the meter band. The volume bars and the mode bar never move, so
you never lose your place — and the column being edited is the one whose name
and rule are *missing*.

```
| ####----- | ######--- | ########## |  volume bars stay put   page 0
|           |  MUSIC    |  SPOTIFY   |  edited column blanked, rule gapped
|             SPOTIFY                |  selected name, full width  panel p0
|   [ic]     [ ICON ]     [ic]       |  icon shelf, TOP-aligned    panel p1-4
| o o O o o  [ ICON ]     .. .. |    |  counter and level FLANK it panel p4
|------------------------------------|
| APP CTRL |||       SYS. #########   |  mode bar stays put     page 7
```

Four pages is not much, and the layout is what buys the icons their size back —
24 px focused, 16 px either side, in a band only three pages tall:

- **the name gets the whole width.** It is the *selected app's* name, not a
  column heading, so nothing has to share the row and a long name has all
  128 px before it is trimmed;
- **the shelf is top-aligned, not bottom-aligned.** Every tier starts on the
  same page and the focused one simply reaches further down — that is what lets
  the focus be 32 px while its neighbours are 24 px;
- **the counter and the level flank the focused icon** rather than sitting
  under it, in the very row the tall icon is using. A row of their own would
  have cost the shelf a quarter of its height. `NEAR` is kept tight (0.18) so
  the 32 px tier never reaches either of them: at the tier edge the icon spans
  x 40–88, against a counter ending at 40 and a level starting at 88. The last
  row of that page is the rule the mode bar sits under, laid across the full
  width *after* everything else, so it never breaks where the tall icon stands
  — the icon reads as standing on the line.

- **The counter is honest.** One marker per app with a *live audio session*
  (`appmeter.active()`), filled at the current selection. Nothing playing means
  no markers — the dropdown still opens (a dead gesture is worse than an empty
  shelf), it just has nothing to assign. It drops from 5 px rings to 3 px
  markers when there are more apps than fit beside the icon.
- **The bottom row meters the selection, not the column.** It follows the
  scroll, so you see what you are about to assign. Same fast-attack /
  slow-release ballistics as the mixer's bands, with a peak tick on the leading
  edge.
- **Page-aligned size tiers.** `TIERS = ((32,1),(24,1))` — both a multiple of
  8 and starting on the same page, so an icon is `fb[o:o+w] = src[s:s+w]` with
  no bit shifting. The tier comes from the *fractional* distance to the focus,
  so easing `pos` toward an integer target produces the zoom for free.
  `PITCH = 42` puts exactly three icons on screen.
- **The blocks must match `TIERS`.** `Drawer` checks their length at
  construction and raises a clear error naming `gen_switcher.py`, because the
  failure otherwise is horrible: on the device `_blit` runs off a short block
  and raises `IndexError` from inside the frame loop, while on CPython the same
  short slice assignment silently **resizes** the destination bytearray — so
  the PC preview looks perfect and only the panel breaks. `deploy.py` now ships
  `switcher_data.py` with every deploy for the same reason.
- **The wipe carries a rule on its leading edge** — the last revealed row is
  forced solid while the panel is moving, so it reads as a surface sliding over
  the meters rather than as content appearing out of nowhere.
- **Apps with no icon get a placeholder** — a rounded frame with the app's
  initial. The session list and the icon catalogue come from different places
  (apps making noise vs. apps with a window), so a mismatch is normal.
- **The mode bar is untouched** while the drawer is out. It already reads
  `APP CTRL`, which is the mode the switcher belongs to.

The panel composites over the live mixer frame: rows above the sliding edge are
the panel, rows below it are still the running meters, merged byte-wise on the
one page the edge cuts through. Measured **0.41 ms** to compose an open panel
and 4.2 ms on the frames where the edge is mid-page. Measured on hardware over
three open/scroll/close passes: **390 fps, 97 Hz of gray cycle**, steady.

Regenerate the icons for whatever you have open, then flash:

```
python host/gen_switcher.py      # -> demos/switcher_data.py (5 kB, 1.3 kB of pixels)
python host/preview_drawer.py    # WYSIWYG check; --live drives it from real audio
python host/deploy.py mixer
```

`drawer.py` imports nothing but `gfx`, so `preview_drawer.py` drives the exact
code the device runs — always preview before flashing.

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
- Serial console (COM9): treat as **read-only for data.** Host->device writes
  were found to hang during V1 bring-up, so nothing streams over it — that is
  what the CDC *data* port is for. Two control bytes did get through in V2
  testing, though, and they are worth knowing about (see below).
- **A crashed `code.py` stops auto-reload.** An unhandled traceback drops
  CircuitPython to the REPL, and from there copying a new `code.py` changes
  nothing: the console goes quiet and the board looks wedged. Sending Ctrl-C
  then Ctrl-D over COM9 soft-reboots it and re-runs `code.py` — that recovered
  it without a replug. `tmp/reboot.py` in the job scratch does exactly this;
  keep `write_timeout` short in case the write does hang.
- If the ports enumerate but refuse to open right after a physical replug
  (`PermissionError: Access is denied`), that is Windows still tearing down the
  old handles. Wait a few seconds rather than assuming a fault.
