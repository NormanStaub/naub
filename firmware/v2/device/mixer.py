"""The naub V2 mixer screen: three app columns, each with a volume bar, a
name, and a scrolling dot trace of what that app is actually playing.

    +------------------------------------+
    | ####----- | ######--- | ########## |  volume bar   (page 0)
    | CHROME    | MUSIC     | SPOTIFY    |  name, marquee (page 1)
    | oooo      | ooooooo   |            |  newest sample (pages 2-6)
    | ooooo     | ooooo     |            |    |  scrolls down,
    | ooo       | oooooo    |            |    v  fading as it ages
    | PRESET 1  |||  SYS. ############   |  footer       (page 7)
    +------------------------------------+

Pure Python, no hardware imports, so host/preview_mixer.py renders exactly what
the panel shows.

Everything here is built around the page grid, because that is what makes it
cheap enough to animate under the FRC plane cycling:

  * the dot rows sit 2-per-page (4 px pitch), so lighting a dot is one `|=` on
    one byte -- no bit shifting across page boundaries, no per-pixel calls;
  * names are rendered once into a one-page gfx.text_strip() and the marquee is
    a byte-slice window into it, so scrolling a name costs nothing;
  * age is drawn as gray by putting older rows into fewer FRC planes, the same
    trick carousel.py uses for unfocused icons.
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
DOT_P0, DOT_P1 = 2, 6         # dot matrix band
PAGE_FOOT = 7                 # preset / system volume

# Volume bars are drawn PURE BINARY -- solid in every FRC plane or not at all.
# A half/quarter-duty bar visibly flickers: a large solid area shows the plane
# cycling far more than the small isolated dots of the trace do.  So the range
# is shown by a 1px baseline rather than a gray track, and a muted bar becomes
# a hollow outline rather than a dimmer fill.
VOL_MASK = 0x7E               # y1..y6 within page 0: the filled bar
VOL_OUTLINE = 0x42            # y1 and y6 only: the same bar, muted
VOL_BASE = 0x40               # y6 only: the empty part of the track
RULE_MASK = 0x80              # bottom row of a page, for horizontal rules
SPACING = 0                   # ccfont.TINY advances already carry side bearing;
                              # 1px more would push most app names into a marquee

# ---- dot matrix ------------------------------------------------------------
# 8 dots across a 42 px column (2 px dot, 5 px pitch) and 10 rows down the
# 5-page band (2 px dot, 4 px pitch -> exactly 2 rows per page).
DOT_COLS = 8
DOT_ROWS = 10
DOTX = tuple(1 + i * 5 for i in range(DOT_COLS))
ROW_PAGE = tuple(DOT_P0 + (r >> 1) for r in range(DOT_ROWS))
ROW_MASK = tuple(0x03 if (r & 1) == 0 else 0x30 for r in range(DOT_ROWS))

# Row 0 is the newest sample and is solid black; older rows are drawn into
# fewer planes so the trace fades as it scrolls down.  Indexes are row/age.
_KEEP = ((0, 1, 2, 3), (0, 1, 2, 3),
         (0, 1, 2), (0, 1, 2), (0, 1, 2),
         (0, 2), (0, 2), (0, 2),
         (0,), (0,))
_GRAY = (0, 2)                # chrome (rules, separators): half duty
_ALL = (0, 1, 2, 3)

# ---- trace styles ----------------------------------------------------------
# Windows gives us one number per app per moment (peak amplitude); there is no
# per-app spectrum to be had.  So the shape of a row is invented from that one
# number -- these are the ways of faking it.  Each returns an 8-bit pattern of
# which dots in the row are lit.
BARS, WAVE, SCOPE, SPECTRO = 0, 1, 2, 3
STYLE_NAMES = ('BARS', 'WAVE', 'SCOPE', 'SPECTRO')
NSTYLE = 4

# quarter-cycle sine, 16 steps -- avoids importing math on the device
_SIN = (0.0, 0.38, 0.71, 0.92, 1.0, 0.92, 0.71, 0.38,
        0.0, -0.38, -0.71, -0.92, -1.0, -0.92, -0.71, -0.38)
_BAND_W = (1.0, 0.97, 0.92, 0.86, 0.79, 0.71, 0.62, 0.52)   # fake band tilt
_BAND_DECAY = 0.72


class Mixer:
    def __init__(self, font, n=4, preset='PRESET 1'):
        self.font = font
        self.planes = [gfx.new() for _ in range(n)]
        self.preset = preset
        self.names = [''] * NCOL
        self._strip = [None] * NCOL      # (w, buf) from gfx.text_strip
        self._scroll = [0] * NCOL        # marquee offset, px
        self._step = [1] * NCOL          # marquee direction
        self.vol = [0.0] * NCOL          # 0..1
        self.mute = [False] * NCOL
        self.sysvol = 0.0
        self.style = WAVE
        self.hist = [[0] * DOT_ROWS for _ in range(NCOL)]   # dot pattern per row
        self._phase = [0] * NCOL                           # SCOPE sweep
        self._band = [[0.0] * DOT_COLS for _ in range(NCOL)]   # SPECTRO decay
        self.chrome()

    # ---- static furniture (draw once) ------------------------------------
    def chrome(self):
        """Rules and column separators.  Mid gray so the data stays dominant."""
        for k in _GRAY:
            fb = self.planes[k]
            o = PAGE_NAME * W
            for x in range(W):                       # rule under the names
                fb[o + x] |= RULE_MASK
            o = DOT_P1 * W
            for x in range(W):                       # rule above the footer
                fb[o + x] |= RULE_MASK
            for sx in SEPX:                          # column separators
                for p in range(PAGE_NAME, DOT_P1 + 1):
                    fb[p * W + sx] = 0xFF

    # ---- data in ---------------------------------------------------------
    def set_name(self, i, s):
        self.names[i] = s
        self._strip[i] = gfx.text_strip(self.font, s, SPACING) if s else None
        self._scroll[i] = 0
        self._step[i] = 1

    def pattern(self, c, lv):
        """One level -> the 8 dots of one row, per the current style."""
        if lv <= 0.0:
            self._phase[c] = 0
            return 0
        if lv > 1.0:
            lv = 1.0
        s = self.style

        if s == BARS:                       # left-aligned, height = level
            return (1 << int(lv * DOT_COLS + 0.5)) - 1

        if s == WAVE:                       # symmetric envelope about the centre
            n = int(lv * DOT_COLS + 0.5)
            if n < 1:
                n = 1
            a = (DOT_COLS - n) >> 1
            return ((1 << n) - 1) << a

        if s == SCOPE:                      # a single dot swinging like a trace
            p = (self._phase[c] + 3) & 15
            self._phase[c] = p
            x = int(3.5 + 3.5 * lv * _SIN[p] + 0.5)
            if x < 0:
                x = 0
            elif x >= DOT_COLS:
                x = DOT_COLS - 1
            return 1 << x

        b = self._band[c]                   # SPECTRO: faked bands with decay
        m = 0
        for d in range(DOT_COLS):
            v = b[d] * _BAND_DECAY
            hit = lv * _BAND_W[d] * (0.55 + 0.9 * _rand())
            if hit > v:
                v = hit
            b[d] = v
            if v > 0.32:
                m |= 1 << d
        return m

    def push(self, levels):
        """Advance the trace one sample.  levels: NCOL floats 0..1."""
        for c in range(NCOL):
            h = self.hist[c]
            for r in range(DOT_ROWS - 1, 0, -1):     # scroll down
                h[r] = h[r - 1]
            h[0] = self.pattern(c, levels[c])

    def set_style(self, s):
        self.style = s % NSTYLE
        for c in range(NCOL):
            self._phase[c] = 0
            for d in range(DOT_COLS):
                self._band[c][d] = 0.0
        return STYLE_NAMES[self.style]

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
        for k in _ALL:                               # clear the band
            fb = self.planes[k]
            for p in range(DOT_P0, DOT_P1 + 1):
                fb[p * W:(p + 1) * W] = _ZERO
        for c in range(NCOL):
            x0 = COLX[c]
            h = self.hist[c]
            for r in range(DOT_ROWS):
                bits = h[r]
                if not bits:
                    continue
                o = ROW_PAGE[r] * W + x0
                m = ROW_MASK[r]
                for k in _KEEP[r]:
                    fb = self.planes[k]
                    for d in range(DOT_COLS):
                        if (bits >> d) & 1:
                            x = o + DOTX[d]
                            fb[x] |= m
                            fb[x + 1] |= m
        for k in _GRAY:                              # separators cross the band
            fb = self.planes[k]
            for sx in SEPX:
                for p in range(DOT_P0, DOT_P1 + 1):
                    fb[p * W + sx] = 0xFF
        fb = self.planes[_GRAY[0]]
        o = DOT_P1 * W
        for x in range(W):
            fb[o + x] |= RULE_MASK
        self.planes[_GRAY[1]][o:o + W] = fb[o:o + W]

    def draw_names(self):
        o = PAGE_NAME * W
        for k in _ALL:
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
            for k in _ALL:
                self.planes[k][a:a + n] = src
        for k in _GRAY:                              # restore the rule + seps
            fb = self.planes[k]
            for x in range(W):
                fb[o + x] |= RULE_MASK
            for sx in SEPX:
                fb[o + sx] = 0xFF

    def draw_vol(self):
        o = PAGE_VOL * W
        for k in _ALL:
            self.planes[k][o:o + W] = _ZERO
        for c in range(NCOL):
            x0 = COLX[c]
            fill = int(self.vol[c] * COLW + 0.5)
            if fill > COLW:
                fill = COLW
            # muted -> hollow outline; live -> solid.  Binary either way.
            m = VOL_OUTLINE if self.mute[c] else VOL_MASK
            for k in _ALL:
                fb = self.planes[k]
                for x in range(x0, x0 + fill):
                    fb[o + x] |= m
                for x in range(x0 + fill, x0 + COLW):
                    fb[o + x] |= VOL_BASE

    def draw_foot(self):
        o = PAGE_FOOT * W
        for k in _ALL:
            self.planes[k][o:o + W] = _ZERO
        w, buf = gfx.text_strip(self.font, self.preset, SPACING)
        n = min(w, 60)
        for k in _ALL:
            self.planes[k][o:o + n] = memoryview(buf)[:n]
        sw, sbuf = gfx.text_strip(self.font, 'SYS.', SPACING)
        bar_w = 34
        sx = W - bar_w - 2 - sw
        for k in _ALL:
            self.planes[k][o + sx:o + sx + sw] = memoryview(sbuf)[:sw]
        bx = W - bar_w
        fill = int(self.sysvol * bar_w + 0.5)
        for k in _ALL:
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
