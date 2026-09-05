"""The EQ screen's curve: the summed response of N biquads, one y per column.

    cur = eqcurve.Curve(nband=5)
    cur.set(0, 'PK', 60.0, 6.0, 0.9)      # type, Hz, dB, Q
    ys = cur.heights(40)                  # 40px tall band -> y per column

Pure Python, no hardware imports, so host/preview_eq.py renders exactly what
the panel shows.

**Why it is built this way.** Measured on the RP2350 at CircuitPython 10.3.0,
evaluating all five bands across 128 columns costs ~31 ms, against a 2.7 ms
frame budget -- so the curve cannot be recomputed per frame, and at 32 redraws
a second it would visibly lag a fast knob.  Two things fix that:

  * **the trig is precomputed once.**  Each column's frequency is fixed, so
    cos(w), sin(w), cos(2w) and sin(2w) never change; only the coefficients do.
    That turns a magnitude evaluation into a handful of multiply-adds.
  * **only the band that moved is recomputed.**  Each band keeps its own
    magnitude-squared array, and the screen curve is their product.  Turning a
    knob redoes one band and re-multiplies, not all five -- about a quarter of
    the work.

The magnitude is evaluated analytically from the biquad coefficients rather
than by running the filter: for H(z) = (b0 + b1 z^-1 + b2 z^-2)/(1 + a1 z^-1 +
a2 z^-2) on the unit circle, the numerator is
(b0 + b1 cos w + b2 cos 2w) - j(b1 sin w + b2 sin 2w), and |H|^2 is the ratio of
the squared magnitudes.  Kept as magnitude SQUARED throughout so the per-column
logarithm happens once at the end, not once per band.

Coefficients are the RBJ cookbook forms, which is what Equalizer APO implements
-- so the curve on the panel is the curve the audio actually gets, not an
illustration of one.
"""
import math

W = 128                       # one column per display pixel
FMIN, FMAX = 20.0, 20000.0    # the span the curve covers, log spaced
SR = 48000.0                  # sample rate; set_rate() if the endpoint differs
MAXDB = 12.0                  # full scale of the plot, +/-

# Filter types, named as Equalizer APO names them so the two cannot drift.
PK, LSC, HSC = 'PK', 'LSC', 'HSC'

_LOG10 = 2.302585092994046
_INV_LOG10 = 0.4342944819032518


def _tables(sr):
    """cos/sin of w and 2w at every column.  The frequencies never move, so
    this is the only trig the curve ever does."""
    c1 = []
    s1 = []
    c2 = []
    s2 = []
    k = math.log(FMAX / FMIN) / (W - 1.0)
    for x in range(W):
        w = 2.0 * math.pi * (FMIN * math.exp(k * x)) / sr
        c1.append(math.cos(w))
        s1.append(math.sin(w))
        c2.append(math.cos(2.0 * w))
        s2.append(math.sin(2.0 * w))
    return c1, s1, c2, s2


def coeffs(kind, f0, db, q, sr=SR):
    """RBJ cookbook biquad, normalised by a0 -> (b0, b1, b2, a1, a2)."""
    a = 10.0 ** (db / 40.0)
    w0 = 2.0 * math.pi * f0 / sr
    if w0 > 3.14159:                       # above Nyquist: nothing to do
        w0 = 3.14159
    cw = math.cos(w0)
    sw = math.sin(w0)
    alpha = sw / (2.0 * q)
    if kind == PK:
        b0 = 1.0 + alpha * a
        b1 = -2.0 * cw
        b2 = 1.0 - alpha * a
        a0 = 1.0 + alpha / a
        a1 = -2.0 * cw
        a2 = 1.0 - alpha / a
    else:
        # Shelves need 2*sqrt(A)*alpha; the sign of the (A-1)cos terms is what
        # separates low shelf from high.
        sa = 2.0 * math.sqrt(a) * alpha
        ap1 = a + 1.0
        am1 = a - 1.0
        if kind == LSC:
            b0 = a * (ap1 - am1 * cw + sa)
            b1 = 2.0 * a * (am1 - ap1 * cw)
            b2 = a * (ap1 - am1 * cw - sa)
            a0 = ap1 + am1 * cw + sa
            a1 = -2.0 * (am1 + ap1 * cw)
            a2 = ap1 + am1 * cw - sa
        else:                              # HSC
            b0 = a * (ap1 + am1 * cw + sa)
            b1 = -2.0 * a * (am1 + ap1 * cw)
            b2 = a * (ap1 + am1 * cw - sa)
            a0 = ap1 - am1 * cw + sa
            a1 = 2.0 * (am1 - ap1 * cw)
            a2 = ap1 - am1 * cw - sa
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


class Band:
    __slots__ = ('kind', 'f0', 'db', 'q', 'on')

    def __init__(self, kind=PK, f0=1000.0, db=0.0, q=1.0, on=False):
        self.kind = kind
        self.f0 = f0
        self.db = db
        self.q = q
        self.on = on


class Curve:
    def __init__(self, nband=5, sr=SR):
        self.sr = sr
        self._c1, self._s1, self._c2, self._s2 = _tables(sr)
        self.bands = [Band() for _ in range(nband)]
        # |H|^2 per column, per band, and their running product
        self._mag = [[1.0] * W for _ in range(nband)]
        self._live = []           # indices whose magnitude is not all-ones
        self._prod = [1.0] * W
        self._ys = bytearray(W)
        self._dirty = True

    # ---- data in ---------------------------------------------------------
    def set_rate(self, sr):
        """The endpoint's sample rate.  Changing it moves every column's
        frequency, so everything is recomputed."""
        if sr == self.sr:
            return
        self.sr = sr
        self._c1, self._s1, self._c2, self._s2 = _tables(sr)
        for i in range(len(self.bands)):
            self._band_mag(i)
        self._remix()

    def set(self, i, kind=None, f0=None, db=None, q=None, on=None):
        """Change one band and recompute only that band."""
        b = self.bands[i]
        if kind is not None:
            b.kind = kind
        if f0 is not None:
            b.f0 = f0
        if db is not None:
            b.db = db
        if q is not None:
            b.q = q
        if on is not None:
            b.on = on
        self._band_mag(i)
        self._remix()

    # ---- the expensive parts ---------------------------------------------
    def _band_mag(self, i):
        """|H|^2 at every column for one band.  This is the ~6 ms."""
        m = self._mag[i]
        b = self.bands[i]
        if not b.on or (b.db == 0.0 and b.kind == PK):
            # An off or flat band is the identity, so remember that it can be
            # skipped entirely rather than multiplying 128 ones into the
            # product.  Most of the time most bands are in this state.
            if i in self._live:
                self._live.remove(i)
            for x in range(W):
                m[x] = 1.0
            return
        if i not in self._live:
            self._live.append(i)
        b0, b1, b2, a1, a2 = coeffs(b.kind, b.f0, b.db, b.q, self.sr)
        c1 = self._c1
        s1 = self._s1
        c2 = self._c2
        s2 = self._s2
        for x in range(W):
            u = c1[x]
            v = s1[x]
            p = c2[x]
            r = s2[x]
            nr = b0 + b1 * u + b2 * p
            ni = b1 * v + b2 * r
            dr = 1.0 + a1 * u + a2 * p
            di = a1 * v + a2 * r
            m[x] = (nr * nr + ni * ni) / (dr * dr + di * di)

    def _remix(self):
        """Product of the per-band magnitudes -- the only thing a knob move
        redoes besides its own band."""
        pr = self._prod
        live = self._live
        if not live:
            for x in range(W):
                pr[x] = 1.0
        else:
            m0 = self._mag[live[0]]
            for x in range(W):
                pr[x] = m0[x]
            for i in live[1:]:
                m = self._mag[i]
                for x in range(W):
                    pr[x] *= m[x]
        self._dirty = True

    def heights(self, h, maxdb=MAXDB):
        """-> bytearray of one y per column, 0 at the TOP of an h-px band.

        0 dB lands on the middle row, +maxdb at the top, -maxdb at the bottom;
        anything beyond is clamped rather than wrapped."""
        ys = self._ys
        pr = self._prod
        half = h * 0.5
        scale = half / maxdb
        top = h - 1
        for x in range(W):
            p = pr[x]
            # 10*log10(|H|^2) == 20*log10(|H|); one log per column, at the end
            db = 4.342944819032518 * math.log(p) if p > 1e-9 else -90.0
            v = half - db * scale
            ys[x] = 0 if v < 0.0 else (top if v > top else int(v))
        self._dirty = False
        return ys

    def flat(self):
        for i, b in enumerate(self.bands):
            b.db = 0.0
            b.on = False
            self._band_mag(i)
        self._remix()

    def db_at(self, x):
        """The curve's dB at one column -- for a readout, without heights()."""
        p = self._prod[x]
        return 4.342944819032518 * math.log(p) if p > 1e-9 else -90.0

    def column_of(self, f0):
        """Which column a frequency lands on, for drawing a band's handle."""
        if f0 <= FMIN:
            return 0
        if f0 >= FMAX:
            return W - 1
        k = (W - 1.0) / math.log(FMAX / FMIN)
        return int(math.log(f0 / FMIN) * k + 0.5)


# ---- knob mappings ---------------------------------------------------------
# Frequency and Q are LOG mapped: a linear knob over 20 Hz - 20 kHz would spend
# its first two detents on the bottom four octaves and be unusable.

def pos_to_freq(p):
    """0..1 -> Hz."""
    if p < 0.0:
        p = 0.0
    elif p > 1.0:
        p = 1.0
    return FMIN * math.exp(math.log(FMAX / FMIN) * p)


def freq_to_pos(f):
    if f <= FMIN:
        return 0.0
    if f >= FMAX:
        return 1.0
    return math.log(f / FMIN) / math.log(FMAX / FMIN)


QMIN, QMAX = 0.3, 10.0


def pos_to_q(p):
    if p < 0.0:
        p = 0.0
    elif p > 1.0:
        p = 1.0
    return QMIN * math.exp(math.log(QMAX / QMIN) * p)


def q_to_pos(q):
    if q <= QMIN:
        return 0.0
    if q >= QMAX:
        return 1.0
    return math.log(q / QMIN) / math.log(QMAX / QMIN)
