"""Rasterize C&C Red Alert TTF -> a compact CircuitPython bitmap-font module.

Output ccfont.py exposes two fonts as {'H':height, 'G':{ch:(w, bytes)}} where the
glyph bytes are row-major, MSB-first, stride=(w+7)//8. Pure data; no deps on device.
"""
import os
from PIL import Image, ImageFont, ImageDraw

# extract from the repo-root c_c_red_alert_inet.zip into host/font_src/ first
TTF = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'font_src', 'C&C Red Alert [INET].ttf')

def build(size, chars):
    f = ImageFont.truetype(TTF, size)
    asc, desc = f.getmetrics()
    H = asc + desc
    glyphs = {}
    for ch in chars:
        adv = round(f.getlength(ch))
        bbox = f.getbbox(ch)                      # (l,t,r,b) ink box, may be None-ish
        right = bbox[2] if bbox else 0
        w = max(adv, right, 1)
        im = Image.new('1', (w, H), 0)
        ImageDraw.Draw(im).text((0, 0), ch, font=f, fill=1)
        px = im.load()
        stride = (w + 7) // 8
        data = bytearray(stride * H)
        for y in range(H):
            for x in range(w):
                if px[x, y]:
                    data[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
        glyphs[ch] = (w, bytes(data))
    return H, glyphs

def emit(fh, name, size, chars):
    H, glyphs = build(size, chars)
    fh.write('%s = {"H": %d, "G": {\n' % (name, H))
    for ch in chars:
        w, data = glyphs[ch]
        fh.write('  %r: (%d, %r),\n' % (ch, w, data))
    fh.write('}}\n\n')
    return H, sum(len(d) for _, d in glyphs.values())

# full printable ASCII (font is all-caps; lowercase maps to caps glyphs in the TTF)
SML_CHARS = ''.join(chr(c) for c in range(32, 127))
BIG_CHARS = '0123456789% .-'

OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', 'device', 'ccfont.py'))
with open(OUT, 'w', encoding='ascii') as fh:
    fh.write('# Auto-generated from C&C Red Alert [INET].ttf. Row-major, MSB-first.\n')
    h1, b1 = emit(fh, 'SML', 16, SML_CHARS)
    h2, b2 = emit(fh, 'BIG', 26, BIG_CHARS)

print('SML H=%d glyphs=%d bytes=%d' % (h1, len(SML_CHARS), b1))
print('BIG H=%d glyphs=%d bytes=%d' % (h2, len(BIG_CHARS), b2))
print('ccfont.py size = %d bytes' % os.path.getsize(OUT))
