"""The naub V2 mixer screen: three app columns, each with a volume bar, a name
and a dot level meter for what that app is playing.

    +------------------------------------+
    | ####----- | ######--- | ########## |  volume bar   (page 0)
    | CHROME    | MUSIC     | SPOTIFY    |  name, marquee (page 1)
    | -   - -   | -     -   |            |  8 bands per column, standing on
    | :   : :   | :     :   |            |  the floor and bouncing UP, each
    | : : : : : | : :   : : |            |  with its own peak marker riding
    | : : : : : | : : : : : |            |  its leading edge
    | PRESET 1  |||  SYS. ############   |  footer       (page 7)
    +------------------------------------+

A mixer meter: eight bands per app rise from the bottom, each with a short line
at its leading edge -- the peak hold, which jumps straight to any new peak and
then settles back smoothly when the signal drops.

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
    byte-slice window into it, so scrolling a long name costs nothing.

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
PAGE_FOOT = 7                 # preset / system volume

VOL_MASK = 0x7E               # y1..y6 within page 0: the filled bar
VOL_OUTLINE = 0x42            # y1 and y6 only: the same bar, muted
VOL_BASE = 0x40               # y6 only: the empty part of the track
RULE_MASK = 0x80              # bottom row of a page, for horizontal rules
SPACING = 0                   # ccfont.TINY advances already carry side bearing;
                              # 1px more would push most app names into a marquee

# ---- meter -----------------------------------------------------------------
# Each app column is 8 bands across (2px wide, 5px pitch), and each band hangs
# from the top of the 5-page band and grows DOWN.
#
# Height is in PIXELS, not dot rows: a band's fill is any of 0..40, so the
# ballistics move at the finest step the panel has.  The dot texture comes from
# the pattern (2px lit, 2px clear) rather than from quantising the height, so
# the meter still reads as a dot matrix while moving smoothly.
NBAND = 8
BANDX = tuple(1 + i * 5 for i in range(NBAND))
BAND_W = 2                    # px wide
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
TILT = (1.0, 0.98, 0.95, 0.90, 0.84, 0.77, 0.68, 0.58)
NOISE_KEEP = 0.82             # how much of last frame's wander is retained
NOISE_GAIN = 0.55             # how far a band can stray from the tilt

# Everything on this screen is binary -- fully inked or absent -- so a column's
# pixels are built once into a scratch buffer and slice-copied into whatever
# planes exist, which is what keeps the 60 Hz meter affordable.
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
    def __init__(self, font, n=1, preset='PRESET 1'):
        """n = number of FRC planes.  This screen is entirely binary, so ONE
        plane is enough and is the default: nothing to cycle means no shimmer
        at all, and a quarter of the drawing work.  Pass n=4 if you add gray
        elements back."""
        self.font = font
        self.planes = [gfx.new() for _ in range(n)]
        self._ink = tuple(range(n))
        self.preset = preset
        self.names = [''] * NCOL
        self._strip = [None] * NCOL      # (w, buf) from gfx.text_strip
        self._scroll = [0] * NCOL        # marquee offset, px
        self._step = [1] * NCOL          # marquee direction
        self.vol = [0.0] * NCOL          # 0..1
        self.mute = [False] * NCOL
        self.sysvol = 0.0
        self.level = [0.0] * NCOL        # the column's own level, smoothed
        # per band: what it shows, where its peak marker sits, and its wander
        self.band = [[0.0] * NBAND for _ in range(NCOL)]
        self.bpeak = [[0.0] * NBAND for _ in range(NCOL)]
        self._noise = [[0.0] * NBAND for _ in range(NCOL)]
        self._scratch = bytearray(NPAGE * COLW)
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
        # The last page is cleared to the rule row, so the line above the
        # footer comes for free.
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
        for k in self._ink:                               # restore the rule + seps
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

    def draw_foot(self):
        o = PAGE_FOOT * W
        for k in self._ink:
            self.planes[k][o:o + W] = _ZERO
        w, buf = gfx.text_strip(self.font, self.preset, SPACING)
        n = min(w, 60)
        for k in self._ink:
            self.planes[k][o:o + n] = memoryview(buf)[:n]
        sw, sbuf = gfx.text_strip(self.font, 'SYS.', SPACING)
        bar_w = 34
        sx = W - bar_w - 2 - sw
        for k in self._ink:
            self.planes[k][o + sx:o + sx + sw] = memoryview(sbuf)[:sw]
        bx = W - bar_w
        fill = int(self.sysvol * bar_w + 0.5)
        for k in self._ink:
            fb = self.planes[k]
            for x in range(bx, bx + fill):
                fb[o + x] |= VOL_MASK
            for x in range(bx + fill, W):
                fb[o + x] |= VOL_BASE

    def render(self):
        self.draw_vol()
        self.draw_names()
        self.draw_dots()
        self.draw_foot()
