"""Render the EQ mode on the PC with the same modules the device runs.

    python preview_eq.py

eqscreen.py and eqcurve.py import nothing but gfx and math, so this is exactly
what the panel shows -- including the mode bar, which comes from mixer.Mixer so
the two screens cannot drift apart.
"""
import _paths
import os
import sys

from PIL import Image

import ccfont
import eqcurve
import eqscreen
import frc
import gfx
import mixer

SCALE = 4
GAP = 6

# LOW is a low shelf, MID a peak, HIGH a high shelf -- the tone-control split.
SETUP = ((eqcurve.LSC, 120.0, 0.7),
         (eqcurve.PK, 1000.0, 0.9),
         (eqcurve.HSC, 4000.0, 0.7))


def build():
    scr = eqscreen.EqScreen(ccfont.TINY)
    cur = eqcurve.Curve(nband=eqscreen.NBAND)
    for i, (kind, f0, q) in enumerate(SETUP):
        cur.set(i, kind, f0, 0.0, q, True)
        scr.handles[i] = cur.column_of(f0)
    # the mode bar belongs to the mixer; borrow a real one so it is identical
    mx = mixer.Mixer(ccfont.TINY)
    mx.sysvol = 0.62
    while mx.set_mode(1):                       # settle the ticks on EQ
        pass
    return scr, cur, mx


def compose(scr, mx):
    """EQ screen on pages 0..6, the mixer's mode bar on page 7."""
    fb = bytearray(scr.planes[0])
    o = mixer.PAGE_FOOT * gfx.W
    fb[o:o + gfx.W] = mx.planes[0][o:o + gfx.W]
    return fb


def shots():
    scr, cur, mx = build()
    out = []

    def snap(tag):
        ys = cur.heights(eqscreen.PLOT_H)
        for i in range(eqscreen.NBAND):
            scr.gains[i] = cur.bands[i].db
            scr.handles[i] = cur.column_of(cur.bands[i].f0)
            scr.set_label(i)
        scr.render(ys)
        out.append((frc.avg_image([compose(scr, mx)], SCALE), tag))

    snap('flat: 0 dB is the middle row')

    cur.set(0, db=1.5)
    cur.set(2, db=1.0)
    snap('LOW +1.5, HIGH +1.0 (the sketch)')

    cur.set(0, db=6.0)
    cur.set(1, db=-5.0)
    cur.set(2, db=4.0)
    snap('a real shape: +6 / -5 / +4')

    cur.set(1, f0=300.0, q=3.0)
    snap('MID moved to 300 Hz, Q 3 -- a narrow notch')

    cur.set(0, db=-10.0)
    cur.set(1, db=0.0)
    cur.set(2, db=-8.0)
    snap('cuts: the curve dips below centre')

    scr.engine = False
    scr.render(None)
    out.append((frc.avg_image([compose(scr, mx)], SCALE),
                'no EQ engine attached to this output'))
    return out


def main(argv):
    imgs = shots()
    for _, tag in imgs:
        print('  ', tag)
    w, h = gfx.W * SCALE, gfx.H * SCALE
    canvas = Image.new('L', (w, (h + GAP) * len(imgs) - GAP), 128)
    for i, (im, _) in enumerate(imgs):
        canvas.paste(im, (0, i * (h + GAP)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'eq_preview.png')
    canvas.save(out)
    print('wrote %s (%d frames)' % (out, len(imgs)))


if __name__ == '__main__':
    main(sys.argv[1:])
