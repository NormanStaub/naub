"""FRC (frame-rate control) grayscale encoder — PC side, the reusable core.

Turns per-pixel ink levels (0.0 = white .. 1.0 = black) into N bitplanes for
the device to cycle at max frame rate (st7565.show_pages).  The mapping the
user picked on hardware ("GRAD 4F"): N=4 planes, integer duty + 4x4 Bayer
ordered dither on the fractional residual, phase-rotated by (x+y)%N so
neighbouring pixels fire in different frames.

    planes = frc.encode(ink)            # ink: HxW nested lists / rows of floats
    planes = frc.encode_image(pil_img)  # grayscale/RGBA PIL image -> full screen

Measured: 4 planes cycle at ~340 fps => ~85 Hz per gray cycle; 6 planes
(7 levels) => ~56 Hz and shimmers slightly more.  N=1 degenerates to plain
static Bayer dithering.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                '..', 'device'))
import gfx

BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))


def encode(ink, n=4, ox=0, oy=0, planes=None):
    """ink: rows of floats (0..1).  Draws into `planes` (created if None) at
    offset (ox, oy) using screen-global Bayer/phase coordinates, so multiple
    encode() calls tile seamlessly."""
    if planes is None:
        planes = [gfx.new() for _ in range(n)]
    for yy, row in enumerate(ink):
        y = oy + yy
        if not 0 <= y < gfx.H:
            continue
        i = (y >> 3) * gfx.W
        b = 1 << (y & 7)
        for xx, g in enumerate(row):
            x = ox + xx
            if not 0 <= x < gfx.W:
                continue
            d = g * n
            base = int(d)
            if (BAYER[y % 4][x % 4] + 0.5) / 16.0 < d - base:
                base += 1
            p = (x + y) % n
            for k in range(n):
                if (k - p) % n < base:
                    planes[k][i + x] |= b
    return planes


def image_ink(im, size=None):
    """PIL image -> ink rows.  RGBA is composited over white first."""
    from PIL import Image
    if im.mode == 'RGBA':
        bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im)
    im = im.convert('L')
    if size:
        im = im.resize(size, Image.LANCZOS)
    px = im.load()
    return [[1.0 - px[x, y] / 255.0 for x in range(im.width)]
            for y in range(im.height)]


def encode_image(im, n=4, size=None, ox=0, oy=0, planes=None):
    return encode(image_ink(im, size), n=n, ox=ox, oy=oy, planes=planes)


def avg_image(planes, scale=5):
    """Time-averaged PIL preview: what the panel's LC response will show."""
    from PIL import Image
    im = Image.new('L', (gfx.W, gfx.H))
    px = im.load()
    for y in range(gfx.H):
        i = (y >> 3) * gfx.W
        b = 1 << (y & 7)
        for x in range(gfx.W):
            on = sum(1 for fb in planes if fb[i + x] & b)
            px[x, y] = 255 - (255 * on // len(planes))
    return im.resize((gfx.W * scale, gfx.H * scale), Image.NEAREST)


def emit_module(path, planes, name='PLANES', doc=''):
    """Write planes as a device-importable module of bytes literals."""
    out = ['"""%s"""' % (doc or 'Precomputed FRC planes.'), '%s = (' % name]
    for fb in planes:
        out.append('    %r,' % bytes(fb))
    out.append(')')
    with open(path, 'w') as f:
        f.write('\n'.join(out) + '\n')
