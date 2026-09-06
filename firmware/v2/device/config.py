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

# ---- what a setting can be -------------------------------------------------
# ENUM  a choice from a tuple of labels; the value is the index
# NUM   an integer with a (lo, hi, step); the value is the number
# BOOL  ON / OFF; the value is 0 or 1
# INFO  read-only, `spec` is the text; a detent does nothing to it
# PAGE  a submenu, `spec` is its rows; SELECT-click enters, MENU NAV backs out
ENUM, NUM, BOOL, INFO, PAGE = 0, 1, 2, 3, 4


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


# 4x4 ordered dither, for putting a gradient on a panel with two colours
BAYER = (0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5)


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

def _shade_art(planes=(1, 2, 3, 4), w=ART_W, h=ROW_P * 8 - 2, y0=1,
               amp=2, waves=1.5):
    """A little waving flag per shading mode: a black-to-white ramp, dithered,
    inside a band displaced by a sine so it reads as cloth rather than a swatch.

    The ramp is quantised to that mode's level count, which is what differs
    between the four -- two levels give a hard edge across the middle, five
    give something that passes for a gradient at 30 pixels wide.

    The band is OUTLINED, and the outline is joined column to column.  Without
    the outline the flag dissolves wherever the ramp goes light, so it reads as
    a blob fading out rather than a shape; without the joining it is a dotted
    trail wherever the sine steps more than a pixel between columns.
    """
    frames = []
    for np in planes:
        n = np + 1                       # levels, counting white and black
        band = h - 2 * amp
        buf = bytearray(ROW_P * w)

        def ink(x, y):
            buf[(y >> 3) * w + x] |= 1 << (y & 7)

        prev = None
        for x in range(w):
            shade = 1.0 - x / (w - 1.0)                    # black on the left
            q = int(shade * (n - 1) + 0.5) / (n - 1.0)
            # floor(v + 0.5), not int(v + 0.5): int() truncates toward zero,
            # so the trough of the wave rounds the wrong way
            dy = int(math.floor(
                amp * math.sin(2.0 * math.pi * waves * x / (w - 1.0)) + 0.5))
            top = y0 + amp + dy
            thr = q * 16.0
            for yy in range(top, top + band):
                if BAYER[(yy & 3) * 4 + (x & 3)] < thr:
                    ink(x, yy)
            lo = top if prev is None else (top if top < prev else prev)
            hi = top if prev is None else (prev if top < prev else top)
            for yy in range(lo, hi + 1):
                ink(x, yy)
                ink(x, yy + band - 1)
            prev = top
        frames.append(bytes(buf))
    return tuple(frames)


# The names are graybars.MODES, which is the experiment these came out of; the
# plane count is what each one costs and how many levels it gets.
SHADES = ('1F SPATIAL', '2F TEMPORAL', '3F HYBRID', '4F HYBRID')
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
        ('SHADING', ENUM, 'shading', SHADES, 3, None, SHADE_ART),
        ('DIM AFTER', ENUM, 'dim_after', ('30S', '1MIN', '5MIN', 'NEVER'),
         1, None, None),
        ('ADVANCED DISPLAY', PAGE, 'advanced', ADV, 0, None, None))

SYS = (('METER RATE', ENUM, 'meter_rate', ('30', '60', '90'), 1, 'HZ', None),
       ('KNOB STEP', NUM, 'knob_step', (1, 10, 1), 4, None, None),
       ('HOLD TO MUTE', BOOL, 'hold_mute', None, 1, None, None),
       ('MEDIA KEYS', BOOL, 'media_keys', None, 1, None, None))

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


class Config:
    def __init__(self, font, tabs=TABS, n=1, tab=START_TAB):
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
        for name, rows in tabs:
            self._add_view(rows, name)
        self.tab = tab                # which TAB is lit at the bottom
        self.view = tab               # ... and which view is on screen
        self.stack = []               # how we got here, for backing out
        self.py = 0.0                 # where the window IS, in pixels
        self.pt = 0.0                 # ... and where it is going
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

    def _add_view(self, rows, title):
        """One list of rows, with everything it needs to be drawn and edited.
        Recurses into PAGE rows, which is fine here -- it happens once, at
        construction, and the tree is two deep."""
        v = len(self.vrows)
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
        for i, r in enumerate(rows):
            if r[F_KIND] == PAGE:
                self.vchild[v][i] = self._add_view(r[F_SPEC], r[F_NAME])
        return v

    def _check(self):
        """Every name must fit its field.  A strip wider than the space it is
        copied into is a short slice assignment, which RESIZES the bytearray on
        CPython -- so the PC preview looks perfect -- and raises IndexError on
        the device.  That has bitten this project once already (drawer.py);
        catch it at construction, where the message can say which name."""
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
        """The key of the row under the cursor.  What a caller acting on a
        change asks, so it never has to know where a row sits."""
        v = self.view
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
        if k == PAGE:
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
        if k == PAGE:
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

    def step(self):
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
        d = self.pt - self.py
        if d > SCROLL_SNAP or d < -SCROLL_SNAP:
            self.py += d * SCROLL_EASE
            return True
        if self.py != self.pt:
            self.py = self.pt
            return True
        return False

    def scroll(self, d):
        """ENC2: the cursor, and the window follows it."""
        v = self.view
        n = len(self.vrows[v])
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
        i = self.vcur[v]
        r = self.vrows[v][i]
        k = r[F_KIND]
        if k == INFO or k == PAGE:
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
        i = self.vcur[v]
        k = self.vrows[v][i][F_KIND]
        if k == PAGE:
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
        if r[F_KIND] == PAGE:                    # a disclosure mark, hard right
            mw, mbuf = self._more
            for j in range(mw):
                strip[b + edge - mw + j] = mbuf[j]
        if art:
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
        v = self.view
        mv = self.vmv[v]
        npage = len(self.vrows[v]) * ROW_P
        off = int(self.py + 0.5)
        pg = off >> 3
        sh = off & 7
        for k in self._ink:
            fb = self.planes[k]
            for p in range(NLIST):
                o = (LIST_P0 + p) * W
                sp = pg + p
                a = mv[sp] if 0 <= sp < npage else _ZERO
                if sh == 0:
                    fb[o:o + W] = a          # settled: a byte-slice, as before
                else:
                    b = mv[sp + 1] if 0 <= sp + 1 < npage else _ZERO
                    r = 8 - sh
                    for x in range(W):
                        fb[o + x] = ((a[x] >> sh) | (b[x] << r)) & 0xFF
            self._cursor(fb, off)
            self._scrollbar(fb, npage, off)
            o = LIST_P1 * W                       # the rule above the tabs
            for x in range(W):
                fb[o + x] |= RULE

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
                        fb[(LIST_P0 + (y >> 3)) * W + MARK_X + j] |= 1 << (y & 7)

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
            o = (LIST_P0 + p) * W + BAR_X
            fb[o] = thumb | track
            for j in range(1, BAR_W):
                fb[o + j] = thumb

    def render(self):
        self.draw_head()
        self.draw_list()
        self.draw_tabs()
