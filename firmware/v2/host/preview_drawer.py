"""Render the app-switcher dropdown on the PC with the same drawer.py the
device runs.

    python preview_drawer.py            # opening, scrolling, settled
    python preview_drawer.py --live     # the app list and levels from real audio

The drawer composites over a real mixer.Mixer frame, so what comes out is the
whole screen -- volume bars and footer included -- exactly as the panel shows
it, which is the point of keeping both widgets free of hardware imports.
"""
import _paths
import os, random, sys, time
from PIL import Image
import ccfont, drawer, frc, gfx, mixer

SCALE = 4
GAP = 6
COLS = ('CHROME', 'SPOTIFY', 'DISCORD')


def build(names=COLS, sel=0):
    m = mixer.Mixer(ccfont.TINY, preset='APP CTRL')
    for i, nm in enumerate(names):
        m.set_name(i, nm)
    m.vol = [0.45, 0.70, 1.0]
    m.sysvol = 0.62
    random.seed(5)
    for _ in range(30):
        m.update([0.85, 0.60, 0.30])
    m.render()
    return m


def shot(m, dr, sel, reveal):
    dr.set_background(m.planes[0],
                      (mixer.PAGE_NAME, mixer.COLX[sel], mixer.COLW))
    dr.compose(reveal)
    return frc.avg_image(dr.planes, SCALE)


def demo_states():
    import switcher_data
    m = build()
    dr = drawer.Drawer(switcher_data.ICONS, switcher_data.APPS,
                       ccfont.TINY, ccfont.SML)
    dr.set_apps(('SPOTIFY', 'CHROME', 'DISCORD', 'FIREFOX', 'CLAUDE'))
    dr.set_focus(0)
    sel = 0

    # opening: the meters below the panel edge are still live
    for r in (10, 24):
        dr.lvl, dr.pk = 0.70, 0.86
        dr.draw_shelf(0.0)
        dr.draw_row()
        yield shot(m, dr, sel, r), 'sliding down (%d px)' % r

    # settled on the first app
    for _ in range(20):
        dr.update(0.72)
    dr.draw_shelf(0.0)
    dr.draw_row()
    yield shot(m, dr, sel, drawer.H), 'open: counter, shelf, level of SPOTIFY'

    # mid-scroll, between two apps
    dr.draw_shelf(1.45)
    yield shot(m, dr, sel, drawer.H), 'scrolling: 24px focus, 16px neighbours'

    # settled on an app we have no icon for -> placeholder with its initial
    dr.set_focus(3)
    for _ in range(30):
        dr.update(0.12)
    dr.draw_shelf(3.0)
    dr.draw_row()
    yield shot(m, dr, sel, drawer.H), 'no icon for FIREFOX: placeholder + peak tick'


def live():
    import appmeter, switcher_data
    src = appmeter.AppMeters()
    cols = [n.upper() for n in src.top(mixer.NCOL)]
    m = build(cols)
    dr = drawer.Drawer(switcher_data.ICONS, switcher_data.APPS,
                       ccfont.TINY, ccfont.SML)
    apps = [k.upper() for k in src.active()] or ['']
    dr.set_apps(apps)
    dr.set_focus(0)
    print('active: %r -- ctrl-C to stop' % (apps,))
    shots = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 4.0:
        lv = src.levels([a.lower() for a in dr.apps])
        dr.update(lv[dr.idx])
        dr.draw_shelf(float(dr.idx))
        dr.draw_row()
        shots.append((shot(m, dr, 0, drawer.H),
                      '%.1fs %s %.2f' % (time.monotonic() - t0,
                                         dr.apps[dr.idx], lv[dr.idx])))
        time.sleep(0.3)
    return shots[-6:]


def main(argv):
    shots = live() if '--live' in argv else list(demo_states())
    for _, tag in shots:
        print('  ', tag)
    w, h = gfx.W * SCALE, gfx.H * SCALE
    canvas = Image.new('L', (w, (h + GAP) * len(shots) - GAP), 128)
    for i, (im, _) in enumerate(shots):
        canvas.paste(im, (0, i * (h + GAP)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'drawer_preview.png')
    canvas.save(out)
    print('wrote %s (%d frames)' % (out, len(shots)))


if __name__ == '__main__':
    main(sys.argv[1:])
