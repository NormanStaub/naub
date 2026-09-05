"""naub_logo.png -> forced-binary 1-bpp icon (V1 pack format), preview + module."""
import _paths
import os
from PIL import Image
import gfx

SRC = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', '..', '..', 'pcb', 'graphics',
                                    'naub_logo.png'))

def make_icon(size, thresh=128):
    # V1 luma path, forced binary (hard threshold, no Floyd-Steinberg). This asset
    # is a bright mark on opaque black, so the mark (luma>thresh) becomes black ink.
    im = Image.open(SRC).convert('L').resize((size, size), Image.LANCZOS)
    p = im.load()
    stride = (size + 7) // 8
    data = bytearray(stride * size)
    for y in range(size):
        for x in range(size):
            if p[x, y] > thresh:
                data[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
    return (size, size, bytes(data))

def fb_to_img(fb, scale=5):
    im = Image.new('RGB', (gfx.W, gfx.H), (14, 18, 10))
    px = im.load()
    for y in range(gfx.H):
        for x in range(gfx.W):
            if (fb[(y >> 3) * gfx.W + x] >> (y & 7)) & 1:
                px[x, y] = (196, 255, 120)
    return im.resize((gfx.W * scale, gfx.H * scale), Image.NEAREST)

if __name__ == '__main__':
    sizes = [48, 56, 64]
    scale = 5
    gap = 12
    canvas = Image.new('RGB', (gfx.W * scale, (gfx.H * scale + gap) * len(sizes)), (40, 40, 40))
    y = 0
    for sz in sizes:
        ic = make_icon(sz)
        fb = gfx.new()
        gfx.blit_icon(fb, ic, (gfx.W - sz) // 2, (gfx.H - sz) // 2)
        canvas.paste(fb_to_img(fb, scale), (0, y))
        y += gfx.H * scale + gap
        print('size %d -> %d bytes packed' % (sz, len(ic[2])))
    canvas.save('icon_preview.png')
    print('wrote icon_preview.png (sizes %r)' % sizes)
