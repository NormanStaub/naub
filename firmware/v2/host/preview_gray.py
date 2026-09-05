"""Verify graybars planes: render each mode's time-averaged appearance."""
import _paths
from PIL import Image
import gfx, graybars

SCALE = 4
GAP = 10

def avg_img(planes):
    n = len(planes)
    im = Image.new('L', (gfx.W, gfx.H))
    p = im.load()
    for y in range(gfx.H):
        i = (y >> 3) * gfx.W
        b = 1 << (y & 7)
        for x in range(gfx.W):
            on = sum(1 for fb in planes if fb[i + x] & b)
            p[x, y] = 255 - (255 * on // n)     # ink -> dark
    return im.resize((gfx.W * SCALE, gfx.H * SCALE), Image.NEAREST)

canvas = Image.new('L', (gfx.W * SCALE, (gfx.H * SCALE + GAP) * 4), 128)
for m in range(4):
    planes = graybars.build(m, "%s C=22" % graybars.MODES[m])
    canvas.paste(avg_img(planes), (0, m * (gfx.H * SCALE + GAP)))
    # duty check: sample centre of each band at y=40
    duties = []
    for band in range(4):
        x = band * 32 + 16
        i = (40 >> 3) * gfx.W + x
        b = 1 << (40 & 7)
        duties.append(sum(1 for fb in planes if fb[i + 0] & b) / len(planes))
    print(graybars.MODES[m], 'planes=%d' % len(planes), 'duties@y40', duties)
canvas.save('gray_preview.png')
print('wrote gray_preview.png')
