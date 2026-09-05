"""What else could ccfont.TINY be?  Render candidate faces at the panel's scale.

    python preview_fonts.py [--size 11] [--sample "APP CTRL"]

The constraint is unforgiving: the whole glyph box must fit ONE 8px page, which
is what lets gfx.text_strip() emit page-aligned column bytes and makes the
mixer's marquee a byte-slice copy.  Inside that, caps get about 6 rows.  Almost
every "nice" typeface turns to mush when a 6px cap height is thresholded to
1-bit -- what survives is faces drawn for small sizes (strong hinting, few
diagonals, no thin strokes) and pixel/bitmap faces.

Each candidate is rasterized the way gen_font.py does it (size, then cropped to
8 rows from a ytop), measured on the strings the UI actually draws, and stamped
with whether it fits the two places that matter:

    * a mixer column name -- 40 px
    * a mode bar label    -- the widest is CUSTOM 2

Writes host/fonts_preview.png, one row per candidate at 4x.
"""
import _paths
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import gfx

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
SRC = os.path.join(HERE, 'font_src')

# (label, path, size, ytop) -- size/ytop are the gen_font.build() crop, tuned
# per face so the caps land in rows 0..5 of the 8-row box.
CANDIDATES = [
    ('C&C Red Alert (current)', os.path.join(SRC, 'C&C Red Alert [INET].ttf'), 11, 3),
    ('C&C Red Alert [LAN]', os.path.join(SRC, 'C&C Red Alert [LAN].ttf'), 11, 3),
    ('Small Fonts', os.path.join(WIN, 'smallf.ttf'), 8, 1),
    ('Tahoma', os.path.join(WIN, 'tahoma.ttf'), 9, 1),
    ('Tahoma Bold', os.path.join(WIN, 'tahomabd.ttf'), 9, 1),
    ('Verdana', os.path.join(WIN, 'verdana.ttf'), 9, 1),
    ('Segoe UI', os.path.join(WIN, 'segoeui.ttf'), 9, 2),
    ('Arial', os.path.join(WIN, 'arial.ttf'), 9, 1),
    ('Arial Narrow', os.path.join(WIN, 'arialn.ttf'), 10, 1),
    ('Consolas', os.path.join(WIN, 'consola.ttf'), 9, 1),
    ('Lucida Console', os.path.join(WIN, 'lucon.ttf'), 9, 1),
    ('Courier New', os.path.join(WIN, 'cour.ttf'), 10, 2),
    ('Impact', os.path.join(WIN, 'impact.ttf'), 9, 1),
    ('Franklin Gothic Med', os.path.join(WIN, 'framd.ttf'), 9, 1),
]

SAMPLES = ('APP CTRL', 'SPOTIFY', 'CHROME')
MEASURE = ('SPOTIFY', 'CUSTOM 2')   # the two fits that matter
H = 8                       # one page: the hard constraint
SCALE = 4
COL_W = 40                  # a mixer column's usable width


def render(path, size, ytop, text):
    """-> (width, list of 8 rows of bools), cropped exactly like gen_font."""
    f = ImageFont.truetype(path, size)
    asc, desc = f.getmetrics()
    src_h = asc + desc
    w = max(int(round(f.getlength(text))), 1)
    im = Image.new('1', (w + 4, src_h + 4), 0)
    ImageDraw.Draw(im).text((0, 0), text, font=f, fill=1)
    px = im.load()
    rows = []
    for y in range(H):
        sy = y + ytop
        rows.append([bool(px[x, sy]) if sy < im.height else False
                     for x in range(w)])
    return w, rows


def ink_rows(rows):
    """Which of the 8 rows carry ink -- i.e. does the face actually fit?"""
    used = [i for i, r in enumerate(rows) if any(r)]
    return (used[0], used[-1]) if used else (0, 0)


def main(argv):
    out_rows = []
    for label, path, size, ytop in CANDIDATES:
        if not os.path.exists(path):
            print('  -- %-22s MISSING (%s)' % (label, os.path.basename(path)))
            continue
        try:
            widths = {}
            strips = []
            top = 8
            bot = 0
            for s in SAMPLES:
                w, rows = render(path, size, ytop, s)
                widths[s] = w
                a, b = ink_rows(rows)
                top = min(top, a)
                bot = max(bot, b)
                strips.append((s, w, rows))
            for s in MEASURE:               # measured, not drawn
                widths[s] = render(path, size, ytop, s)[0]
        except Exception as e:
            print('  -- %-22s FAILED (%s)' % (label, e))
            continue
        fits_col = widths['SPOTIFY'] <= COL_W
        fits_bar = widths['CUSTOM 2'] <= 60
        print('  %-22s rows %d..%d  SPOTIFY %2dpx %s  CUSTOM 2 %2dpx %s'
              % (label, top, bot, widths['SPOTIFY'],
                 'fits' if fits_col else 'WIDE', widths['CUSTOM 2'],
                 'fits' if fits_bar else 'WIDE'))
        out_rows.append((label, strips))

    if not out_rows:
        sys.exit('no candidates rendered')

    pad = 2
    line_h = (H + pad) * SCALE
    label_w = 150
    width = label_w + gfx.W * SCALE
    canvas = Image.new('L', (width, line_h * len(out_rows) + 8), 255)
    d = ImageDraw.Draw(canvas)
    for i, (label, strips) in enumerate(out_rows):
        y0 = i * line_h + 4
        d.text((4, y0 + 4), label, fill=0)
        px = 0                              # cursor in DISPLAY pixels, not canvas
        for s, w, rows in strips:
            for ry, row in enumerate(rows):
                for rx, on in enumerate(row):
                    if not on or px + rx >= gfx.W:
                        continue
                    cx = label_w + (px + rx) * SCALE
                    cy = y0 + ry * SCALE
                    d.rectangle([(cx, cy), (cx + SCALE - 1, cy + SCALE - 1)],
                                fill=0)
            px += w + 6
    path = os.path.join(HERE, 'fonts_preview.png')
    canvas.save(path)
    print('wrote %s (%d candidates)' % (path, len(out_rows)))


if __name__ == '__main__':
    main(sys.argv[1:])
