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

Under the curve is a **real meter of the system output**, not a decorative
fill: 21 bands rising from the floor, styled exactly like the mixer's app
meters -- same 2px dots, same peak markers riding the leading edge, same
fast-attack/slow-release ballistics.

It is also *accurate*, which the app meters cannot be.  Windows exposes no
per-session spectrum, so the mixer invents its band shape; the system output by
contrast can be captured, so host/sysmeter.py runs a real FFT on the WASAPI
loopback and sends the bands over.  What is drawn here is what is coming out of
the machine -- past the EQ, past the mix.

Every band height is precomputed as page masks (BAND_PAGES) with the dot
texture anchored to the BOTTOM, so a band is a few ORs and the dots stay put as
it moves rather than crawling with it.

The column separators run from the top down to the curve and stop there, which
is what makes the curve read as a surface with the dots beneath it.

Pure Python, no hardware imports, so host/preview_eq.py renders exactly what
the panel shows.
"""
import ballistics
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

HANDLE = 2                    # handle half-size: a 5x5 blob, corners off
PAD_R = 2                     # keep the value clear of the column edge

# ---- the meter under the curve ---------------------------------------------
# Styled exactly as the mixer's: 2px bands, and the run centred in its space.
# 21 bands at pitch 6 span 122 of the 128 px, so the margin is 3 either side.
NMETER = 21
BAND_PITCH = 6
BAND_W = 2
BAND_X0 = (W - ((NMETER - 1) * BAND_PITCH + BAND_W)) // 2
BANDX = tuple(BAND_X0 + i * BAND_PITCH for i in range(NMETER))
PEAK_W = 4                    # the marker overhangs its band, so it reads as
PEAK_DX = -1                  # a line rather than another dot

FALL = ballistics.FALL        # one home for all three meters; ballistics.py
PEAK_FALL = ballistics.PEAK_FALL


def _band_pages(h):
    """A band h pixels tall standing on the floor, as one mask per page.  The
    2px-on/2px-off texture is anchored to the bottom too, so the dots stay put
    as the band grows instead of crawling."""
    pages = bytearray(NPAGE)
    for i in range(h):
        if (i & 3) < 2:
            r = FLOOR_ROW - i
            pages[r >> 3] |= 1 << (r & 7)
    return bytes(pages)


BAND_PAGES = tuple(_band_pages(h) for h in range(PLOT_H + 1))


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
        self.band = [0.0] * NMETER         # the live meter, smoothed
        self.bpeak = [0.0] * NMETER
        # Text strips are cached, and this is not a micro-optimisation: the
        # header is six strings, and rebuilding them per frame measured 11.8 ms
        # -- three times the whole meter, and the single biggest cost on the
        # screen.  Names never change; a value's strip is rebuilt only when its
        # text actually differs.
        self._nstrip = tuple(gfx.text_strip(font, n, SPACING) for n in self.names)
        self._vstrip = [None] * NBAND
        self._scratch = bytearray(NPAGE * W)
        # The plot is two layers with very different update rates: the curve,
        # separators and handles move only when a band does, while the meter
        # moves 60 times a second.  Keeping the slow layer prebuilt turns a
        # meter tick into a memcpy plus 21 bands instead of redrawing 128
        # columns of curve as well.
        self._static = bytearray(NPAGE * W)
        self._ys = bytearray(W)
        for i in range(NBAND):
            self.set_label(i)

    # ---- header ----------------------------------------------------------
    def set_label(self, i, text=None):
        """The cell text for band i.  Defaults to the gain, which is what the
        resting screen shows; pass text to show a different parameter while it
        is being edited."""
        if text is None:
            text = '%+.1f' % self.gains[i]
        if text == self.label[i] and self._vstrip[i] is not None:
            return
        self.label[i] = text
        self._vstrip[i] = gfx.text_strip(self.font, text, SPACING)

    def draw_head(self):
        o = PAGE_HEAD * W
        for k in self._ink:
            self.planes[k][o:o + W] = _ZERO
        for c in range(NBAND):
            nw, nbuf = self._nstrip[c]
            vw, vbuf = self._vstrip[c]
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

    # ---- the meter -------------------------------------------------------
    def update(self, levels):
        """Feed one level per band (0..1) and advance the ballistics a step.
        Rise instantly, fall gradually, and let the peak marker fall ~6x slower
        so it detaches and settles -- exactly the mixer's meter."""
        n = len(levels)
        for i in range(NMETER):
            v = levels[i] if i < n else 0.0
            if v < 0.0:
                v = 0.0
            elif v > 1.0:
                v = 1.0
            x = self.band[i]
            x = v if v >= x else x + (v - x) * FALL
            self.band[i] = x
            p = self.bpeak[i]
            self.bpeak[i] = x if x >= p else p + (x - p) * PEAK_FALL

    # ---- the plot --------------------------------------------------------
    def set_curve(self, ys):
        """Rebuild the slow layer: the response curve, the separators that stop
        against it, the band handles and the floor.  Only a band change moves
        any of this, so it is built once per change rather than per frame.

        `ys` is one curve row per column, 0 at the top -- eqcurve.heights()."""
        st = self._static
        blank = _ZERO[:W]
        for p in range(NPAGE):
            st[p * W:(p + 1) * W] = blank

        prev = ys[0]
        for x in range(W):
            y = ys[x]
            if y > FLOOR_ROW:
                y = FLOOR_ROW
            # a segment between this column and the last, so a steep slope
            # stays a line instead of a dotted diagonal
            lo, hi = (prev, y) if prev <= y else (y, prev)
            for r in range(lo, hi + 1):
                st[(r >> 3) * W + x] |= 1 << (r & 7)
            prev = y

        # separators run from the top down to the curve and stop, which is what
        # makes it read as a surface with the meter beneath
        for sx in SEPX:
            end = ys[sx]
            if end > FLOOR_ROW:
                end = FLOOR_ROW
            for r in range(0, end + 1):
                st[(r >> 3) * W + sx] |= 1 << (r & 7)

        for i in range(NBAND):
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
                        st[(r >> 3) * W + x] |= 1 << (r & 7)

        o = (FLOOR_ROW >> 3) * W
        b = 1 << (FLOOR_ROW & 7)
        for x in range(W):
            st[o + x] |= b

    def draw_plot(self, ys=None):
        """Composite the live meter under the prebuilt curve layer.  Pass `ys`
        only when the curve has moved."""
        if ys is not None:
            self.set_curve(ys)
        sc = self._scratch
        st = self._static
        sc[:] = st

        # the meter, drawn into the copy; the curve is already above it
        for i in range(NMETER):
            h = int(self.band[i] * PLOT_H + 0.5)
            if h > PLOT_H:
                h = PLOT_H
            bx = BANDX[i]
            if h:
                pages = BAND_PAGES[h]
                for p in range(NPAGE):
                    m = pages[p]
                    if m:
                        o = p * W + bx
                        for x in range(o, o + BAND_W):
                            sc[x] |= m
            pk = self.bpeak[i]
            if pk > 0.0:
                d = int(pk * PLOT_H + 0.5)
                if d < 1:
                    d = 1
                elif d > PLOT_H:
                    d = PLOT_H
                r = FLOOR_ROW - d + 1 - ballistics.PEAK_GAP   # clear of the bar
                if r < 0:
                    r = 0
                o = (r >> 3) * W + bx + PEAK_DX
                m = 1 << (r & 7)
                lo = 0 if o < 0 else o
                for x in range(lo, o + PEAK_W):
                    sc[x] |= m

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
