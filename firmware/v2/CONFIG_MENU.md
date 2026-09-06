# Working with the CONFIG menu

How to add settings to the panel's config screen, what the pieces are, and the
handful of rules that will bite you if you ignore them.

The screen lives in `device/config.py`. It is one file, about 460 lines, and it
is deliberately not a widget framework: it is a list of tuples and a renderer
that knows four kinds of value.

---

## 1. The shape of it

```
 page 0    MENU NAV | SCROLL | SELECT     what each top knob does
 pages 1-5 the setting list, 16px rows, scrollbar down column 0
 page 6    OS | SYS | DISP                tabs, the active one inverted
           <ADVANCED DISPLAY              ... or where you are, in a submenu
 page 7    the mode bar                   mixer.py's, copied in by the caller
```

Three knobs, three jobs, and they are labelled across the top of the screen
because that labelling *is* the affordance:

| knob | does | notes |
|---|---|---|
| ENC1 | switches tabs, or backs out of a submenu | clamped at both ends, never wraps |
| ENC1 click | backs out of a submenu | the same thing, for whoever reaches for a click |
| ENC2 | moves the cursor | the window follows it |
| ENC3 | changes the focused value | **live** — no arming, no commit |
| ENC3 click | enters a `PAGE`, flips a `BOOL` | and is where any future one-shot action goes |

There is no edit mode on purpose. A 128×64 panel has nowhere to show *which*
state you are in, and an invisible mode is exactly how someone changes a
setting they did not mean to. Splitting "move" and "change" across two knobs
does the same job with nothing hidden.

**The list scrolls smoothly.** Rows are 16 px — two pages — so a settled window
is five byte-slice copies. While it is easing toward the row the cursor is on,
though, the window sits at offsets that are *not* multiples of 8, and the blit
has to shift bits: the one place this screen leaves the page grid, deliberately
and for about a dozen frames. `Config.step()` does the easing and returns
`True` while it is moving, which is what asks the frame loop for the next
redraw; when it arrives it returns `False` and the screen goes back to pushing
the same bytes.

A tab or submenu change **snaps** instead of easing. Sliding between two
different lists is motion that means nothing.

---

## 2. A setting is a tuple

```python
(name, kind, key, spec, default, unit, art)
```

| field | what it is |
|---|---|
| `name` | the label, upper case (ccfont is all-caps ASCII) |
| `kind` | `ENUM`, `NUM`, `BOOL` or `INFO` |
| `key` | a stable string identifier — see below |
| `spec` | whatever the kind needs |
| `default` | the starting value, in the kind's own terms |
| `unit` | drawn after the value: a string, or a strip of column bytes |
| `art` | `None`, or a tuple of pictures indexed **by the value** |

The four kinds:

| kind | `spec` | value is | shows as | a detent does |
|---|---|---|---|---|
| `ENUM` | tuple of labels | the **index** | the label | ±1 index, clamped |
| `NUM` | `(lo, hi, step)` | the number | `%d` | ± one step, clamped |
| `BOOL` | `None` | `0` / `1` | `ON` / `OFF` | toggles |
| `INFO` | the text | unused | the text | nothing |
| `PAGE` | a nested rows tuple | nothing | a `>` at the right | nothing — click to enter |

**`key` matters more than it looks.** It is how anything outside this file
finds a value. Row order will change — you will insert a setting in the middle
of a tab within the week — and the moment a value is sent to the host or
written to disk, position is the wrong handle. Name it once, in the tuple, and
never index by position outside `config.py`.

---

## 3. Adding a setting

One line in the right tab's tuple:

```python
SYS = (('METER RATE', ENUM, 'meter_rate', ('30', '60', '90'), 1, 'HZ', None),
       ('KNOB STEP',  NUM,  'knob_step',  (1, 10, 1),         4, None, None),
       ('MY SETTING', BOOL, 'my_setting', None,               0, None, None))
```

That is the whole change. The renderer has no special case for it, the cursor
and the scrollbar pick it up, and `cfg.value('my_setting')` works immediately.

Three things to check as you write it:

- **The name has to fit.** With artwork the field is 90 px, without it 122.
  `Config._check()` measures every name at construction and raises with the
  offending one named, so a too-long name fails loudly at import rather than
  drawing over the picture.
- **Values are clipped, not checked.** They change at runtime, so a long value
  string is simply cut off at the field edge. Keep them short; that is what
  `unit` is for.
- **Nothing wraps.** A fast spin should stop at the end of the range, not come
  round again on a setting you were watching.

## Adding a tab

```python
TABS = (('OS', OS), ('SYS', SYS), ('DISP', DISP), ('NET', NET))
```

Tab cells use the mixer's column geometry (`COLX`, `COLW`), which fits **three**
across. A fourth needs a narrower cell layout in `draw_tabs()` — the tab strip
is the one part of this screen that does not scale for free, and that is a
deliberate limit: a config panel that needs six tabs on a screen this size
needs fewer settings, not more tabs.

## Adding a submenu

A `PAGE` row's `spec` is just another rows tuple:

```python
ADV  = (('CONTRAST', NUM, 'contrast', (0, 63, 1), 31, None, None),
        ('BIAS',     ENUM,'bias',     ('1/9', '1/7'), 0, None, None))

DISP = (...,
        ('ADVANCED DISPLAY', PAGE, 'advanced', ADV, 0, None, None))
```

SELECT-click enters it; MENU NAV (turned or clicked) backs out. The row draws a
`>` at its right edge, page 6 becomes an inverted `<ADVANCED DISPLAY` bar while
you are inside, and each page keeps its own cursor and scroll position, so
backing out puts you where you were.

**Pages are flattened at construction.** A tab and a submenu are the same
thing — a list of rows with its own cursor, scroll and values — so they are
built into one flat array of *views* and a `PAGE` row holds its child's index.
Nothing recurses at runtime: entering is an integer assignment and backing out
is a pop. A submenu therefore costs exactly what a tab costs, and nesting one
inside another needs no new code (though on a screen this size, if you want a
third level, the answer is probably fewer settings).

There is no automatic BACK row and no breadcrumb trail beyond the one bar. Both
would cost a row of a five-row window to say what the inverted bar and the
knob labels already say.

## Adding artwork

`art` is a tuple of frames indexed by the value, so the flipbook is `art[val]`
and there is no logic anywhere. Each frame is:

```python
bytes(ROW_P * ART_W)      # 2 pages x 30 columns = 60 bytes, 16px tall
```

Same bit layout as a framebuffer page: `frame[page * ART_W + x]`, bit
`1 << (y & 7)`, LSB at the top of the page. Leave row 0 and row 15 clear or the
picture welds itself to the row divider.

For an `ENUM` the value is the index, so `len(art) == len(labels)`. For a `NUM`
you would need one frame per possible value, which is usually the sign that the
setting wants to be an `ENUM`.

`_angle_art()` builds the view-angle flipbook at import from a dozen lines of
integer maths rather than baking it in a generator — worth copying for anything
parametric, because it is one fewer file to keep in step with the code. Bake it
on the PC (like `gen_switcher.py`) only when the art is drawn rather than
computed.

**Exaggerate.** The view angle is nine frames from 10° to 90°, and a truthful
perspective projection changes by well under a pixel per step at this size. A
flipbook whose frames are identical is not a flipbook.

**`SHADING`'s art is a little waving flag**: a black-to-white ramp, dithered,
inside a band displaced by a sine. The ramp is quantised to that mode's level
count, which is what differs between the four — two levels give a hard edge
across the middle, five give something that passes for a gradient at 30 pixels
wide.

Two details it needed, both found by drawing it wrong first. The band is
**outlined**, because without an outline the flag dissolves wherever the ramp
goes light and reads as a blob fading out rather than a shape. And the outline
is **joined column to column**, because a sine sampled per column steps by more
than a pixel and leaves a dotted trail otherwise. It still cannot show the
*temporal* half of a mode; no still frame can.

## Units

`unit` is drawn after the value and is not part of the formatted text, because
some units are not text:

```python
('BRIGHTNESS',         NUM,  'brightness', (0, 100, 5), 100, '%',  None)
('DISPLAY VIEW ANGLE', ENUM, 'view_angle', ANGLES,      4,   DEG,  ANGLE_ART)
```

A string goes through the font. A tuple of column bytes is taken as already
drawn — which is how a degree sign exists at all, ccfont being ASCII. `DEG` is
the only one so far; add others beside it.

---

## 4. Reading a value, and wiring it to something

```python
cfg.value('view_angle')     # -> '90'   the ENUM's LABEL, not a number
cfg.value('brightness')     # -> 100    a NUM is an int
cfg.value('invert')         # -> False  a BOOL is a bool
cfg.value('firmware')       # -> 'V2-MIXER'
cfg.raw('view_angle')       # -> 8      the ENUM's INDEX, for indexing with
cfg.set('contrast', 25)     # -> True   write one from outside, clamped
```

Note the first one: an `ENUM` gives you its **label string**. Use `raw()` when
what you want is the index — typically to index a table of your own, which is
exactly what the view angle does.

`set()` is for **one setting standing behind another**. It clamps to the row's
own range, so a caller cannot put a row somewhere the knob could not, and it
rebuilds that row's strip. Use it to keep two rows in agreement rather than
letting them describe the panel differently.

Wiring one up happens in `demo_mixer.py`, where the detent is handled, never
inside `config.py`. There is one function for it and it is the only place that
knows about both a setting and a register:

```python
def apply_display(key):
    if key == 'view_angle':                       # a curve over another row
        c = config.VIEW_CONTRAST[cfg.raw('view_angle')]
        cfg.set('contrast', c)
        disp.set_contrast(c)
        return True
    if key == 'contrast':
        c = cfg.value('contrast')
        disp.set_contrast(c)
        cfg.set('view_angle', nearest_angle(c))   # ... and back the other way
    elif key == 'bias':
        disp.cmd(BIAS_CMD[cfg.value('bias')])
    elif key == 'brightness':
        dim.set_full(cfg.value('brightness'), now)
    else:
        return False
    return True
```

called from the two places a value can change:

```python
elif cfg.adjust(d):                    # a detent
    cfg_dirty = True
    apply_display(cfg.focused_key())

key = cfg.focused_key()                # a click -- read the key BEFORE it,
if cfg.click():                        # because entering a PAGE moves the
    cfg_dirty = True                   # cursor somewhere else
    apply_display(key)
```

Note the labels doing double duty as the lookup: `BIAS_CMD['1/9'] -> 0xA2`. The
config module hands back what the row says and the demo turns that into a
command byte, so there is no second table of "real" values to keep in step.

`config.py` draws settings; it does not know what a register or a backlight is.
That is what keeps it importable by the PC preview with no hardware anywhere
near it.

### ADVANCED DISPLAY

`DISP > ADVANCED DISPLAY` is the ST7565R itself — the same registers the V2
bring-up swept by hand to get a picture at all (`bench/stage4_display.py`):

| row | register | what it does |
|---|---|---|
| CONTRAST | `0x81 n` | electronic volume, 0-63 |
| RESISTOR RATIO | `0x20\|n` | V0 divider, 0-7 |
| BIAS | `0xA2` / `0xA3` | 1/9 or 1/7 |
| BOOSTER | `0xF8 n` | 4x / 5x / 6x charge pump |
| INVERT | `0xA6` / `0xA7` | normal or reversed |
| ALL PIXELS ON | `0xA4` / `0xA5` | the bring-up test pattern |
| START LINE | `0x40\|n` | scrolls the RAM window, 0-63 |

They are **live**: turning the knob writes the register, because a calibration
control you have to reflash to try is not one. Every value of every one of them
has been swept on the hardware and the panel survives all of them.

**`DISPLAY VIEW ANGLE` is `CONTRAST`**, and nothing else. Measured on the
fitted panel: **25** looking down at it on a desk, **40** from an extremely
shallow angle, so the nine steps interpolate that range:

| 10° | 20° | 30° | 40° | 50° | 60° | 70° | 80° | 90° |
|---|---|---|---|---|---|---|---|---|
| 40 | 38 | 36 | 34 | 33 | 31 | 29 | 27 | 25 |

The two rows are kept in agreement **both ways**: setting the angle writes the
contrast row, and turning the raw contrast moves the angle row to whatever it
now means (nearest step; ties go to the shallower one). Two controls over one
register that disagreed about its value would be worse than one control.

That is the pattern for any friendly-name-over-raw-value pair — `raw()` to read
the index, `set()` to write the other row, and both directions handled, or the
one you forgot becomes the bug report.

Two can black the panel out — contrast 0, and ALL PIXELS ON. The cursor does
not move when they do, so the way back is the knob you just turned; a power
cycle is the other way back, since nothing here is saved.

---

## 5. Redrawing

| what changed | call | measured |
|---|---|---|
| a value (`adjust`, `click`) | rebuilds that row's strip for you, then `draw_list()` | 3.4 ms |
| the cursor, settled | `draw_list()` | 1.9 ms |
| the cursor, mid-slide | `draw_list()`, shifting bits | 7.7 ms |
| the tab | `render()` | 5.1 ms |

The mid-slide figure is what smooth scrolling costs: about six extra
milliseconds a frame for the ~12 frames a scroll lasts, which still fits inside
the 10.8 ms the frame loop is paced to, alongside the 1.8 ms frame push.

That shift is as cheap as Python gets here. Measured against 256-entry lookup
tables (6.5 ms vs the plain shift's 6.8 — 5%, for 4 kB of tables), `zip` over
two memoryviews (11.0) and `bytes()` over a generator (11.4). The obvious loop
wins; do not be clever with it.

`nav`, `scroll`, `adjust` and `click` all return `True` when something actually
changed, which is what the demo ORs into `cfg_dirty`:

```python
cfg_dirty = cfg.scroll(d) or cfg_dirty
```

Then the frame loop redraws once, at the end, however many knobs moved.

**Nothing on this screen is on a timer**, and it should stay that way. The
mixer and EQ redraw on a 16 ms tick because their meters move; a settings list
changes when a knob does and not otherwise, so between events the loop pushes
the same bytes and the mode holds the full 92.4 Hz.

The scroll is not an exception to that: it asks for its own redraws through
`step()` and stops asking when it arrives. That is the shape any animation here
should take — self-limiting, and started by an input. If you find yourself
wanting a blink or a marquee, price it first: those cost a redraw per tick *for
ever*, to say something the inverted tab and the filled cursor already say.

---

## 6. The rules that will bite you

- **A short slice assignment resizes a `bytearray` on CPython and raises
  `IndexError` on the device.** A strip wider than the field it is copied into
  therefore looks *perfect* in the PC preview and crashes the panel. This has
  cost this project a debugging session already (see `drawer.py`). Clip every
  copy with `min(w, avail)` and let `_check()` catch the ones it can at import.
- **Slicing the source allocates.** `strip[a:b]` on the right of an assignment
  makes a copy; the row memoryviews in `self._mv` exist so `draw_list` never
  does. Slice the destination, never the source.
- **Stay on the page grid.** Rows are 16 px because 16 is two pages, so a
  settled list is five byte-slices. The smooth scroll leaves the grid on
  purpose and pays 7.7 ms a frame for the dozen frames it lasts; a 20 px row
  would pay that on *every* redraw, for ever, which is how a screen that costs
  1.9 ms starts costing 8.
- **The font is 8 px, upper case, ASCII.** `gfx.text_strip` refuses anything
  taller. Missing glyphs are silently substituted with `?`, so check before
  using a symbol — that is why `DEG` is hand-drawn.
- **Time nothing with `time.monotonic()`.** It is a single-precision float of
  seconds since boot whose granularity doubles as uptime doubles (~8 ms after a
  day). This screen needs no clock at all; keep it that way.
- **The mode bar is not yours.** Page 7 belongs to `mixer.py` and is copied in
  by `sync_foot()` in the demo. Do not draw there.

---

## 7. Seeing it without the hardware

```
python host/preview_config.py        # -> host/config_preview.png
```

`config.py` imports nothing but `gfx`, so the preview is pixel-exact — the same
code paths the panel runs, rendered to a PNG at 4x. Add a `snap()` to
`shots()` for whatever state you are working on and look at it before you
flash. Then:

```
python host/deploy.py mixer          # ships config.py with everything else
```

---

## 8. What is deliberately missing

- **Persistence.** Values reset on reload — including the display registers,
  which is deliberate for now: it means no setting can brick the panel across
  a power cycle while the calibration values are still being found. When it is wanted, `key` is the
  handle: a dict of `{key: value}` to NVM or to the host's own state file, and
  a `Config.load(d)` that ignores keys it no longer has — which is exactly why
  the keys are strings.
- **A host protocol.** Same shape: `cfg <key> <value>` both ways, and the
  values already have stable names to carry.
- **An `ACTION` kind** (RESET, FACTORY). It belongs on the ENC3 *click*, never
  on a detent — a detent is what an accidental brush of the panel delivers.
- **Anything wired to the view angle.** It is a drawing. The panel's real
  viewing angle is a bias/contrast setting on the ST7565; whether it is worth
  exposing is a question for after the layout is settled.
