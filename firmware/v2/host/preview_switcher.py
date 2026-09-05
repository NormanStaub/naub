"""Render the switcher carousel on the PC, using the same carousel.py the
device runs — WYSIWYG check before flashing.

    python preview_switcher.py            # settled frames, one per app
    python preview_switcher.py --anim     # one scroll transition, frame by frame

Writes switcher_preview.png: each row is a time-averaged frame (frc.avg_image),
i.e. what the panel's LC response actually shows.
"""
import _paths
import os, sys
from PIL import Image
import ccfont, carousel, frc, gfx
import switcher_data as data

SCALE = 4
GAP = 6


def frames(anim):
    car = carousel.Carousel(data.ICONS, data.APPS)
    n = len(data.APPS)
    if anim:
        # the same easing demo_switcher.py runs: 1 -> 2, sampled every step
        pos, target = 1.0, 2.0
        car.set_focus(ccfont.SML, 1)
        for _ in range(8):
            car.render(pos)
            yield car, 'pos %.2f' % pos
            pos += (target - pos) * 0.3
        return
    for i in range(n):
        car.render(float(i))
        car.set_focus(ccfont.SML, i)
        yield car, '%d %s' % (i, data.APPS[i])


def main(argv):
    anim = '--anim' in argv
    shots = []
    for car, tag in frames(anim):
        shots.append((frc.avg_image(car.planes, SCALE), tag))
        print('  ', tag)
    w = gfx.W * SCALE
    h = gfx.H * SCALE
    canvas = Image.new('L', (w, (h + GAP) * len(shots) - GAP), 128)
    for i, (im, _) in enumerate(shots):
        canvas.paste(im, (0, i * (h + GAP)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'switcher_preview.png')
    canvas.save(out)
    print('wrote %s (%d frames)' % (out, len(shots)))


if __name__ == '__main__':
    main(sys.argv[1:])
