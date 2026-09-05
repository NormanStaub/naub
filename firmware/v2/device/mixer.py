"""The naub V2 mixer screen: three app columns, each with a volume bar, a name
and a dot level meter for what that app is playing, over a one-page mode bar.

    +------------------------------------+
    | ####----- | ######--- | ########## |  volume bar    (page 0)
    | CHROME    | MUSIC     | SPOTIFY    |  name, marquee (page 1)
    | -   - -   | -     -   |            |  7 bands per column, standing on
    | :   : :   | :     -   |            |  the floor and bouncing UP, each
    | : : : : : | : : : : : |            |  with its own peak marker riding
    |------------------------------------|  its leading edge   (pages 2-6)
    | APP CTRL |||       SYS. #########   |  mode bar      (page 7)
    +------------------------------------+

A mixer meter: seven bands per app rise from the bottom, each with a short line
at its leading edge -- the peak hold, which jumps straight to any new peak and
then settles back smoothly when the signal drops.

Both text bars are exactly one page tall, each with its rule on the row that
divides it from the meter: the names bar carries its rule at the bottom of its
own page (y15), the mode bar takes the last row of the meter band (y55).  That
is why the meter's scratch clears its final page TO the rule -- the line above
the mode bar then costs nothing.

The bottom bar names the current MODE rather than a fixed preset, and the `|||`
beside the label is not decoration: it is the position in the mode list.  There
is always one tick fewer than there are modes, and the current mode splits them
-- ticks to the LEFT of the label are the modes behind you, ticks to the RIGHT
the ones ahead, so `|| CUSTOM 1 |` reads as third of four at a glance.

The ticks sit on a FIXED LATTICE and never move to accommodate the text, which
is the whole point of them: a position indicator that shifted with the length
of the name would indicate nothing.  The label is left-justified in a field as
wide as the longest mode name, so advancing a mode moves exactly two things --
one tick hops from the right lattice to the left one, and the label's leading
edge steps right by exactly the space that tick now occupies.

That hop is ANIMATED, and the animation falls out of the geometry rather than
being bolted on.  Every tick j has exactly two homes -- LEFT_j and RIGHT_j, one
per side of the label -- and is at LEFT_j precisely when j is behind the current
mode.  So give each tick a crossed-ness from 0 (right) to 1 (left), ease it
toward its target, and place it between its two homes: one mode step wipes a
single line across the label, and a wrap from the last mode to the first sweeps
all of them back at once.  The label's own x is the SUM of those crossed-nesses
times the pitch, so it slides in step with the lines that are pushing it --
there is no separate label tween to keep in sync.

Pure Python, no hardware imports, so host/preview_mixer.py renders exactly what
the panel shows.

Everything is built around the page grid, because that is what makes a 60 Hz
meter affordable in Python:

  * every possible band height is precomputed as page byte masks (BAND_PAGES),
    so drawing a band is a handful of ORs into a per-column scratch buffer that
    is then slice-copied straight into the framebuffer;
  * the scratch overwrites every byte of a column, so there is no clear pass,
    and the separators live in the 1px gaps BETWEEN columns where the hot path
    never touches them -- chrome is drawn once, at construction;
  * names render once into a one-page gfx.text_strip() and the marquee is a
    byte-slice window into it, so scrolling a long name costs nothing.  Both
    text bars being one page tall is what allows that: a strip IS a page.

This screen is drawn PURE BINARY: every element is fully inked or absent, and
a part-duty area of any size visibly flickers.  Being binary also means one
framebuffer suffices (n=1, the default) -- there is nothing to cycle, so no FRC
shimmer at all and a quarter of the drawing work.
"""
from random import random as _rand

import gfx

W = gfx.W
_ZERO = bytes(W)

# ---- layout ----------------------------------------------------------------
NCOL = 3
COLX = (0, 43, 86)            # column left edges; separators at x=42 and x=85
COLW = 42
SEPX = (42, 85)

PAGE_VOL = 0                  # volume bars
PAGE_NAME = 1                 # app names (+ the rule under them)
DOT_P0, DOT_P1 = 2, 6         # meter band
NPAGE = DOT_P1 - DOT_P0 + 1
PAGE_FOOT = 7                 # the mode bar, one page like the names bar

VOL_MASK = 0x7E               # y1..y6 within a page: a filled bar
VOL_OUTLINE = 0x42            # y1 and y6 only: the same bar, muted
VOL_BASE = 0x40               # y6 only: the empty part of the track
RULE_MASK = 0x80              # bottom row of a page, for horizontal rules
SPACING = 0                   # ccfont.TINY advances already carry side bearing;
                              # 1px more would push most app names into a marquee

# ---- the mode bar (page 7) -------------------------------------------------
MODES = ('APP CTRL', 'EQ', 'CUSTOM 1', 'CUSTOM 2')

TICK_MASK = 0xFF              # the full height of the bar: the ticks hang off
                              # the rule like cabinet dividers
FOOT_SHIFT = 1                # push the label down a row so it clears the rule
                              # above it, mirroring the 1px the names bar
                              # leaves above ITS rule.  TINY's caps live in
                              # rows 0..5, so shifting by one still fits.
TICK_PITCH = 3                # 1px stroke + 2px gap: the space one tick owns
TICK_X = 1                    # the left lattice starts here
TICK_GAP = 4                  # px between the label field and the right lattice
TICK_EASE = 0.28              # per-frame fraction of the remaining crossing
TICK_SNAP = 0.01              # ... below which it is simply done

SYS_BARW = 34
SYS_PAD = 2

# ---- meter -----------------------------------------------------------------
# Each app column is 7 bands across, and each band stands on the floor of the
# band and grows UP.
#
# The band run is CENTRED in its column, which constrains the numbers: the run
# is (NBAND-1) * BAND_PITCH + BAND_W wide and the leftover has to be even for
# the margins to match.  7 bands at pitch 6 span 38 of the 42px column, so the
# margin is exactly 2 either side.  (8 bands at pitch 5 span 37, leaving an odd
# 5 -- that is what made the old meter look adrift in its column.)
#
# Height is in PIXELS, not dot rows: a band's fill is any of 0..40, so the
# ballistics move at the finest step the panel has.  The dot texture comes from
# the pattern (2px lit, 2px clear) rather than from quantising the height, so
# the meter still reads as a dot matrix while moving smoothly.
NBAND = 7
BAND_PITCH = 6
BAND_W = 2                    # px wide
BAND_X0 = (COLW - ((NBAND - 1) * BAND_PITCH + BAND_W)) // 2
BANDX = tuple(BAND_X0 + i * BAND_PITCH for i in range(NBAND))
METER_H = NPAGE * 8           # 40 px of travel
PEAK_W = 4                    # peak marker overhangs its band, so it reads as
PEAK_DX = -1                  # a line rather than another dot

# Meter ballistics, as fraction-of-the-gap per update.  Both rise instantly and
# fall gradually -- that asymmetry is what makes a meter look alive.  The peak
# marker falls ~6x slower than the band, so it detaches and settles.  Tuned for
# TICK_MS = 16 (~60 Hz); at half the rate, roughly double both.
FALL = 0.18
PEAK_FALL = 0.028

# Faked spectrum.  The host can only tell us one amplitude per app, so the
# shape across the bands is invented: a gentle tilt (lows louder, as most
# material is) times a per-band random walk.  The walk is smoothed rather than
# re-rolled each frame, so bands drift and bounce independently instead of
# flickering like static.
TILT = (1.0, 0.97, 0.93, 0.87, 0.79, 0.70, 0.59)
NOISE_KEEP = 0.82             # how much of last frame's wander is retained
NOISE_GAIN = 0.55             # how far a band can stray from the tilt

_RULE_ROW = bytes((RULE_MASK,)) * COLW    # clears a page AND lays the rule


def _band_pages(h):
    """A band standing h pixels tall from the BOTTOM, as one byte mask per page.

    The 2px-on/2px-off dot texture is anchored to the bottom too, so the dots
    stay put as the bar grows instead of crawling."""
    pages = bytearray(NPAGE)
    for i in range(h):
        if (i & 3) < 2:
            y = METER_H - 1 - i
            pages[y >> 3] |= 1 << (y & 7)
    return bytes(pages)


BAND_PAGES = tuple(_band_pages(h) for h in range(METER_H + 1))


class Mixer:
    def __init__(self, font, n=1, mode=0, modes=MODES):
        """n = number of FRC planes.  This screen is entirely binary, so ONE
        plane is enough and is the default: nothing to cycle means no shimmer
        at all, and a quarter of the drawing work.  Pass n=4 if you add gray
        elements back."""
        self.font = font
        self.planes = [gfx.new() for _ in range(n)]
        self._ink = tuple(range(n))
        self.modes = tuple(modes)
        self.mode = mode
        # The label field is as wide as the longest mode name, and the right
        # lattice starts past the field -- so no tick position depends on which
        # mode is showing.
        self._label_w = max(gfx.measure(font, m, SPACING) for m in self.modes)
        self._tick_rx = TICK_X + self._label_w + TICK_GAP
        # Strips built once: draw_foot runs every frame while the ticks are
        # crossing, and gfx.text_strip allocates.
        self._mstrip = tuple(gfx.text_strip(font, m, SPACING) for m in self.modes)
        self._sysstrip = gfx.text_strip(font, 'SYS.', SPACING)
        # per tick: 0 = parked on the right of the label, 1 = on the left
        self._cross = [1.0 if j < self.mode else 0.0
                       for j in range(len(self.modes) - 1)]
        self.names = [''] * NCOL
        self._strip = [None] * NCOL      # (w, buf) from gfx.text_strip
        self._scroll = [0] * NCOL        # marquee offset, px
        self._step = [1] * NCOL          # marquee direction
        self.vol = [0.0] * NCOL          # 0..1
        self.mute = [False] * NCOL
        self.sysvol = 0.0
        self.sysmute = False
        self.level = [0.0] * NCOL        # the column's own level, smoothed
        # per band: what it shows, where its peak marker sits, and its wander
        self.band = [[0.0] * NBAND for _ in range(NCOL)]
        self.bpeak = [[0.0] * NBAND for _ in range(NCOL)]
        self._noise = [[0.0] * NBAND for _ in range(NCOL)]
        self._scratch = bytearray(NPAGE * COLW)
        self._foot = bytearray(W)        # the mode bar, built a row at a time
        self.chrome()

    # ---- static furniture (draw once) ------------------------------------
    def chrome(self):
        """Rules and column separators.  The separators sit in the 1px gaps
        BETWEEN the columns, so nothing in the hot path ever overwrites them
        and they only need drawing once."""
        for k in self._ink:
            fb = self.planes[k]
            o = PAGE_NAME * W
            for x in range(W):
                fb[o + x] |= RULE_MASK
            for sx in SEPX:
                for p in range(PAGE_NAME, DOT_P1 + 1):
                    fb[p * W + sx] = 0xFF

    # ---- data in ---------------------------------------------------------
    def set_name(self, i, s):
        self.names[i] = s
        self._strip[i] = gfx.text_strip(self.font, s, SPACING) if s else None
        self._scroll[i] = 0
        self._step[i] = 1

    def update(self, levels):
        """Feed one amplitude per column (0..1); spread it across the bands and
        advance every band's ballistics one step."""
        for c in range(NCOL):
            v = levels[c]
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            self.level[c] = v
            bands = self.band[c]
            peaks = self.bpeak[c]
            noise = self._noise[c]
            for b in range(NBAND):
                n = noise[b] * NOISE_KEEP + (_rand() - 0.5) * NOISE_GAIN
                if n < -0.9:
                    n = -0.9
                elif n > 0.9:
                    n = 0.9
                noise[b] = n
                t = v * TILT[b] * (1.0 + n)
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                x = bands[b]
                x = t if t >= x else x + (t - x) * FALL
                bands[b] = x
                p = peaks[b]
                peaks[b] = x if x >= p else p + (x - p) * PEAK_FALL

    def marquee(self):
        """Advance any name too wide for its column; -> True if one moved."""
        moved = False
        for c in range(NCOL):
            st = self._strip[c]
            if st is None or st[0] <= COLW - 2:
                continue
            over = st[0] - (COLW - 2)
            s = self._scroll[c] + self._step[c]
            if s >= over:
                s = over
                self._step[c] = -1
            elif s <= 0:
                s = 0
                self._step[c] = 1
            self._scroll[c] = s
            moved = True
        return moved

    # ---- draw ------------------------------------------------------------
    def draw_dots(self):
        # No band clear and no chrome restore: the scratch fully overwrites
        # every byte of a column, and the separators live outside the columns.
        # The last page is cleared to the rule row, so the line above the mode
        # bar comes for free.
        scratch = self._scratch
        mv = memoryview(scratch)
        blank = _ZERO[:COLW]
        last = NPAGE - 1
        for c in range(NCOL):
            for i in range(last):                    # one column's worth
                scratch[i * COLW:(i + 1) * COLW] = blank
            scratch[last * COLW:] = _RULE_ROW
            bands = self.band[c]
            peaks = self.bpeak[c]
            for b in range(NBAND):
                h = int(bands[b] * METER_H + 0.5)
                if h > METER_H:
                    h = METER_H
                bx = BANDX[b]
                if h:
                    pages = BAND_PAGES[h]
                    for i in range(NPAGE):
                        m = pages[i]
                        if m:
                            o = i * COLW + bx
                            for x in range(o, o + BAND_W):
                                scratch[x] |= m
                p = peaks[b]
                if p > 0.0:                          # this band's peak marker
                    d = int(p * METER_H + 0.5)       # height above the floor
                    if d < 1:
                        d = 1
                    elif d > METER_H:
                        d = METER_H
                    y = METER_H - d
                    o = (y >> 3) * COLW + bx + PEAK_DX
                    m = 1 << (y & 7)
                    lo = 0 if o < 0 else o
                    for x in range(lo, o + PEAK_W):
                        scratch[x] |= m
            x0 = COLX[c]
            for i in range(NPAGE):
                o = (DOT_P0 + i) * W + x0
                src = mv[i * COLW:(i + 1) * COLW]
                for k in self._ink:
                    self.planes[k][o:o + COLW] = src

    def draw_names(self):
        o = PAGE_NAME * W
        for k in self._ink:
            self.planes[k][o:o + W] = _ZERO
        for c in range(NCOL):
            st = self._strip[c]
            if st is None:
                continue
            w, buf = st
            avail = COLW - 2
            if w <= avail:
                x = COLX[c] + (COLW - w) // 2
                src = memoryview(buf)[:w]
                n = w
            else:
                x = COLX[c] + 1
                s = self._scroll[c]
                src = memoryview(buf)[s:s + avail]
                n = avail
            a = o + x
            for k in self._ink:
                self.planes[k][a:a + n] = src
        for k in self._ink:                               # restore rule + seps
            fb = self.planes[k]
            for x in range(W):
                fb[o + x] |= RULE_MASK
            for sx in SEPX:
                fb[o + sx] = 0xFF

    def draw_vol(self):
        o = PAGE_VOL * W
        for k in self._ink:
            self.planes[k][o:o + W] = _ZERO
        for c in range(NCOL):
            x0 = COLX[c]
            fill = int(self.vol[c] * COLW + 0.5)
            if fill > COLW:
                fill = COLW
            # muted -> hollow outline; live -> solid.  Binary either way.
            m = VOL_OUTLINE if self.mute[c] else VOL_MASK
            for k in self._ink:
                fb = self.planes[k]
                for x in range(x0, x0 + fill):
                    fb[o + x] |= m
                for x in range(x0 + fill, x0 + COLW):
                    fb[o + x] |= VOL_BASE

    # ---- the mode bar ----------------------------------------------------
    def set_mode(self, i):
        """Point the ticks at mode i.  They ease there; call step_foot() until
        it returns False."""
        self.mode = i % len(self.modes)
        return self.step_foot()

    def step_foot(self):
        """Advance the tick crossing one frame and repaint the bar.
        -> True while anything is still moving."""
        cross = self._cross
        moving = False
        for j in range(len(cross)):
            t = 1.0 if j < self.mode else 0.0
            c = cross[j]
            d = t - c
            if d > TICK_SNAP or d < -TICK_SNAP:
                cross[j] = c + d * TICK_EASE
                moving = True
            elif c != t:
                cross[j] = t
        self.draw_foot()
        return moving

    def _text(self, x, strip):
        """OR a prebuilt TINY strip into the mode bar row at column x, one row
        down so it does not touch the rule above."""
        w, buf = strip
        row = self._foot
        for i in range(w):
            if x + i >= W:
                break
            row[x + i] |= (buf[i] << FOOT_SHIFT) & 0xFF

    def draw_foot(self):
        """The mode bar: position ticks either side of the mode name, then the
        system volume.  Cheap enough to run per frame while the ticks cross,
        but otherwise only redrawn when something changes."""
        row = self._foot
        row[:] = _ZERO

        # Think of it as n slots in a row: n-1 hold a tick and one holds the
        # label, and advancing a mode swaps the label with the tick on its
        # right.  Every home depends only on the tick's index and on the widest
        # mode name, never on the name currently showing -- so the last tick
        # always lands on the same column whatever the label says.
        i = self.mode if 0 <= self.mode < len(self.modes) else 0
        cross = self._cross
        slid = 0.0
        for j in range(len(cross)):
            c = cross[j]
            slid += c
            lx = TICK_X + j * TICK_PITCH               # its home on the left
            rx = self._tick_rx + j * TICK_PITCH        # ... and on the right
            t = int(rx + (lx - rx) * c + 0.5)
            if 0 <= t < W:
                row[t] |= TICK_MASK
        self._text(TICK_X + int(slid * TICK_PITCH + 0.5), self._mstrip[i])

        sw = self._sysstrip[0]
        bx = W - SYS_BARW
        self._text(bx - SYS_PAD - sw, self._sysstrip)
        fill = int(self.sysvol * SYS_BARW + 0.5)
        if fill > SYS_BARW:
            fill = SYS_BARW
        m = VOL_OUTLINE if self.sysmute else VOL_MASK
        for x in range(bx, bx + fill):
            row[x] |= m
        for x in range(bx + fill, W):
            row[x] |= VOL_BASE

        o = PAGE_FOOT * W
        for k in self._ink:
            self.planes[k][o:o + W] = row

    def render(self):
        self.draw_vol()
        self.draw_names()
        self.draw_dots()
        self.draw_foot()
