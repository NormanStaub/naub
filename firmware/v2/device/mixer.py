"""The naub V2 mixer screen: three app columns, each with a volume bar, a name
and a dot level meter for what that app is playing.

    +------------------------------------+
    | ####----- | ######--- | ########## |  volume bar   (page 0)
    | CHROME    | MUSIC     | SPOTIFY    |  name, marquee (page 1)
    | o o o o o | o o o o o |            |  meter hangs from the top
    | o o o o o | o o o o o |            |    |  and grows DOWN
    | --------- | o o o o o |            |    v  with the level
    |           | --------- |            |  peak marker at the leading edge
    | PRESET 1  |||  SYS. ############   |  footer       (page 7)
    +------------------------------------+

An upside-down mixer meter: the dots fill from the top down, and the thin
continuous line at the leading edge is the peak hold -- it jumps straight to
any new peak and then settles back smoothly when the signal drops.

Pure Python, no hardware imports, so host/preview_mixer.py renders exactly what
the panel shows.

Everything is built around the page grid, because that is what makes it cheap
enough to animate under the FRC plane cycling:

  * the meter's 11 possible fill levels are precomputed as page-aligned byte
    patterns (LEVEL_PAGES), so drawing a column is 5 slice copies plus the peak
    line, not a loop over 80 dots;
  * names render once into a one-page gfx.text_strip() and the marquee is a
    byte-slice window into it, so scrolling a long name costs nothing.

Bars and meter are drawn PURE BINARY -- solid in every FRC plane or not at all.
A large area at part duty visibly flickers; gray is kept for the chrome only.
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
# 8 dots across a 42px column (2px dot, 5px pitch) and 10 rows down the 5-page
# band (2px dot, 4px pitch -> exactly 2 rows per page, so a row never straddles
# a page boundary).
DOT_COLS = 8
DOT_ROWS = 10
DOTX = tuple(1 + i * 5 for i in range(DOT_COLS))
ROW_MASK = tuple(0x03 if (r & 1) == 0 else 0x30 for r in range(DOT_ROWS))
# the peak marker is the lower pixel of its row, drawn right across the column:
# a continuous line against the dotted rows, so it reads as a marker not a row
PEAK_MASK = tuple(0x02 if (r & 1) == 0 else 0x20 for r in range(DOT_ROWS))

# Meter ballistics, in fraction-of-the-gap per update.  Both rise instantly and
# fall gradually -- that asymmetry is what makes a meter look alive.  The peak
# marker falls much more slowly than the bar, so it detaches and settles.
FALL = 0.34
PEAK_FALL = 0.055

_GRAY = (0, 2)                # chrome (rules, separators): half duty
_ALL = (0, 1, 2, 3)


def _level_pages(n):
    """The n-rows-lit meter, as one byte pattern per page of the band."""
    pages = [bytearray(COLW) for _ in range(NPAGE)]
    for r in range(n):
        buf = pages[r >> 1]
        m = ROW_MASK[r]
        for dx in DOTX:
            buf[dx] |= m
            buf[dx + 1] |= m
    return tuple(bytes(b) for b in pages)


LEVEL_PAGES = tuple(_level_pages(n) for n in range(DOT_ROWS + 1))


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
        self.level = [0.0] * NCOL        # what the meter shows now
        self.peak = [0.0] * NCOL         # where the peak marker sits
        self._scratch = bytearray(NPAGE * COLW)
        self.chrome()

    # ---- static furniture (draw once) ------------------------------------
    def chrome(self):
        """Rules and column separators.  Mid gray so the data stays dominant."""
        for k in _GRAY:
            fb = self.planes[k]
            for p in (PAGE_NAME, DOT_P1):
                o = p * W
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
        """Feed one sample per column (0..1) and advance the ballistics."""
        for c in range(NCOL):
            v = levels[c]
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            lv = self.level[c]
            self.level[c] = v if v >= lv else lv + (v - lv) * FALL
            pk = self.peak[c]
            self.peak[c] = v if v >= pk else pk + (v - pk) * PEAK_FALL

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
        scratch = self._scratch
        mv = memoryview(scratch)
        for c in range(NCOL):
            n = int(self.level[c] * DOT_ROWS + 0.5)
            if n > DOT_ROWS:
                n = DOT_ROWS
            pages = LEVEL_PAGES[n]
            for i in range(NPAGE):
                scratch[i * COLW:(i + 1) * COLW] = pages[i]
            pk = self.peak[c]
            if pk > 0.0:                             # peak marker
                r = int(pk * DOT_ROWS + 0.5) - 1
                if r < 0:
                    r = 0
                elif r >= DOT_ROWS:
                    r = DOT_ROWS - 1
                base = (r >> 1) * COLW
                m = PEAK_MASK[r]
                for x in range(base, base + COLW):
                    scratch[x] |= m
            x0 = COLX[c]
            for i in range(NPAGE):
                o = (DOT_P0 + i) * W + x0
                src = mv[i * COLW:(i + 1) * COLW]
                for k in _ALL:
                    self.planes[k][o:o + COLW] = src
        for k in _GRAY:                              # separators cross the band
            fb = self.planes[k]
            for sx in SEPX:
                for p in range(DOT_P0, DOT_P1 + 1):
                    fb[p * W + sx] = 0xFF
            o = DOT_P1 * W
            for x in range(W):
                fb[o + x] |= RULE_MASK

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
