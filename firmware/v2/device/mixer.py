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

VOL_MASK = 0x7E               # y1..y6 within page 0
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
_MUTED = (0, 2)               # a muted app's level: half duty, still readable
_TRACK = (0,)                 # the unfilled part of a bar: quarter duty
_ALL = (0, 1, 2, 3)


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
        self.hist = [[0] * DOT_ROWS for _ in range(NCOL)]   # dots lit per row
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

    def push(self, levels):
        """Advance the trace one sample.  levels: NCOL floats 0..1."""
        for c in range(NCOL):
            h = self.hist[c]
            n = int(levels[c] * DOT_COLS + 0.5)
            if n < 0:
                n = 0
            elif n > DOT_COLS:
                n = DOT_COLS
            for r in range(DOT_ROWS - 1, 0, -1):     # scroll down
                h[r] = h[r - 1]
            h[0] = n

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
                n = h[r]
                if not n:
                    continue
                o = ROW_PAGE[r] * W + x0
                m = ROW_MASK[r]
                for k in _KEEP[r]:
                    fb = self.planes[k]
                    for d in range(n):
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
            # muted: half duty instead of solid black, so the level still reads
            # but the column is obviously not live
            for k in (_MUTED if self.mute[c] else _ALL):
                fb = self.planes[k]
                for x in range(x0, x0 + fill):
                    fb[o + x] |= VOL_MASK
            for k in _TRACK:                         # faint track for the rest
                fb = self.planes[k]
                for x in range(x0 + fill, x0 + COLW):
                    fb[o + x] |= VOL_MASK

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
        for k in _TRACK:
            fb = self.planes[k]
            for x in range(bx + fill, W):
                fb[o + x] |= VOL_MASK

    def render(self):
        self.draw_vol()
        self.draw_names()
        self.draw_dots()
        self.draw_foot()
