"""The CONFIG screen: settings, in tabs, driven by the three top knobs.

    cfg = config.Config(ccfont.TINY)
    cfg.nav(+1)        # ENC1: OS / SYS / DISP
    cfg.scroll(+1)     # ENC2: move the cursor down the list
    cfg.adjust(+1)     # ENC3: change the focused setting
    cfg.render()

Layout, top to bottom:

    page 0    MENU NAV | SCROLL | SELECT      what each top knob does
    pages 1-5 the setting list, 16px rows, with a scrollbar down column 0
    page 6    OS | SYS | DISP                 tabs, the active one inverted
    page 7    the mode bar, which belongs to mixer.py and is copied in

**A setting is a tuple, and adding one is a line.**  The renderer has exactly
one place that knows what a kind is (`_fmt`) and the input handler has one
more; everything else works off the tuple.  Tuples rather than objects because
this runs on CircuitPython, where every instance carries a dict, and because
the same module is imported on the PC for pixel-exact previews.

    (name, kind, key, spec, default, art)

`spec` is whatever the kind needs: ENUM a tuple of labels, PCT a (lo, hi, step)
triple, BOOL nothing, INFO the string to show.  `key` is a string from the
start, so a value can be found by name rather than by position -- the order of
these rows WILL change, and the day one of them is wired to the host or saved
to disk, position is the wrong handle.  `art` is None or a tuple of little
16px-tall pictures indexed BY THE VALUE, which is the whole of the view-angle
flipbook: no logic, just `art[val]`.

**Rows are 16 px, which is two pages.**  The name on the top line, the marker
and value on the bottom, a picture spanning both on the right -- and, because
16 is a multiple of 8, scrolling by a whole row is still a byte-slice blit.
Every screen here is cheap for that one reason.  Two rows show fully and a
third shows its top half, which is also how you can tell there is more below.

**Nothing here is on a timer.**  The mixer and EQ screens redraw on a 16 ms
tick because their meters move; a settings list changes only when a knob does,
so it redraws on input and the frame loop pushes the same bytes in between.
"""
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
ENUM, PCT, BOOL, INFO = 0, 1, 2, 3


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


ANGLE_ART = _angle_art()
ANGLES = tuple('%d' % (10 * (i + 1)) for i in range(len(ANGLE_ART)))

# name, kind, key, spec, default, art.  Only DISPLAY VIEW ANGLE is real; the
# rest are placeholders standing in the right shape so the list, the scrolling
# and the tabs have something to be tested against.
DISP = (('DISPLAY VIEW ANGLE', ENUM, 'view_angle', ANGLES, 4, ANGLE_ART),
        ('BRIGHTNESS', PCT, 'brightness', (0, 100, 5), 100, None),
        ('DIM AFTER', ENUM, 'dim_after', ('30S', '1MIN', '5MIN', 'NEVER'),
         1, None),
        ('INVERT', BOOL, 'invert', None, 0, None))

SYS = (('METER RATE', ENUM, 'meter_rate', ('30HZ', '60HZ', '90HZ'), 1, None),
       ('KNOB STEP', PCT, 'knob_step', (1, 10, 1), 4, None),
       ('HOLD TO MUTE', BOOL, 'hold_mute', None, 1, None),
       ('MEDIA KEYS', BOOL, 'media_keys', None, 1, None))

OS = (('HOST LINK', INFO, 'link', 'USB CDC', 0, None),
      ('AUTO CONNECT', BOOL, 'autoconnect', None, 1, None),
      ('KEEP COLUMNS', BOOL, 'keep_columns', None, 1, None),
      ('FIRMWARE', INFO, 'firmware', 'V2-MIXER', 0, None))

TABS = (('OS', OS), ('SYS', SYS), ('DISP', DISP))
START_TAB = 2                     # DISP: where the one real setting lives


class Config:
    def __init__(self, font, tabs=TABS, n=1, tab=START_TAB):
        self.font = font
        self.planes = [gfx.new() for _ in range(n)]
        self._ink = tuple(range(n))
        self.tabs = tabs
        self.tab = tab
        self.rows = tuple(t[1] for t in tabs)
        self._n = tuple(len(r) for r in self.rows)
        # cursor and scroll are PER TAB: coming back to a tab should find it
        # where you left it, not reset to the top
        self.cur = [0] * len(tabs)
        self.top = [0] * len(tabs)
        self.vals = [[r[4] for r in rows] for rows in self.rows]
        self._nstrip = [[gfx.text_strip(font, r[0], SPACING) for r in rows]
                        for rows in self.rows]
        self._vstrip = [[None] * len(rows) for rows in self.rows]
        self._strip = [bytearray(len(rows) * ROW_P * W) for rows in self.rows]
        self._mv = [tuple(memoryview(s)[p * W:(p + 1) * W]
                          for p in range(len(s) // W))
                    for s in self._strip]
        self._hstrip = tuple(gfx.text_strip(font, h, SPACING) for h in HEAD)
        self._tstrip = tuple(gfx.text_strip(font, t[0], SPACING) for t in tabs)
        self._check()
        for t in range(len(tabs)):
            for i in range(self._n[t]):
                self._build(t, i)
        self.render()

    def _check(self):
        """Every name must fit its field.  A strip wider than the space it is
        copied into is a short slice assignment, which RESIZES the bytearray on
        CPython -- so the PC preview looks perfect -- and raises IndexError on
        the device.  That has bitten this project once already (drawer.py);
        catch it at construction, where the message can say which name."""
        for t, rows in enumerate(self.rows):
            for i, r in enumerate(rows):
                avail = (ART_X if r[5] else W - PAD_R) - NAME_X
                w = self._nstrip[t][i][0]
                if w > avail:
                    raise ValueError('%s: name is %dpx, field is %d'
                                     % (r[0], w, avail))
        for i, (w, _) in enumerate(self._hstrip):
            if w > COLW - 2:
                raise ValueError('%s: header cell is %d' % (HEAD[i], COLW - 2))

    # ---- values ----------------------------------------------------------
    def value(self, key):
        """A setting by name.  Nothing reads these yet -- the view angle is a
        drawing for now -- but this is the handle the wiring will use."""
        for t, rows in enumerate(self.rows):
            for i, r in enumerate(rows):
                if r[2] == key:
                    return self._raw(t, i)
        return None

    def _raw(self, t, i):
        r = self.rows[t][i]
        v = self.vals[t][i]
        if r[1] == ENUM:
            return r[3][v]
        if r[1] == BOOL:
            return bool(v)
        if r[1] == INFO:
            return r[3]
        return v

    def _fmt(self, t, i):
        """The only place that knows what a kind looks like."""
        r = self.rows[t][i]
        k = r[1]
        v = self.vals[t][i]
        if k == ENUM:
            return r[3][v]
        if k == PCT:
            return '%d%%' % v
        if k == BOOL:
            return 'ON' if v else 'OFF'
        return r[3]

    # ---- input -----------------------------------------------------------
    def nav(self, d):
        """ENC1: the tabs.  Clamped, not wrapped -- a fast spin should stop at
        the end of the row, not come round again somewhere unexpected."""
        t = self.tab + d
        t = 0 if t < 0 else (len(self.tabs) - 1 if t >= len(self.tabs) else t)
        if t == self.tab:
            return False
        self.tab = t
        return True

    def scroll(self, d):
        """ENC2: the cursor, and the window follows it."""
        t = self.tab
        n = self._n[t]
        c = self.cur[t] + d
        c = 0 if c < 0 else (n - 1 if c >= n else c)
        if c == self.cur[t]:
            return False
        self.cur[t] = c
        top = self.top[t]
        if c < top:
            top = c
        elif c > top + NVIS - 1:
            top = c - NVIS + 1
        hi = n - NVIS
        self.top[t] = 0 if hi < 0 else (hi if top > hi else top)
        return True

    def adjust(self, d):
        """ENC3: the focused value, live -- no arming, no commit.  The knobs
        are labelled along the top, which is the affordance; a hidden edit mode
        is what makes people change settings they did not mean to."""
        t = self.tab
        i = self.cur[t]
        r = self.rows[t][i]
        k = r[1]
        if k == INFO:
            return False
        v = self.vals[t][i]
        if k == ENUM:
            nv = v + d
            nv = 0 if nv < 0 else (len(r[3]) - 1 if nv >= len(r[3]) else nv)
        elif k == PCT:
            lo, hi, step = r[3]
            nv = v + d * step
            nv = lo if nv < lo else (hi if nv > hi else nv)
        else:
            nv = 1 if (v + d) > v else 0        # a toggle, either direction
        if nv == v:
            return False
        self.vals[t][i] = nv
        self._build(t, i)
        return True

    def click(self):
        """ENC3 click flips a toggle.  Anything one-shot or destructive that
        arrives later belongs here too, never on a detent: a detent is what an
        accidental brush of the panel delivers."""
        t = self.tab
        i = self.cur[t]
        if self.rows[t][i][1] != BOOL:
            return False
        self.vals[t][i] = 0 if self.vals[t][i] else 1
        self._build(t, i)
        return True

    # ---- one row into the strip ------------------------------------------
    def _build(self, t, i):
        r = self.rows[t][i]
        strip = self._strip[t]
        o = i * ROW_P * W
        for p in range(ROW_P):                   # clear the row
            strip[o + p * W:o + (p + 1) * W] = _ZERO
        art = r[5]
        edge = ART_X - 2 if art else W - PAD_R

        nw, nbuf = self._nstrip[t][i]            # the name, top line
        for j in range(min(nw, edge - NAME_X)):
            strip[o + NAME_X + j] = nbuf[j]

        b = o + W                                # ... and the value line
        for j in range(MARK_W):                  # the row marker, empty
            strip[b + MARK_X + j] = RING[j]
        txt = self._fmt(t, i)
        self._vstrip[t][i] = gfx.text_strip(self.font, txt, SPACING)
        vw, vbuf = self._vstrip[t][i]
        deg = r[5] is ANGLE_ART                  # the view angle reads "45 deg"
        for j in range(min(vw, edge - VAL_X)):
            strip[b + VAL_X + j] = vbuf[j]
        if deg:
            dx = VAL_X + vw + 1
            for j in range(len(DEG)):
                if dx + j < edge:
                    strip[b + dx + j] = DEG[j]
        if art:
            frame = art[self.vals[t][i]]
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
        o = PAGE_TABS * W
        for k in self._ink:
            fb = self.planes[k]
            fb[o:o + W] = _ZERO
            for c in range(len(self.tabs)):
                x0 = COLX[c]
                on = c == self.tab
                if on:                            # the active tab is inverted:
                    for x in range(x0, x0 + COLW):    # fill, then knock the
                        fb[o + x] = 0xFF              # letters back out
                tw, tbuf = self._tstrip[c]
                for j in range(min(tw, COLW - 2)):
                    if on:
                        fb[o + x0 + 1 + j] &= 0xFF ^ tbuf[j]
                    else:
                        fb[o + x0 + 1 + j] |= tbuf[j]
            for x in range(W):
                fb[o + x] |= RULE
            for sx in SEPX:
                fb[o + sx] = 0xFF

    def draw_list(self):
        t = self.tab
        mv = self._mv[t]
        npage = self._n[t] * ROW_P
        base = self.top[t] * ROW_P
        for k in self._ink:
            fb = self.planes[k]
            for p in range(NLIST):
                o = (LIST_P0 + p) * W
                sp = base + p
                fb[o:o + W] = mv[sp] if sp < npage else _ZERO
            # the cursor: a filled marker OR'd over the ring already in place
            wp = self.cur[t] * ROW_P + 1 - base
            if 0 <= wp < NLIST:
                o = (LIST_P0 + wp) * W + MARK_X
                for j in range(MARK_W):
                    fb[o + j] |= DISC[j]
            self._scrollbar(fb, npage)
            o = LIST_P1 * W                       # the rule above the tabs
            for x in range(W):
                fb[o + x] |= RULE

    def _scrollbar(self, fb, npage):
        """A 2px solid thumb against a 1px dotted track.  Both the same width
        and the thumb is invisible; the track dotted, so it reads as a track
        and not as a second thumb."""
        h = NLIST * 8
        if npage <= NLIST:
            top, length = 0, h
        else:
            length = h * NLIST // npage
            if length < 6:
                length = 6
            top = (h - length) * (self.top[self.tab] * ROW_P) // (npage - NLIST)
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
