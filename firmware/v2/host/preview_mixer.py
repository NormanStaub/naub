"""Render the mixer screen on the PC with the same mixer.py the device runs.

    python preview_mixer.py            # a few states side by side
    python preview_mixer.py --live     # drive it from this PC's real audio

--live polls the actual per-app WASAPI peak meters (appmeter.py), so you can
see the real trace before any of it is on the device.
"""
import _paths
import math, os, random, sys, time
from PIL import Image
import ccfont, frc, gfx, mixer

SCALE = 4
GAP = 6


def demo_states():
    """A loud hit, then the decay -- so the peak markers can be seen settling
    away from the falling meter, which is the whole point of them."""
    m = mixer.Mixer(ccfont.TINY)
    m.set_name(0, 'CHROME')
    m.set_name(1, 'MUSIC')
    m.set_name(2, 'SPOTIFY')
    m.vol = [0.45, 0.70, 1.0]
    m.sysvol = 0.62

    random.seed(3)
    for _ in range(30):                      # let the per-band wander settle in
        m.update([0.85, 0.60, 0.0])
    m.render()
    yield m, 'playing: bands wander independently'

    for i in range(4):
        for _ in range(8):                   # signal drops; peaks hang behind
            m.update([0.12, 0.25, 0.0])
        m.render()
        yield m, 'decay %d: peaks lag the bands' % (i + 1)

    m.mute[1] = True
    m.set_name(1, 'SUPERNOTE_PARTNER')       # long -> marquee
    for _ in range(12):
        m.marquee()
    m.render()
    yield m, 'muted (hollow bar) + long name'

    for k in range(1, len(mixer.MODES)):     # the mode bar's position ticks
        m.mode = k
        m.sysmute = k == 2
        m.draw_foot()
        yield m, 'mode %d/%d: %s%s' % (k + 1, len(mixer.MODES), mixer.MODES[k],
                                       ' (system muted)' if m.sysmute else '')


def live():
    import appmeter
    src = appmeter.AppMeters()
    m = mixer.Mixer(ccfont.TINY)
    names = src.top(mixer.NCOL)
    for i, nm in enumerate(names):
        m.set_name(i, nm.upper())
    m.sysvol = src.system_volume()
    print('watching %r -- ctrl-C to stop' % (names,))
    shots = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 4.0:
        lv = src.levels(names)
        m.update(lv)
        for i, nm in enumerate(names):
            m.vol[i] = src.volume(nm)
        m.marquee()
        m.render()
        shots.append((frc.avg_image(m.planes, SCALE), '%.1fs %r' % (
            time.monotonic() - t0, [round(x, 2) for x in lv])))
        time.sleep(0.35)
    return shots[-6:]


def main(argv):
    if '--live' in argv:
        shots = live()
    else:
        shots = [(frc.avg_image(m.planes, SCALE), tag) for m, tag in demo_states()]
    for _, tag in shots:
        print('  ', tag)
    w, h = gfx.W * SCALE, gfx.H * SCALE
    canvas = Image.new('L', (w, (h + GAP) * len(shots) - GAP), 128)
    for i, (im, _) in enumerate(shots):
        canvas.paste(im, (0, i * (h + GAP)))
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       'mixer_preview.png')
    canvas.save(out)
    print('wrote %s (%d frames)' % (out, len(shots)))


if __name__ == '__main__':
    main(sys.argv[1:])
