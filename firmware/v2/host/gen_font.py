"""Rasterize C&C Red Alert TTF -> a compact CircuitPython bitmap-font module.

Output ccfont.py exposes two fonts as {'H':height, 'G':{ch:(w, bytes)}} where the
glyph bytes are row-major, MSB-first, stride=(w+7)//8. Pure data; no deps on device.
"""
import os
from PIL import Image, ImageFont, ImageDraw

# extract from the repo-root c_c_red_alert_inet.zip into host/font_src/ first
TTF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'font_src', 'C&C Red Alert [INET].ttf')

def build(size, chars, height=None, ytop=0):
    """Rasterize `chars` at `size`.  height/ytop crop the raster to a fixed box:
    TINY uses them to fit a legible 7px cap height into a single 8px page (the
    TTF's own metrics box at that size is 12 rows, most of it leading)."""
    f = ImageFont.truetype(TTF, size)
    asc, desc = f.getmetrics()
    src_h = asc + desc
    H = height or src_h
    glyphs = {}
    for ch in chars:
        adv = round(f.getlength(ch))
        bbox = f.getbbox(ch)                      # (l,t,r,b) ink box, may be None-ish
        right = bbox[2] if bbox else 0
        w = max(adv, right, 1)
        im = Image.new('1', (w, src_h), 0)
        ImageDraw.Draw(im).text((0, 0), ch, font=f, fill=1)
        px = im.load()
        stride = (w + 7) // 8
        data = bytearray(stride * H)
        for y in range(H):
            sy = y + ytop
            if sy >= src_h:
                break
            for x in range(w):
                if px[x, sy]:
                    data[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
        glyphs[ch] = (w, bytes(data))
    return H, glyphs

def emit(fh, name, size, chars, height=None, ytop=0):
    H, glyphs = build(size, chars, height, ytop)
    fh.write('%s = {"H": %d, "G": {\n' % (name, H))
    for ch in chars:
        w, data = glyphs[ch]
        fh.write('  %r: (%d, %r),\n' % (ch, w, data))
    fh.write('}}\n\n')
    return H, sum(len(d) for _, d in glyphs.values())

# full printable ASCII (font is all-caps; lowercase maps to caps glyphs in the TTF)
SML_CHARS = ''.join(chr(c) for c in range(32, 127))
BIG_CHARS = '0123456789% .-'
# TINY is exactly 8 px tall, i.e. one display page.  That is what lets
# gfx.text_strip() render a label into page-aligned column bytes, so scrolling
# it (the mixer's marquee) is a byte-slice copy rather than a redraw.
# Rasterized at 11 and cropped from row 3: caps then occupy rows 0..5 (6px,
# legible) with rows 6-7 as the gap.  Rendering at a size whose *metrics* are
# 8 rows gives only 4px caps, too small to read on the panel; going bigger
# makes most app names wider than the mixer's 40px column and forces every one
# of them to marquee.  Draw TINY with spacing=0 (see mixer.SPACING): the glyph
# advances already carry their own side bearing.
TINY_CHARS = SML_CHARS
TINY_SIZE, TINY_H, TINY_YTOP = 11, 8, 3

OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', 'device', 'ccfont.py'))
with open(OUT, 'w', encoding='ascii') as fh:
    fh.write('# Auto-generated from C&C Red Alert [INET].ttf. Row-major, MSB-first.\n')
    h1, b1 = emit(fh, 'SML', 16, SML_CHARS)
    h2, b2 = emit(fh, 'BIG', 26, BIG_CHARS)
    h3, b3 = emit(fh, 'TINY', TINY_SIZE, TINY_CHARS, TINY_H, TINY_YTOP)

print('SML  H=%d glyphs=%d bytes=%d' % (h1, len(SML_CHARS), b1))
print('BIG  H=%d glyphs=%d bytes=%d' % (h2, len(BIG_CHARS), b2))
print('TINY H=%d glyphs=%d bytes=%d' % (h3, len(TINY_CHARS), b3))
assert h3 == 8, 'TINY must be exactly one page tall, got %d' % h3
print('ccfont.py size = %d bytes' % os.path.getsize(OUT))
