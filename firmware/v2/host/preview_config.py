"""Render the CONFIG mode on the PC with the same modules the device runs.

    python preview_config.py

config.py imports nothing but gfx, so this is exactly what the panel shows --
including the mode bar, which comes from mixer.Mixer so the two cannot drift.
"""
import _paths
import os
import sys

from PIL import Image

import ccfont
import config
import frc
import gfx
import mixer

SCALE = 4
GAP = 6
CONFIG_MODE = 3            # ... and demo_mixer.CONFIG_MODE


def build():
    cfg = config.Config(ccfont.TINY)
    mx = mixer.Mixer(ccfont.TINY)
    mx.sysvol = 0.62
    while mx.set_mode(CONFIG_MODE):             # settle the ticks on CONFIG
        pass
    return cfg, mx


def compose(cfg, mx):
    """Config screen on pages 0..6, the mixer's mode bar on page 7."""
    fb = bytearray(cfg.planes[0])
    o = mixer.PAGE_FOOT * gfx.W
    fb[o:o + gfx.W] = mx.planes[0][o:o + gfx.W]
    return fb


def shots():
    cfg, mx = build()
    out = []

    def snap(tag):
        cfg.render()
        out.append((frc.avg_image([compose(cfg, mx)], SCALE), tag))

    snap('DISP tab, cursor on the view angle (50 deg)')

    for _ in range(4):
        cfg.adjust(-1)
    snap('SELECT turned down: 10 deg, the panel seen edge-on')

    for _ in range(8):
        cfg.adjust(+1)
    snap('SELECT turned up: 90 deg, square-on')

    cfg.scroll(+1)
    snap('SCROLL down: BRIGHTNESS, and the scrollbar moves')

    cfg.adjust(-6)
    snap('SELECT on BRIGHTNESS: 70% -- live on the backlight')

    cfg.scroll(+2)
    snap('SCROLL to the end: the window follows the cursor')

    cfg.nav(-1)
    snap('MENU NAV back one tab: SYS')

    cfg.nav(-1)
    snap('MENU NAV again: OS, with a read-only row')

    cfg.nav(+1)
    cfg.nav(+1)
    while cfg.focused_key() != 'advanced':
        cfg.scroll(+1)
    snap('DISP again: the ADVANCED DISPLAY row, with its > mark')

    cfg.click()
    snap('SELECT click enters: the ST7565 registers, and the way back')

    cfg.adjust(-9)
    snap('CONTRAST down to 22 -- this one is live on the glass')

    cfg.scroll(+4)
    snap('scrolled into the toggles')

    cfg.click()
    snap('INVERT ON')

    cfg.click()
    cfg.nav(-1)
    snap('MENU NAV backs out to DISP, cursor where it was')
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
                       'config_preview.png')
    canvas.save(out)
    print('wrote %s (%d frames)' % (out, len(imgs)))


if __name__ == '__main__':
    main(sys.argv[1:])
