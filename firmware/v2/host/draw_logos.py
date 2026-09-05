"""Build high-res Chrome + Spotify marks, threshold to binary at a target size."""
import _paths
from PIL import Image, ImageDraw
import gfx

SS = 8  # supersample

def spotify_src(px):
    S = px * SS
    im = Image.new('L', (S, S), 255)     # white bg
    d = ImageDraw.Draw(im)
    c = S / 2
    R = S * 0.47
    d.ellipse([c - R, c - R, c + R, c + R], fill=0)    # black disc
    # three nested "sound wave" arcs: tops of circles centred well below the disc,
    # so each is a shallow hill (peak centred). White, thickest/widest on top.
    arcs = [(1.55, 39, 0.15), (1.30, 34, 0.14), (1.05, 30, 0.13)]
    cyc = c + R * 1.15                    # common arc-circle centre, below disc
    for rr, half, w in arcs:
        ar = R * rr
        bb = [c - ar, cyc - ar, c + ar, cyc + ar]
        d.arc(bb, 270 - half, 270 + half, fill=255, width=int(R * w))
    return im

def chrome_src(px):
    S = px * SS
    im = Image.new('L', (S, S), 255)
    d = ImageDraw.Draw(im)
    c = S / 2
    R = S * 0.47
    d.ellipse([c - R, c - R, c + R, c + R], fill=0)     # black outer disc
    import math
    sw = int(R * 0.14)                                   # spoke half-width via line width
    for ang in (270, 30, 150):                           # up, down-right, down-left
        a = math.radians(ang)
        x2 = c + R * math.cos(a); y2 = c + R * math.sin(a)
        d.line([c, c, x2, y2], fill=255, width=sw)       # white segment boundary
    rw = int(R * 0.10)
    rr = R * 0.44
    d.ellipse([c - rr, c - rr, c + rr, c + rr], outline=255, width=rw)  # white ring
    ic = R * 0.34
    d.ellipse([c - ic, c - ic, c + ic, c + ic], fill=0)  # black hub (the blue centre)
    return im

def pack(src, size, thresh=128):
    im = src.resize((size, size), Image.LANCZOS)
    p = im.load()
    stride = (size + 7) // 8
    data = bytearray(stride * size)
    for y in range(size):
        for x in range(size):
            if p[x, y] < thresh:                 # black-on-white art -> ink where dark
                data[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
    return (size, size, bytes(data))

def fb_to_img(fb, scale=6):
    im = Image.new('RGB', (gfx.W, gfx.H), (14, 18, 10))
    px = im.load()
    for y in range(gfx.H):
        for x in range(gfx.W):
            if (fb[(y >> 3) * gfx.W + x] >> (y & 7)) & 1:
                px[x, y] = (196, 255, 120)
    return im.resize((gfx.W * scale, gfx.H * scale), Image.NEAREST)

if __name__ == '__main__':
    SIZE = 45
    spot = pack(spotify_src(SIZE), SIZE)
    chrome = pack(chrome_src(SIZE), SIZE)
    gap = 8
    total = SIZE * 2 + gap
    x0 = (gfx.W - total) // 2
    y0 = (gfx.H - SIZE) // 2
    fb = gfx.new()
    gfx.blit_icon(fb, chrome, x0, y0)
    gfx.blit_icon(fb, spot, x0 + SIZE + gap, y0)
    fb_to_img(fb).save('logos_preview.png')
    # also save each source at 1:1 zoomed for detail
    spotify_src(SIZE).resize((SIZE*SS, SIZE*SS)).save('spot_src.png')
    print('SIZE', SIZE, 'chrome bytes', len(chrome[2]), 'spot bytes', len(spot[2]))
    print('wrote logos_preview.png')
