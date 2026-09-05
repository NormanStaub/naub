"""The EQ mode: three bands over a filled response curve.

    +------------------------------------+
    | LOW +1.5 | MID +0.0 | HIGH +1.0    |  band + gain, one per knob   page 0
    |----------------------------------- |
    |  __O__   |          |              |  the response curve, with a
    | :::::::  |          |        _O__  |  handle on each band, over a
    | ::::::::::   ___O___|______:::::::|  dot fill that hangs from it
    | ::::::::::::::::::::::::::::::::::|  pages 1-6, 0 dB dead centre
    |------------------------------------|
    | EQ ||             SYS. #########   |  mode bar stays put          page 7
    +------------------------------------+

Same three-column frame as the mixer, for the same reason: there are three top
knobs, so there are three bands.  LOW is a low shelf, MID a peak, HIGH a high
shelf -- the classic tone-control split, and the one Equalizer APO implements
with the coefficients eqcurve draws from.

**0 dB is the vertical middle of the plot**, so a flat EQ is a straight line
across the centre and the curve reads as deviation from flat in both
directions.  The plot is 48 rows, pages 1..6, so the centre lands on a row
rather than between two.

The dot fill hangs from the curve down to the floor, on a grid anchored to the
BOTTOM so the dots stay put as the curve moves rather than crawling with it --
the same reasoning as the mixer's meter bands.  Every possible curve height is
precomputed as page masks (FILL), so drawing a column is six byte writes and a
redraw is ~768 of them rather than 6000 per-pixel calls.

The column separators run from the top down to the curve and stop there, which
is what makes the curve read as a surface with the dots beneath it.

Pure Python, no hardware imports, so host/preview_eq.py renders exactly what
the panel shows.
"""
import gfx

W = gfx.W
_ZERO = bytes(W)

NBAND = 3
NAMES = ('LOW', 'MID', 'HIGH')

# ---- layout: the mixer's frame, with the plot where the meter would be -----
PAGE_HEAD = 0                 # band names + their gains
PLOT_P0, PLOT_P1 = 1, 6       # the curve
NPAGE = PLOT_P1 - PLOT_P0 + 1
PLOT_H = NPAGE * 8            # 48 rows
PLOT_Y0 = PLOT_P0 * 8
PAGE_FOOT = 7                 # the mode bar, drawn by mixer.Mixer

COLX = (0, 43, 86)            # matches mixer.COLX -- three knobs, three cells
COLW = 42
SEPX = (42, 85)

HEAD_RULE = 0x80              # bottom row of page 0
FLOOR_ROW = PLOT_H - 1        # the rule above the mode bar, in plot rows
SPACING = 0

DOT_X = 5                     # dot grid: 2 px dots on a 5 px pitch, both
DOT_Y = 5                     # axes.  4 px pitch reads as a solid grey wash
                              # at this size; 5 keeps it a field of dots.
HANDLE = 2                    # handle half-size: a 5x5 blob, corners off
PAD_R = 2                     # keep the value clear of the column edge


def _fill_table():
    """For a dotted column whose curve sits at row `top`, the page masks of the
    fill below it.  Anchored to the bottom so the dots do not crawl."""
    out = []
    for top in range(PLOT_H + 1):
        pages = bytearray(NPAGE)
        for r in range(top, PLOT_H):
            if ((FLOOR_ROW - r) % DOT_Y) < 2:
                pages[r >> 3] |= 1 << (r & 7)
        out.append(bytes(pages))
    return tuple(out)


FILL = _fill_table()


def _is_dot_col(x):
    return (x % DOT_X) < 2


class EqScreen:
    def __init__(self, font, names=NAMES, n=1):
        self.font = font
        self.planes = [gfx.new() for _ in range(n)]
        self._ink = tuple(range(n))
        self.names = tuple(names)
        self.gains = [0.0] * NBAND
        self.label = [''] * NBAND          # what the header shows per band
        self.handles = [W // 2] * NBAND    # column of each band's centre freq
        self.engine = True                 # False -> say so instead of lying
        self._scratch = bytearray(NPAGE * W)
        self._ys = bytearray(W)
        for i in range(NBAND):
            self.set_label(i)

    # ---- header ----------------------------------------------------------
    def set_label(self, i, text=None):
        """The cell text for band i.  Defaults to name + gain, which is what
        the resting screen shows; pass text to show a different parameter
        while it is being edited."""
        if text is None:
            text = '%+.1f' % self.gains[i]
        self.label[i] = text

    def draw_head(self):
        o = PAGE_HEAD * W
        for k in self._ink:
            self.planes[k][o:o + W] = _ZERO
        for c in range(NBAND):
            nm = self.names[c]
            val = self.label[c]
            nw, nbuf = gfx.text_strip(self.font, nm, SPACING)
            vw, vbuf = gfx.text_strip(self.font, val, SPACING)
            x0 = COLX[c]
            # name hard left, value hard right: the value is what changes, and
            # a centred pair would jitter sideways every time it did.
            for j in range(min(nw, COLW)):
                self._or(o + x0 + 1 + j, nbuf[j])
            vx = x0 + COLW - PAD_R - vw
            if vx < x0 + nw + 2:
                vx = x0 + nw + 2
            for j in range(vw):
                if vx + j < x0 + COLW:
                    self._or(o + vx + j, vbuf[j])
        for k in self._ink:
            fb = self.planes[k]
            for x in range(W):
                fb[o + x] |= HEAD_RULE
            for sx in SEPX:
                fb[o + sx] = 0xFF

    def _or(self, idx, val):
        for k in self._ink:
            self.planes[k][idx] |= val

    # ---- the plot --------------------------------------------------------
    def draw_plot(self, ys):
        """`ys` is one curve row per column, 0 at the top -- eqcurve.heights().

        Builds the whole band in a scratch buffer and slice-copies it, so there
        is no clear pass and no per-pixel work."""
        sc = self._scratch
        blank = _ZERO[:W]
        for p in range(NPAGE):
            sc[p * W:(p + 1) * W] = blank

        prev = ys[0]
        for x in range(W):
            y = ys[x]
            if y > FLOOR_ROW:
                y = FLOOR_ROW
            if _is_dot_col(x):
                m = FILL[y]
                for p in range(NPAGE):
                    if m[p]:
                        sc[p * W + x] |= m[p]
            # the curve itself: a segment between this column and the last, so
            # a steep slope stays a line instead of a dotted diagonal
            lo, hi = (prev, y) if prev <= y else (y, prev)
            for r in range(lo, hi + 1):
                sc[(r >> 3) * W + x] |= 1 << (r & 7)
            prev = y

        # separators: from the top of the plot down to the curve, so the curve
        # reads as a surface they stop against
        for sx in SEPX:
            end = ys[sx]
            if end > FLOOR_ROW:
                end = FLOOR_ROW
            for r in range(0, end + 1):
                sc[(r >> 3) * W + sx] |= 1 << (r & 7)

        for i in range(NBAND):                     # handles, on the curve
            hx = self.handles[i]
            hy = ys[hx] if 0 <= hx < W else 0
            for dx in range(-HANDLE, HANDLE + 1):
                x = hx + dx
                if not (0 <= x < W):
                    continue
                for dy in range(-HANDLE, HANDLE + 1):
                    # drop the corners: a bare square reads as a glitch in the
                    # curve, a rounded blob reads as a handle on it
                    if abs(dx) == HANDLE and abs(dy) == HANDLE:
                        continue
                    r = hy + dy
                    if 0 <= r <= FLOOR_ROW:
                        sc[(r >> 3) * W + x] |= 1 << (r & 7)

        for x in range(W):                         # the floor
            sc[(FLOOR_ROW >> 3) * W + x] |= 1 << (FLOOR_ROW & 7)

        mv = memoryview(sc)
        for p in range(NPAGE):
            o = (PLOT_P0 + p) * W
            src = mv[p * W:(p + 1) * W]
            for k in self._ink:
                self.planes[k][o:o + W] = src

    def draw_no_engine(self):
        """No EQ engine on the current output: say so rather than draw a curve
        that shapes nothing.  Being installed is not being attached -- see
        host/eqapo.py."""
        sc = self._scratch
        blank = _ZERO[:W]
        for p in range(NPAGE):
            sc[p * W:(p + 1) * W] = blank
        for row, msg in ((1, 'NO EQ ENGINE'), (3, 'ON THIS OUTPUT')):
            w, buf = gfx.text_strip(self.font, msg, SPACING)
            x0 = (W - w) >> 1
            for j in range(w):
                sc[row * W + x0 + j] |= buf[j]
        for x in range(W):
            sc[(FLOOR_ROW >> 3) * W + x] |= 1 << (FLOOR_ROW & 7)
        mv = memoryview(sc)
        for p in range(NPAGE):
            o = (PLOT_P0 + p) * W
            src = mv[p * W:(p + 1) * W]
            for k in self._ink:
                self.planes[k][o:o + W] = src

    def render(self, ys=None):
        self.draw_head()
        if self.engine and ys is not None:
            self.draw_plot(ys)
        else:
            self.draw_no_engine()
