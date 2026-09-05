"""Grayscale test planes for temporal dithering on the ST7565R.

Bar modes render a stepped bar (white -> black); gradient modes show a
smooth horizontal ramp from precomputed FRC planes (graygrad.py):

  0 1F SPATIAL   static spatial dither only (flicker-free reference)
  1 2F TEMPORAL  2 alternating frames; mid levels are checker-split 50%
  2 3F TEMPORAL  3 frames, band-uniform on/off (worst-case flicker)
  3 3F HYBRID    3 frames, on-pixels phase-distributed by (x+y)%3
  4 4F HYBRID    4 frames, 5 levels, phase-rotated
  5 GRAD 1F      gradient, static Bayer dither
  6 GRAD 4F      gradient, 4-plane FRC + Bayer residual
  7 GRAD 6F      gradient, 6-plane FRC + Bayer residual

build() returns a list of framebuffers (bitplanes) to be cycled at max
frame rate.  Pure Python: runs on CPython for preview and CircuitPython.
"""
import gfx
import graygrad
from ccfont import SML

TOP = 16                    # test area is y=16..63 (pages 2..7); label above
MODES = ("1F SPATIAL", "2F TEMPORAL", "3F TEMPORAL", "3F HYBRID",
         "4F HYBRID", "GRAD 1F", "GRAD 4F", "GRAD 6F")
PLANES = (1, 2, 3, 3, 4, 1, 4, 6)
LEVELS = (4, 4, 4, 4, 5, 0, 0, 0)          # bar band count; 0 = gradient mode
GRADS = (graygrad.P1, graygrad.P4, graygrad.P6)


def _bit(mode, k, L, p):
    # k = plane index, L = band level, p = (x+y) % 12
    top = LEVELS[mode] - 1
    if L == 0:
        return 0
    if L == top:
        return 1
    if mode == 0:                       # spatial dither, duty L/3, one plane
        return 1 if (p % 3) < L else 0
    if mode == 1:                       # 2-frame: both mid levels 50%, checker-split
        return 1 if (p % 2) == k else 0
    if mode == 2:                       # 3-frame, band-uniform: on in L of 3 frames
        return 1 if k < L else 0
    if mode == 3:                       # 3-frame hybrid, phase spread
        if L == 1:
            return 1 if k == (p % 3) else 0
        return 1 if k != (p % 3) else 0
    # mode 4: 4-frame, on in L consecutive planes starting at phase
    return 1 if (k - p) % 4 < L else 0


def build(mode, label):
    if LEVELS[mode] == 0:               # gradient: copy precomputed planes
        planes = [bytearray(b) for b in GRADS[mode - 5]]
        set_label(planes, label)
        return planes
    n = PLANES[mode]
    levels = LEVELS[mode]
    # tiny LUT: page byte for (plane, level, column phase); period 12 covers
    # the %2, %3 and %4 patterns.  y = page*8 + yy.
    lut = [[[0] * 12 for _ in range(levels)] for _ in range(n)]
    for k in range(n):
        for L in range(levels):
            for ph in range(12):
                b = 0
                for yy in range(8):
                    if _bit(mode, k, L, (ph + yy) % 12):
                        b |= 1 << yy
                lut[k][L][ph] = b
    planes = []
    for k in range(n):
        fb = gfx.new()
        row = lut[k]
        for page in range(TOP >> 3, gfx.PAGES):
            base = page * gfx.W
            po = (page * 8) % 12
            for x in range(gfx.W):
                fb[base + x] = row[x * levels // gfx.W][(po + x) % 12]
        planes.append(fb)
    set_label(planes, label)
    return planes


def set_label(planes, label):
    # label lives in pages 0..1 (y<16); solid in every plane => steady text
    lw = gfx.W * 2
    for fb in planes:
        for i in range(lw):
            fb[i] = 0
        gfx.text_center(fb, SML, label, 0)
