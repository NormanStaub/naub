"""The CONFIG screen: settings, in tabs, driven by the three top knobs.

    cfg = config.Config(ccfont.TINY)
    cfg.nav(+1)        # ENC1: OS / SYS / DISP -- or back out of a submenu
    cfg.scroll(+1)     # ENC2: move the cursor down the list
    cfg.adjust(+1)     # ENC3: change the focused setting
    cfg.click()        # ENC3 click: enter a PAGE, or flip a BOOL
    cfg.render()

Layout, top to bottom:

    page 0    MENU NAV | SCROLL | SELECT      what each top knob does
    pages 1-5 the setting list, 16px rows, with a scrollbar down column 0
    page 6    OS | SYS | DISP                 tabs, the active one inverted
              ... or `<ADVANCED DISPLAY`      when you are inside a submenu
    page 7    the mode bar, which belongs to mixer.py and is copied in

**A setting is a tuple, and adding one is a line.**  The renderer has exactly
one place that knows what a kind is (`_fmt`) and the input handler has one
more; everything else works off the tuple.  Tuples rather than objects because
this runs on CircuitPython, where every instance carries a dict, and because
the same module is imported on the PC for pixel-exact previews.

    (name, kind, key, spec, default, unit, art)

`spec` is whatever the kind needs: ENUM a tuple of labels, NUM a (lo, hi, step)
triple, BOOL nothing, INFO the string to show, PAGE a nested rows tuple.  `key`
is a string from the start, so a value can be found by name rather than by
position -- the order of these rows WILL change, and the day one of them is
wired to the host or saved to disk, position is the wrong handle.  `unit` is
drawn after the value: a string, or a hand-drawn strip of column bytes for the
ones ccfont has no glyph for (DEG is the only one so far).  `art` is None or a
tuple of little 16px-tall pictures indexed BY THE VALUE, which is the whole of
the view-angle flipbook: no logic, just `art[val]`.

**Pages are flattened at construction.**  A tab and a submenu are the same
thing -- a list of rows with its own cursor, scroll position and values -- so
they are built into one flat array of "views" and a PAGE row simply holds its
child's index.  Nothing recurses at runtime; entering a submenu is an integer
assignment, and coming back is a pop.

**Rows are 16 px, which is two pages.**  The name on the top line, the marker
and value on the bottom, a picture spanning both on the right -- and, because
16 is a multiple of 8, scrolling by a whole row is still a byte-slice blit.
Every screen here is cheap for that one reason.  Two rows show fully and a
third shows its top half, which is also how you can tell there is more below.

**Nothing here is on a timer.**  The mixer and EQ screens redraw on a 16 ms
tick because their meters move; a settings list changes only when a knob does,
so it redraws on input and the frame loop pushes the same bytes in between.

See CONFIG_MENU.md for how to add to any of this.
"""
import math

import gfx
import qrdata

W = gfx.W
_ZERO = bytes(W)

# ---- layout ----------------------------------------------------------------
PAGE_HEAD = 0
LIST_P0, LIST_P1 = 1, 5
NLIST = LIST_P1 - LIST_P0 + 1     # 5 pages of window
PAGE_TABS = 6
PAGE_FOOT = 7                     # mixer.py's, copied in by the caller

ROW_P = 2                         # pages per row: 16px, name over value
NVIS = NLIST // ROW_P             # rows that show in full (the next shows half)
ROW_H = ROW_P * 8                 # ... in pixels, which is what scrolling uses

# The list scrolls smoothly, so the window lands on offsets that are not
# multiples of 8 and the blit has to shift bits.  That costs -- see step() --
# but only while it is moving, which is a couple of hundred milliseconds.
SCROLL_EASE = 0.30                # fraction of the remaining distance a frame
SCROLL_SNAP = 0.4                 # px: below this, land on it

# The three cells match the mixer's columns exactly, so the header, the tabs
# and every other screen's furniture line up down the whole stack.
COLX = (0, 43, 86)
COLW = 42
SEPX = (42, 85)

RULE = 0x80                       # bottom row of a page: a horizontal rule
SPACING = 0                       # as everywhere else with ccfont.TINY

BAR_X = 0                         # the scrollbar owns columns 0 and 1
BAR_W = 2
NAME_X = 4
MARK_X = 4                        # the row marker, under the name
MARK_W = 5
VAL_X = MARK_X + MARK_W + 3
PAD_R = 2
ART_W = 30
ART_X = W - ART_W - PAD_R

# 5x5, one page tall: an empty ring for a row, filled for the one in focus.
# The filled one is a superset of the ring, so the cursor is an OR over the
# baked strip rather than a redraw of the row.
RING = (0x0E, 0x11, 0x11, 0x11, 0x0E)
DISC = (0x0E, 0x1F, 0x1F, 0x1F, 0x0E)
DEG = (0x02, 0x05, 0x02)          # ccfont has no degree glyph; this is one

HEAD = ('MENU NAV', 'SCROLL', 'SELECT')

# ---- the GITHUB card -------------------------------------------------------
# The QR is drawn at ONE PIXEL PER MODULE, which is not a choice: 37 modules is
# already 37 of the 40 pixel rows this window has, so there is no room to scale
# it and the code has to earn its scannability from contrast instead of size.
# That is also why qrdata carries the 4-module quiet zone rather than leaving it
# to a margin here -- a later layout tweak could eat a margin without anyone
# noticing the code had stopped scanning.
QR_X, QR_Y = 1, 1
# The link, beside it, in the 89 px the QR leaves.  Both halves measure exactly
# 78 px at ccfont.TINY, so they are centred in that space as a block; splitting
# after the last slash of the host is the one break that keeps them equal.
URL_LINES = ('https://github.com/', 'NormanStaub/naub')
TXT_X = 44
TXT_Y = 10                        # ... so the two lines centre on the QR
TXT_PITCH = 10

# ---- what a setting can be -------------------------------------------------
# ENUM  a choice from a tuple of labels; the value is the index
# NUM   an integer with a (lo, hi, step); the value is the number
# BOOL  ON / OFF; the value is 0 or 1
# INFO  read-only, `spec` is the text; a detent does nothing to it
# PAGE  a submenu, `spec` is its rows; SELECT-click enters, MENU NAV backs out
# CARD  a page that is a PICTURE rather than a list.  `spec` is a builder
#       f(font) -> NLIST*W bytes, called once at construction; entering and
#       backing out work exactly as PAGE, and the two knobs that would move a
#       cursor simply have nothing to move.
ENUM, NUM, BOOL, INFO, PAGE, CARD = 0, 1, 2, 3, 4, 5


def _angle_art(n=9, w=ART_W, h=ROW_P * 8 - 2, y0=1):
    """The view-angle flipbook: the panel seen from `n` angles, 10 to 90.

    A trapezoid whose top edge widens and whose height grows until, seen
    head-on, it is a plain rectangle.  The perspective is EXAGGERATED rather
    than true -- at this size a truthful projection changes by well under a
    pixel per step, and a flipbook whose frames are identical is not a
    flipbook.  Built here rather than baked by a generator because it is a
    dozen lines of integer maths and one fewer file to keep in step.
    """
    frames = []
    for i in range(n):
        t = i / float(n - 1)
        hh = int(6 + (h - 6) * t + 0.5)          # 6px at 10 deg, full at 90
        tw = int(14 + (w - 14) * t + 0.5)        # narrow far edge -> square
        x0 = (w - tw) // 2
        x1 = x0 + tw - 1
        y_top = h - hh
        buf = bytearray(ROW_P * w)               # 16 rows; the shape uses 14,
                                                 # so the row's divider rule
                                                 # stays a rule and does not
                                                 # weld itself to the picture
        for x in range(w):
            if x < x0:                            # the left slant
                y = h - 1 - (hh - 1) * x // x0 if x0 else y_top
            elif x > x1:                          # ... and the right
                y = h - 1 - (hh - 1) * (w - 1 - x) // (w - 1 - x1) \
                    if x1 < w - 1 else y_top
            else:
                y = y_top
            for yy in range(y + y0, h + y0):
                buf[(yy >> 3) * w + x] |= 1 << (yy & 7)
        frames.append(bytes(buf))
    return tuple(frames)


# 4x4 ordered dither, for putting a gradient on a panel with two colours.
# Flat, and indexed [(y & 3) * 4 + (x & 3)] -- host/frc.py's nested rows.
BAYER = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)

# This screen runs FRC, so the SHADING patch can show what the app switcher's
# icons actually look like rather than an impression of them.  Everything else
# on it is identical across the four planes; only the patch differs, which is
# why draw_list composes the list ONCE and copies it out.
NPLANE = 4

# Modes whose frame count divides the 4-plane cycle, so each is shown exactly.
# graybars.MODES also has 3-frame variants; a 3-frame pattern inside a 4-frame
# loop repeats one of its frames, which is a different duty and therefore a
# different grey -- a patch that lied about the one thing it is for.
SHADE_PLANES = (1, 2, 4)
SHADE_PHASES = 16                 # of the wave; ~0.9 s to come round
PHASE_MS = 55


ANGLE_ART = _angle_art()
ANGLES = tuple('%d' % (10 * (i + 1)) for i in range(len(ANGLE_ART)))

# DISPLAY VIEW ANGLE *is* a contrast curve, and nothing else.  MEASURED on the
# fitted panel: 25 looking down at it on a desk, 40 from an extremely shallow
# angle, so the nine steps are that range interpolated.  One number, one
# register (0x81) -- the row exists because "the angle I look at it from" is a
# question anyone can answer and "electronic volume" is not.
VIEW_CONTRAST = tuple(int(40 + (25 - 40) * i / (len(ANGLES) - 1.0) + 0.5)
                      for i in range(len(ANGLES)))
VIEW_DEFAULT = len(ANGLES) - 1          # 90 deg -> contrast 25, the default

def _cloth(np, w, band, y0, amp):
    """One shading mode's gradient, as `np` bitplanes of column bits.

    This is `host/frc.py` encode(), transcribed: integer duty, a 4x4 Bayer
    ordered dither on the fractional residual, and the phase rotated by
    (x+y)%n so neighbouring pixels fire on different frames.  Transcribed
    rather than approximated because the whole point of the patch is that it
    shows what the app switcher's icons will look like, and the icons are that
    function's output.

    -> plane -> per-column 16-bit ints, the band sitting at its rest position
    with `amp` rows of headroom above and below for the wave to move into.
    """
    out = []
    top = y0 + amp
    for k in range(np):
        col = []
        for x in range(w):
            g = 1.0 - x / (w - 1.0)                  # ink: black on the left
            d = g * np
            base = int(d)
            v = 0
            for y in range(top, top + band):
                b = base
                if (BAYER[(y & 3) * 4 + (x & 3)] + 0.5) / 16.0 < d - base:
                    b += 1
                if (k - (x + y)) % np < b:
                    v |= 1 << y
            col.append(v)
        out.append(col)
    return out


def _shade_art(modes=SHADE_PLANES, nphase=SHADE_PHASES, nplane=NPLANE,
               w=ART_W, h=ROW_P * 8 - 2, y0=1, amp=2, waves=1.5):
    """A little waving flag per shading mode: the gradient above, inside a band
    displaced by a sine, at `nphase` phases of the wave.

    -> mode -> phase -> plane -> a ROW_P*ART_W frame.

    The wave is a per-column vertical SHIFT of the cloth, which is both what a
    flag does and what makes this cheap: the dither is encoded once per mode
    and every phase after that is thirty shifts, so the whole animation builds
    at import in milliseconds instead of re-dithering 30x14 pixels a frame.

    The band is OUTLINED, and the outline joined column to column.  Without the
    outline the flag dissolves wherever the ramp goes light and reads as a blob
    fading out rather than a shape; without the joining it is a dotted trail
    wherever the sine steps more than a pixel between columns.
    """
    band = h - 2 * amp
    out = []
    for np in modes:
        tex = _cloth(np, w, band, y0, amp)
        phases = []
        for ph in range(nphase):
            # the wave, and the outline that rides on it -- computed once and
            # shared by every plane, because the edge is solid black
            dys = []
            edge = []
            prev = None
            for x in range(w):
                t = 2.0 * math.pi * (waves * x / (w - 1.0) + ph / float(nphase))
                dy = int(math.floor(amp * math.sin(t) + 0.5))
                top = y0 + amp + dy
                lo = top if prev is None else (top if top < prev else prev)
                hi = top if prev is None else (prev if top < prev else top)
                m = 0
                for yy in range(lo, hi + 1):
                    m |= (1 << yy) | (1 << (yy + band - 1))
                dys.append(dy)
                edge.append(m)
                prev = top
            planes = []
            for k in range(nplane):
                src = tex[k % np]
                buf = bytearray(ROW_P * w)
                for x in range(w):
                    dy = dys[x]
                    v = (src[x] << dy) if dy >= 0 else (src[x] >> -dy)
                    v = (v | edge[x]) & 0xFFFF
                    buf[x] = v & 0xFF
                    buf[w + x] = (v >> 8) & 0xFF
                planes.append(bytes(buf))
            phases.append(tuple(planes))
        out.append(tuple(phases))
    return tuple(out)


SHADES = ('1F SPATIAL', '2F TEMPORAL', '4F HYBRID')
SHADE_ART = _shade_art()

# ---- the settings ----------------------------------------------------------
# ADVANCED DISPLAY is the ST7565R itself: exactly the registers the V2 bring-up
# swept by hand to find a picture at all (bench/stage4_display.py) -- the
# electronic volume, the three things that set V0 alongside it, and the two
# whole-panel test modes.  They are LIVE, because a calibration control you
# have to reflash to try is not one.
#
# Nothing here is saved.  A setting that makes the panel unreadable is one
# power cycle from being undone, which is the right trade for a menu whose
# whole job is finding out what a register does.
def _text_at(buf, strip, w, x0, y, limit=W):
    """One 8px text strip at an ARBITRARY y, which may straddle a page.

    _build() never needs this because rows are page-aligned by construction;
    a card is a free drawing, so it does.  Clipped at `limit` rather than
    trusted: a short slice assignment resizes the bytearray on CPython and
    raises IndexError on the device, which is the trap _check() exists for.
    """
    p = y >> 3
    sh = y & 7
    n = min(w, limit - x0)
    for j in range(n):
        v = strip[j] << sh
        o = p * W + x0 + j
        buf[o] |= v & 0xFF
        if sh and p + 1 < NLIST:
            buf[o + W] |= (v >> 8) & 0xFF


def _github_card(font):
    """The GITHUB page: the repo's QR beside the link it encodes.

    Both, not either.  The code is the point on a panel with no keyboard, but a
    QR is unreadable to a person -- if it will not scan, or you are reading the
    panel in a photograph, the text is the only thing that tells you where it
    went.
    """
    buf = bytearray(NLIST * W)
    for y in range(qrdata.N):
        row = qrdata.ROWS[y]
        yy = QR_Y + y
        o = (yy >> 3) * W + QR_X
        b = 1 << (yy & 7)
        for x in range(qrdata.N):
            if (row >> x) & 1:
                buf[o + x] |= b
    for k in range(len(URL_LINES)):
        w, sbuf = gfx.text_strip(font, URL_LINES[k], SPACING)
        _text_at(buf, sbuf, w, TXT_X, TXT_Y + k * TXT_PITCH, W - PAD_R)
    return bytes(buf)


ADV = (('CONTRAST', NUM, 'contrast', (0, 63, 1), 25, None, None),
       ('RESISTOR RATIO', NUM, 'resistor', (0, 7, 1), 7, None, None),
       ('BIAS', ENUM, 'bias', ('1/9', '1/7'), 0, None, None),
       ('BOOSTER', ENUM, 'booster', ('4X', '5X', '6X'), 0, None, None),
       ('INVERT', BOOL, 'invert', None, 0, None, None),
       ('ALL PIXELS ON', BOOL, 'all_on', None, 0, None, None),
       ('START LINE', NUM, 'start_line', (0, 63, 1), 0, None, None))

DISP = (('DISPLAY VIEW ANGLE', ENUM, 'view_angle', ANGLES, VIEW_DEFAULT,
         DEG, ANGLE_ART),
        ('BRIGHTNESS', NUM, 'brightness', (0, 100, 5), 100, '%', None),
        ('SHADING', ENUM, 'shading', SHADES, 2, None, SHADE_ART),
        ('DIM AFTER', ENUM, 'dim_after', ('30S', '1MIN', '5MIN', 'NEVER'),
         1, None, None),
        ('ADVANCED DISPLAY', PAGE, 'advanced', ADV, 0, None, None))

SYS = (('METER RATE', ENUM, 'meter_rate', ('30', '60', '90'), 1, 'HZ', None),
       ('KNOB STEP', NUM, 'knob_step', (1, 10, 1), 4, None, None),
       ('HOLD TO MUTE', BOOL, 'hold_mute', None, 1, None, None),
       ('MEDIA KEYS', BOOL, 'media_keys', None, 1, None, None),
       ('GITHUB', CARD, 'github', _github_card, 0, None, None))

OS = (('HOST LINK', INFO, 'link', 'USB CDC', 0, None, None),
      ('AUTO CONNECT', BOOL, 'autoconnect', None, 1, None, None),
      ('KEEP COLUMNS', BOOL, 'keep_columns', None, 1, None, None),
      ('FIRMWARE', INFO, 'firmware', 'V2-MIXER', 0, None, None))

TABS = (('OS', OS), ('SYS', SYS), ('DISP', DISP))
START_TAB = 2                     # DISP: where the real settings live

# fields of a setting tuple, named so nothing reads r[5] and hopes
F_NAME, F_KIND, F_KEY, F_SPEC, F_DFLT, F_UNIT, F_ART = 0, 1, 2, 3, 4, 5, 6


def _unit_strip(font, unit):
    """A unit as column bytes.  Strings go through the font; a tuple is taken
    as already drawn, which is how a degree sign exists at all -- ccfont is
    ASCII and has no glyph for one."""
    if not unit:
        return None
    if isinstance(unit, str):
        return gfx.text_strip(font, unit, SPACING)[1]
    return unit


def _edge(r):
    """Where a row's text has to stop: short of the picture if it has one."""
    return ART_X - 2 if r[F_ART] else W - PAD_R


def _animated(art):
    """Is this row's art a per-plane animation rather than one still?

    Told apart by shape: a still is `art[value] -> bytes`, an animation is
    `art[value][phase][plane] -> bytes`.  One test, in one place, rather than a
    seventh field in every row tuple that only one row would ever use."""
    return art is not None and not isinstance(art[0], (bytes, bytearray))


class Config:
    def __init__(self, font, tabs=TABS, n=NPLANE, tab=START_TAB):
        self.font = font
        self.planes = [gfx.new() for _ in range(n)]
        self._ink = tuple(range(n))
        self.tabs = tabs
        # A view is a list of rows with its own cursor, scroll and values.
        # Tabs are views 0..ntabs-1; submenus are appended after them and a
        # PAGE row holds its child's index.  Flat, so nothing recurses at
        # runtime and a submenu costs exactly what a tab costs.
        self.vrows = []
        self.vtitle = []
        self.vvals = []
        self.vcur = []
        self.vtop = []
        self.vchild = []
        self.vnstrip = []
        self.vvstrip = []
        self.vunit = []
        self.vstrip = []
        self.vmv = []
        self.vcard = []           # a view is either a list of rows or a card
        # BREADTH FIRST, and it matters.  nav() selects a tab by assigning
        # `view = tab`, so views 0..ntabs-1 must BE the tabs.  Allocating
        # depth-first gave the first submenu of SYS index 2 and pushed DISP to
        # 3, so choosing DISP landed on the submenu.  That invariant used to
        # hold only by luck: ADVANCED DISPLAY belongs to the LAST tab, so its
        # child index fell after every tab anyway, and the first submenu on any
        # earlier tab broke it.  _check() asserts it now instead of hoping.
        for name, rows in tabs:
            self._add_view(rows, name)
        v = 0
        while v < len(self.vrows):      # grows as children are appended, which
            self._add_children(v)       # is what carries nesting past depth 1
            v += 1
        self.tab = tab                # which TAB is lit at the bottom
        self.view = tab               # ... and which view is on screen
        self.stack = []               # how we got here, for backing out
        self.py = 0.0                 # where the window IS, in pixels
        self.pt = 0.0                 # ... and where it is going
        self.phase = 0                # of the shading flag's wave
        self._t_phase = 0
        self._scratch = bytearray(NLIST * W)   # the list, composed once
        self._smv = tuple(memoryview(self._scratch)[p * W:(p + 1) * W]
                          for p in range(NLIST))
        self._hstrip = tuple(gfx.text_strip(font, h, SPACING) for h in HEAD)
        self._tstrip = tuple(gfx.text_strip(font, t[0], SPACING) for t in tabs)
        self._more = gfx.text_strip(font, '>', SPACING)
        self._crumb = tuple(gfx.text_strip(font, '<' + t, SPACING)
                            for t in self.vtitle)
        self._check()
        for v in range(len(self.vrows)):
            for i in range(len(self.vrows[v])):
                self._build(v, i)
        self.render()

    def _add_view(self, rows, title, card=None):
        """One list of rows, with everything it needs to be drawn and edited.

        Does NOT recurse -- _add_children does, once every tab has its index.

        A CARD's view has NO rows.  That is the whole of what makes it
        different: everything that walks rows finds none, so the cursor, the
        scrollbar and the value knob all have nothing to do without needing to
        be told about cards individually."""
        v = len(self.vrows)
        self.vcard.append(card)
        self.vrows.append(rows)
        self.vtitle.append(title)
        self.vvals.append([r[F_DFLT] for r in rows])
        self.vcur.append(0)
        self.vtop.append(0)
        self.vchild.append([-1] * len(rows))
        self.vnstrip.append([gfx.text_strip(self.font, r[F_NAME], SPACING)
                             for r in rows])
        self.vvstrip.append([None] * len(rows))
        self.vunit.append([_unit_strip(self.font, r[F_UNIT]) for r in rows])
        strip = bytearray(len(rows) * ROW_P * W)
        self.vstrip.append(strip)
        self.vmv.append(tuple(memoryview(strip)[p * W:(p + 1) * W]
                              for p in range(len(strip) // W)))
        return v

    def _add_children(self, v):
        """Give every PAGE and CARD row in view `v` a view of its own."""
        for i, r in enumerate(self.vrows[v]):
            k = r[F_KIND]
            if k == PAGE:
                self.vchild[v][i] = self._add_view(r[F_SPEC], r[F_NAME])
            elif k == CARD:
                self.vchild[v][i] = self._add_view(
                    (), r[F_NAME], r[F_SPEC](self.font))

    def _check(self):
        """Every name must fit its field.  A strip wider than the space it is
        copied into is a short slice assignment, which RESIZES the bytearray on
        CPython -- so the PC preview looks perfect -- and raises IndexError on
        the device.  That has bitten this project once already (drawer.py);
        catch it at construction, where the message can say which name."""
        for t in range(len(self.tabs)):
            # nav() assigns `view = tab`, so this has to hold.  It is one
            # comparison at construction against a whole tab showing the wrong
            # screen -- which is exactly how this was found, on the panel.
            if self.vtitle[t] != self.tabs[t][0]:
                raise ValueError('view %d is %r, not tab %r'
                                 % (t, self.vtitle[t], self.tabs[t][0]))
        for v, rows in enumerate(self.vrows):
            for i, r in enumerate(rows):
                avail = _edge(r) - NAME_X          # the same edge _build uses,
                w = self.vnstrip[v][i][0]          # or the check is a lie
                if w > avail:
                    raise ValueError('%s: name is %dpx, field is %d'
                                     % (r[F_NAME], w, avail))
        for i, (w, _) in enumerate(self._hstrip):
            if w > COLW - 2:
                raise ValueError('%s: header cell is %d' % (HEAD[i], COLW - 2))

    # ---- values ----------------------------------------------------------
    def value(self, key):
        """A setting by name, wherever it lives -- tab or submenu."""
        for v, rows in enumerate(self.vrows):
            for i, r in enumerate(rows):
                if r[F_KEY] == key:
                    return self._raw(v, i)
        return None

    def raw(self, key):
        """The STORED value: an ENUM's index rather than its label.  What a
        caller wants when the row is an index into something of its own."""
        for v, rows in enumerate(self.vrows):
            for i, r in enumerate(rows):
                if r[F_KEY] == key:
                    return self.vvals[v][i]
        return None

    def set(self, key, val):
        """Write a value from outside -- one setting standing behind another,
        which is how DISPLAY VIEW ANGLE and CONTRAST stay in agreement instead
        of quietly disagreeing the moment you touch either.  Clamped to the
        row's own range, so a caller cannot put a row somewhere the knob could
        not.  -> True if it changed."""
        for v, rows in enumerate(self.vrows):
            for i, r in enumerate(rows):
                if r[F_KEY] != key:
                    continue
                k = r[F_KIND]
                if k == ENUM:
                    n = len(r[F_SPEC])
                    val = 0 if val < 0 else (n - 1 if val >= n else val)
                elif k == NUM:
                    lo, hi, _ = r[F_SPEC]
                    val = lo if val < lo else (hi if val > hi else val)
                elif k == BOOL:
                    val = 1 if val else 0
                else:
                    return False
                if val == self.vvals[v][i]:
                    return False
                self.vvals[v][i] = val
                self._build(v, i)
                return True
        return False

    def focused_key(self):
        """The key of the row under the cursor, or None on a card, which has
        no rows.  What a caller acting on a change asks, so it never has to
        know where a row sits."""
        v = self.view
        if not self.vrows[v]:
            return None
        return self.vrows[v][self.vcur[v]][F_KEY]

    def _raw(self, v, i):
        r = self.vrows[v][i]
        val = self.vvals[v][i]
        k = r[F_KIND]
        if k == ENUM:
            return r[F_SPEC][val]
        if k == BOOL:
            return bool(val)
        if k == INFO:
            return r[F_SPEC]
        if k == PAGE or k == CARD:
            return None
        return val

    def _fmt(self, v, i):
        """The only place that knows what a kind looks like.  The unit is not
        part of it: that is drawn separately, because some of them are not
        text."""
        r = self.vrows[v][i]
        k = r[F_KIND]
        val = self.vvals[v][i]
        if k == ENUM:
            return r[F_SPEC][val]
        if k == NUM:
            return '%d' % val
        if k == BOOL:
            return 'ON' if val else 'OFF'
        if k == PAGE or k == CARD:
            return ''
        return r[F_SPEC]

    # ---- input -----------------------------------------------------------
    def nav(self, d):
        """ENC1: the tabs -- or, inside a submenu, the way out.  Clamped, not
        wrapped: a fast spin should stop at the end of the row, not come round
        again somewhere unexpected."""
        if self.stack:
            return self.back()
        t = self.tab + d
        t = 0 if t < 0 else (len(self.tabs) - 1 if t >= len(self.tabs) else t)
        if t == self.tab:
            return False
        self.tab = t
        self.view = t
        self._snap()
        return True

    def back(self):
        """Out of a submenu, to whatever it was entered from."""
        if not self.stack:
            return False
        self.view = self.stack.pop()
        self._snap()
        return True

    def enter(self):
        """Into the focused row's submenu, if it has one."""
        v = self.view
        child = self.vchild[v][self.vcur[v]]
        if child < 0:
            return False
        self.stack.append(v)
        self.view = child
        self._snap()
        return True

    def _snap(self):
        """Put the window where the new view left it, with no animation: a
        slide between two different lists is motion that means nothing."""
        self.pt = self.py = float(self.vtop[self.view] * ROW_H)

    def step(self, now=0):
        """Ease the window toward the row the cursor is on.  -> True while it
        is still moving, which is what tells the caller to keep redrawing.

        MEASURED on the board: a page-aligned redraw is 1.9 ms and a shifted
        one 7.7 ms, so a scroll costs about six extra milliseconds a frame for
        the ~12 frames it lasts -- which still fits the 10.8 ms the loop is
        paced to, alongside the 1.8 ms frame push.  Worth it: a list that jumps
        by a whole row gives you nothing to tell you which way it went.

        The shift is as cheap as it gets in Python here.  Measured against 256
        entry lookup tables (6.5 vs 6.8 ms -- 5%, for 4 kB of tables), zip over
        two memoryviews (11.0) and bytes() over a generator (11.4); the obvious
        loop wins."""
        moved = False
        d = self.pt - self.py
        if d > SCROLL_SNAP or d < -SCROLL_SNAP:
            self.py += d * SCROLL_EASE
            moved = True
        elif self.py != self.pt:
            self.py = self.pt
            moved = True
        # the flag waves only while its row is the one you are looking at:
        # an animation nobody has asked to see is a redraw per tick for ever
        if now and self.animating():
            if now - self._t_phase >= PHASE_MS * 1000000:
                self._t_phase = now
                self.phase += 1
                moved = True
        return moved

    def animating(self):
        """Is the highlighted row one that moves?"""
        v = self.view
        if not self.vrows[v]:
            return False
        return _animated(self.vrows[v][self.vcur[v]][F_ART])

    def gray(self):
        """Is anything on screen actually using the four planes?  When nothing
        is, the caller can push one plane instead of four."""
        v = self.view
        off = int(self.py + 0.5)
        for i in range(len(self.vrows[v])):
            if not _animated(self.vrows[v][i][F_ART]):
                continue
            wy = i * ROW_H - off
            if -ROW_H < wy < NLIST * 8:
                return True
        return False

    def scroll(self, d):
        """ENC2: the cursor, and the window follows it."""
        v = self.view
        n = len(self.vrows[v])
        if not n:                                # a card: nothing to move
            return False
        c = self.vcur[v] + d
        c = 0 if c < 0 else (n - 1 if c >= n else c)
        if c == self.vcur[v]:
            return False
        self.vcur[v] = c
        top = self.vtop[v]
        if c < top:
            top = c
        elif c > top + NVIS - 1:
            top = c - NVIS + 1
        hi = n - NVIS
        self.vtop[v] = 0 if hi < 0 else (hi if top > hi else top)
        self.pt = float(self.vtop[v] * ROW_H)
        return True

    def adjust(self, d):
        """ENC3: the focused value, live -- no arming, no commit.  The knobs
        are labelled along the top, which is the affordance; a hidden edit mode
        is what makes people change settings they did not mean to."""
        v = self.view
        if not self.vrows[v]:                    # a card: nothing to change
            return False
        i = self.vcur[v]
        r = self.vrows[v][i]
        k = r[F_KIND]
        if k == INFO or k == PAGE or k == CARD:
            return False
        val = self.vvals[v][i]
        if k == ENUM:
            n = len(r[F_SPEC])
            nv = val + d
            nv = 0 if nv < 0 else (n - 1 if nv >= n else nv)
        elif k == NUM:
            lo, hi, step = r[F_SPEC]
            nv = val + d * step
            nv = lo if nv < lo else (hi if nv > hi else nv)
        else:
            nv = 1 if (val + d) > val else 0        # a toggle, either direction
        if nv == val:
            return False
        self.vvals[v][i] = nv
        self._build(v, i)
        return True

    def click(self):
        """ENC3 click: enter a submenu, or flip a toggle.  Anything one-shot
        or destructive that arrives later belongs here too, never on a detent:
        a detent is what an accidental brush of the panel delivers."""
        v = self.view
        if not self.vrows[v]:                    # a card: MENU NAV is the exit
            return False
        i = self.vcur[v]
        k = self.vrows[v][i][F_KIND]
        if k == PAGE or k == CARD:
            return self.enter()
        if k != BOOL:
            return False
        self.vvals[v][i] = 0 if self.vvals[v][i] else 1
        self._build(v, i)
        return True

    # ---- one row into the strip ------------------------------------------
    def _build(self, v, i):
        r = self.vrows[v][i]
        strip = self.vstrip[v]
        o = i * ROW_P * W
        for p in range(ROW_P):                   # clear the row
            strip[o + p * W:o + (p + 1) * W] = _ZERO
        art = r[F_ART]
        edge = _edge(r)

        nw, nbuf = self.vnstrip[v][i]            # the name, top line
        for j in range(min(nw, edge - NAME_X)):
            strip[o + NAME_X + j] = nbuf[j]

        b = o + W                                # ... and the value line
        for j in range(MARK_W):                  # the row marker, empty
            strip[b + MARK_X + j] = RING[j]
        txt = self._fmt(v, i)
        self.vvstrip[v][i] = gfx.text_strip(self.font, txt, SPACING)
        vw, vbuf = self.vvstrip[v][i]
        for j in range(min(vw, edge - VAL_X)):
            strip[b + VAL_X + j] = vbuf[j]
        unit = self.vunit[v][i]
        if unit:
            dx = VAL_X + vw + 1
            for j in range(len(unit)):
                if dx + j < edge:
                    strip[b + dx + j] = unit[j]
        k = r[F_KIND]
        if k == PAGE or k == CARD:               # a disclosure mark, hard right
            mw, mbuf = self._more
            for j in range(mw):
                strip[b + edge - mw + j] = mbuf[j]
        if art and not _animated(art):
            # A still goes into the strip like everything else.  An animation
            # cannot: it differs per plane and per frame, so it is composited
            # at draw time instead -- see _overlay().
            frame = art[self.vvals[v][i]]
            for p in range(ROW_P):
                so = o + p * W + ART_X
                fo = p * ART_W
                strip[so:so + ART_W] = frame[fo:fo + ART_W]
        for x in range(W):                       # the divider under the row
            strip[b + x] |= RULE

    # ---- drawing ----------------------------------------------------------
    def draw_head(self):
        o = PAGE_HEAD * W
        for k in self._ink:
            fb = self.planes[k]
            fb[o:o + W] = _ZERO
            for c in range(len(HEAD)):
                hw, hbuf = self._hstrip[c]
                x0 = COLX[c] + 1
                for j in range(min(hw, COLW - 2)):
                    fb[o + x0 + j] |= hbuf[j]
            for x in range(W):
                fb[o + x] |= RULE
            for sx in SEPX:
                fb[o + sx] = 0xFF

    def draw_tabs(self):
        """The tabs -- or, inside a submenu, where you are and the way out."""
        o = PAGE_TABS * W
        for k in self._ink:
            fb = self.planes[k]
            fb[o:o + W] = _ZERO
            if self.stack:
                # One inverted bar reading `<ADVANCED DISPLAY`.  The arrow is
                # the affordance for MENU NAV, which is the only knob with
                # nothing else to do while you are down here.
                for x in range(W):
                    fb[o + x] = 0xFF
                cw, cbuf = self._crumb[self.view]
                for j in range(min(cw, W - 4)):
                    fb[o + 2 + j] &= 0xFF ^ cbuf[j]
            else:
                for c in range(len(self.tabs)):
                    x0 = COLX[c]
                    on = c == self.tab
                    if on:                        # the active tab is inverted:
                        for x in range(x0, x0 + COLW):   # fill, then knock the
                            fb[o + x] = 0xFF             # letters back out
                    tw, tbuf = self._tstrip[c]
                    for j in range(min(tw, COLW - 2)):
                        if on:
                            fb[o + x0 + 1 + j] &= 0xFF ^ tbuf[j]
                        else:
                            fb[o + x0 + 1 + j] |= tbuf[j]
                for sx in SEPX:
                    fb[o + sx] = 0xFF
            for x in range(W):
                fb[o + x] |= RULE

    def draw_list(self):
        """Compose the list ONCE, then copy it to every plane.

        The window, the cursor, the scrollbar and the rule are identical on all
        four planes -- only the shading patch differs -- so the expensive part,
        which is the bit-shifted blit while the list is sliding, happens once
        and each plane is five byte-slices.  Doing the whole thing per plane
        would be four times the work for three quarters of it wasted.
        """
        v = self.view
        off = int(self.py + 0.5)
        sc = self._scratch
        card = self.vcard[v]
        if card is not None:
            # A card is already the whole window, composed once at
            # construction, so this is one copy and none of the machinery
            # below applies: there is no window to slide, no cursor to draw
            # and nothing to scroll past.
            sc[:] = card
        else:
            mv = self.vmv[v]
            npage = len(self.vrows[v]) * ROW_P
            pg = off >> 3
            sh = off & 7
            for p in range(NLIST):
                o = p * W
                sp = pg + p
                a = mv[sp] if 0 <= sp < npage else _ZERO
                if sh == 0:
                    sc[o:o + W] = a      # settled: a byte-slice, as before
                else:
                    b = mv[sp + 1] if 0 <= sp + 1 < npage else _ZERO
                    r = 8 - sh
                    for x in range(W):
                        sc[o + x] = ((a[x] >> sh) | (b[x] << r)) & 0xFF
            self._cursor(sc, off)
            self._scrollbar(sc, npage, off)
        o = (NLIST - 1) * W                       # the rule above the tabs
        for x in range(W):
            sc[o + x] |= RULE
        smv = self._smv
        for k in self._ink:
            fb = self.planes[k]
            for p in range(NLIST):
                o = (LIST_P0 + p) * W
                fb[o:o + W] = smv[p]
        self._overlay(off)

    def _overlay(self, off):
        """Composite any animated art over the copied list, per plane.  This is
        the only thing on the screen that differs between the four."""
        v = self.view
        rows = self.vrows[v]
        for i in range(len(rows)):
            art = rows[i][F_ART]
            if not _animated(art):
                continue
            wy = i * ROW_H - off
            if wy <= -ROW_H or wy >= NLIST * 8:
                continue                          # not on screen
            frames = art[self.vvals[v][i]]
            fs = frames[self.phase % len(frames)]
            n = len(fs)
            for k in self._ink:
                self._art(self.planes[k], fs[k % n], wy)

    def _art(self, fb, frame, wy):
        """One 30x16 patch at an arbitrary window row, a column at a time.

        Shifted by the BIT part of the offset only, never the whole row
        position: a 16-bit value shifted up 40 bits is a 56-bit integer, which
        on MicroPython leaves the small-int path for the heap.  Shifting by at
        most 7 keeps every value inside 23 bits.

        The three destination pages are the same for every column, so their
        base indices are computed once, out here.  Doing that arithmetic inside
        the loop -- (LIST_P0 + p) * W + ART_X + x, thirty times, three deep,
        four planes over -- measured 4.9 ms a redraw against this one's 2.2.
        """
        p0 = wy >> 3                    # floors for negatives, which is right
        sh = wy & 7                     # ... and this is then always positive
        o0 = o1 = o2 = -1
        if 0 <= p0 < NLIST:
            o0 = (LIST_P0 + p0) * W + ART_X
        if 0 <= p0 + 1 < NLIST:
            o1 = (LIST_P0 + p0 + 1) * W + ART_X
        if 0 <= p0 + 2 < NLIST:
            o2 = (LIST_P0 + p0 + 2) * W + ART_X
        for x in range(ART_W):
            v = (frame[x] | (frame[ART_W + x] << 8)) << sh
            if not v:
                continue
            if o0 >= 0:
                b = v & 0xFF
                if b:
                    fb[o0 + x] |= b
            if o1 >= 0:
                b = (v >> 8) & 0xFF
                if b:
                    fb[o1 + x] |= b
            if o2 >= 0:
                b = (v >> 16) & 0xFF
                if b:
                    fb[o2 + x] |= b

    def _cursor(self, fb, off):
        """The filled marker, over the empty one baked into the row.  Drawn a
        pixel at a time because mid-scroll it straddles a page boundary."""
        v = self.view
        wy = self.vcur[v] * ROW_H + 8 - off
        h = NLIST * 8
        for j in range(MARK_W):
            m = DISC[j]
            for bit in range(8):
                if (m >> bit) & 1:
                    y = wy + bit
                    if 0 <= y < h:
                        fb[(y >> 3) * W + MARK_X + j] |= 1 << (y & 7)

    def _scrollbar(self, fb, npage, off):
        """A 2px solid thumb against a 1px dotted track.  Both the same width
        and the thumb is invisible; the track dotted, so it reads as a track
        and not as a second thumb."""
        h = NLIST * 8
        total = npage * 8
        if total <= h:
            top, length = 0, h
        else:
            length = h * h // total
            if length < 6:
                length = 6
            top = (h - length) * off // (total - h)
            if top > h - length:      # the last row is allowed to sit above a
                top = h - length      # blank half-row; the thumb is not
            elif top < 0:
                top = 0
        for p in range(NLIST):
            thumb = track = 0
            for y in range(8):
                gy = p * 8 + y
                if top <= gy < top + length:
                    thumb |= 1 << y
                elif not (gy & 1):
                    track |= 1 << y
            o = p * W + BAR_X
            fb[o] = thumb | track
            for j in range(1, BAR_W):
                fb[o + j] = thumb

    def render(self):
        self.draw_head()
        self.draw_list()
        self.draw_tabs()
