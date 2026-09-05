"""Rasterize a TTF -> a compact CircuitPython bitmap-font module (ccfont.py).

    python gen_font.py                 # the default face
    python gen_font.py --face redalert # ... or another from FACES
    python gen_font.py --list          # what is on offer

Output exposes each size as {'H': height, 'G': {ch: (w, bytes)}} where the glyph
bytes are row-major, MSB-first, stride=(w+7)//8. Pure data; no device deps.

**TINY is the whole UI font** and is exactly 8 px tall, i.e. one display page.
That is what lets gfx.text_strip() render a label into page-aligned column
bytes, so the mixer's marquee is a byte-slice copy rather than a redraw, and
what lets the names bar and the mode bar each be a single page.

Fitting a face into that box is the entire difficulty, and the numbers are not
guessable -- they were found by rasterizing candidates and measuring which rows
actually carry ink (see host/preview_fonts.py). The rules that matter:

  * ink must clear row 7, which the names bar keeps for its rule.  Where the
    ink starts also matters: the mode bar wants a blank row above the caps so
    they do not touch the rule over them, and a face whose own crop already
    leaves one (Tahoma at ytop 1 starts on row 1) gets that for free --
    mixer.FOOT_SHIFT is then 0 rather than nudging it down by hand.
  * cropping is `size` then `ytop`, so a face is tuned by BOTH -- a bigger size
    with a bigger ytop keeps the cap height while cutting the leading above it.
  * at this scale most faces disintegrate.  What survives is faces drawn or
    hinted for small sizes; see the survey in preview_fonts.py.

Tahoma at size 8 cropped from row 1 gives 6 px caps in rows 1..6 and renders
SPOTIFY in 31 px -- the same width as the all-caps C&C face it replaced, so no
column fit or marquee threshold changed.

TINY is generated ALL-CAPS: lowercase is rasterized from its uppercase form.
The C&C face was all-caps by construction, and a proper face is not -- eight
rows cannot hold ascenders, caps AND descenders at a legible cap height, and
the UI is uppercase throughout anyway (the host upper()s every app name).  The
one casualty is Q, whose tail falls past the box; ALLOW_CLIP records that so
the check stays meaningful for everything else.
"""
import os
import sys

from PIL import Image, ImageFont, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
SRC = os.path.join(HERE, 'font_src')

# name -> (ttf path, tiny_size, tiny_ytop, sml_size, big_size)
# tiny_size/tiny_ytop are the one-page crop; see the note above.
FACES = {
    'tahoma': (os.path.join(WIN, 'tahoma.ttf'), 8, 1, 16, 26),
    'tahomabd': (os.path.join(WIN, 'tahomabd.ttf'), 8, 1, 16, 26),
    'consolas': (os.path.join(WIN, 'consola.ttf'), 9, 1, 16, 26),
    'lucida': (os.path.join(WIN, 'lucon.ttf'), 9, 1, 16, 26),
    'franklin': (os.path.join(WIN, 'framd.ttf'), 9, 2, 16, 26),
    # extract the repo-root c_c_red_alert_inet.zip into host/font_src/ first
    'redalert': (os.path.join(SRC, 'C&C Red Alert [INET].ttf'), 11, 3, 16, 26),
}
DEFAULT = 'tahoma'

TINY_H = 8                      # one page.  Non-negotiable: see the note above.
ALLOW_CLIP = set('Qq')          # Q's tail does not fit and never will; the UI
                                # would rather have 6px caps than a legible Q.
SML_CHARS = ''.join(chr(c) for c in range(32, 127))
BIG_CHARS = '0123456789% .-'
TINY_CHARS = SML_CHARS


def build(ttf, size, chars, height=None, ytop=0, caps=False):
    """Rasterize `chars` at `size`.  height/ytop crop the raster to a fixed box:
    TINY uses them to fit a legible cap height into a single 8px page (a face's
    own metrics box at that size is mostly leading)."""
    f = ImageFont.truetype(ttf, size)
    asc, desc = f.getmetrics()
    src_h = asc + desc
    H = height or src_h
    glyphs = {}
    for ch in chars:
        draw = ch.upper() if caps else ch
        adv = round(f.getlength(draw))
        # Render onto a canvas comfortably wider than the advance, then measure
        # where the ink actually ends.  A proportional face routinely overhangs
        # its own advance by a pixel, and sizing the canvas to the advance
        # silently shears the glyph by clipping that column -- which is exactly
        # what it looks like on the panel: an A with no right-hand stroke.
        # Draw at an offset on a roomy canvas, then find where the ink really
        # is.  A proportional face routinely overhangs its advance on the right
        # AND starts left of the pen origin (Tahoma's 'A' has a negative left
        # side bearing).  Sizing the canvas to the advance and drawing at x=0
        # clips both, which on the panel looks like a sheared glyph -- an A
        # missing one diagonal.  So keep whatever the face actually drew.
        ox = 4
        im = Image.new('1', (adv + 2 * ox + 8, src_h + ytop + 2), 0)
        ImageDraw.Draw(im).text((ox, 0), draw, font=f, fill=1)
        px = im.load()
        cols = [x for x in range(im.width)
                if any(px[x, y] for y in range(im.height))]
        if cols:
            start = min(ox, cols[0])
            w = max(adv + (ox - start), cols[-1] - start + 1, 1)
        else:
            start, w = ox, max(adv, 1)          # space and friends
        stride = (w + 7) // 8
        data = bytearray(stride * H)
        for y in range(H):
            sy = y + ytop
            if sy >= im.height:
                break
            for x in range(w):
                sx = start + x
                if sx < im.width and px[sx, sy]:
                    data[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
        glyphs[ch] = (w, bytes(data))
    return H, glyphs


def clipped(ttf, size, chars, height, ytop, caps=False):
    """Glyphs whose ink the crop throws away -- they lose their tops or tails
    and the face silently looks broken on the panel.  -> sorted list."""
    f = ImageFont.truetype(ttf, size)
    asc, desc = f.getmetrics()
    lost = []
    for ch in chars:
        draw = ch.upper() if caps else ch
        w = max(round(f.getlength(draw)), 1) + 8
        im = Image.new('1', (w, asc + desc + ytop + 4), 0)
        ImageDraw.Draw(im).text((0, 0), draw, font=f, fill=1)
        px = im.load()
        for y in list(range(ytop)) + list(range(ytop + height, im.height)):
            if any(px[x, y] for x in range(w)):
                lost.append(ch)
                break
    return sorted(set(lost))


def emit(fh, name, ttf, size, chars, height=None, ytop=0, caps=False):
    H, glyphs = build(ttf, size, chars, height, ytop, caps)
    fh.write('%s = {"H": %d, "G": {\n' % (name, H))
    for ch in chars:
        w, data = glyphs[ch]
        fh.write('  %r: (%d, %r),\n' % (ch, w, data))
    fh.write('}}\n\n')
    return H, sum(len(d) for _, d in glyphs.values())


def main(argv):
    face = DEFAULT
    it = iter(argv)
    for a in it:
        if a == '--face':
            face = next(it)
        elif a == '--list':
            for k, v in sorted(FACES.items()):
                mark = ' (default)' if k == DEFAULT else ''
                ok = 'ok' if os.path.exists(v[0]) else 'MISSING'
                print('  %-10s size %2d ytop %d  %-7s %s%s'
                      % (k, v[1], v[2], ok, os.path.basename(v[0]), mark))
            return
        else:
            sys.exit(__doc__)
    if face not in FACES:
        sys.exit('unknown face %r; try --list' % face)
    ttf, tiny_size, tiny_ytop, sml_size, big_size = FACES[face]
    if not os.path.exists(ttf):
        sys.exit('font file not found: %s' % ttf)

    lost = [c for c in clipped(ttf, tiny_size, TINY_CHARS, TINY_H, tiny_ytop,
                               caps=True) if c not in ALLOW_CLIP]
    if lost:
        sys.exit('%s at size %d ytop %d clips ink on %d glyph(s): %s\n'
                 'Retune the crop -- host/preview_fonts.py shows the row spans.'
                 % (face, tiny_size, tiny_ytop, len(lost), ' '.join(lost)))

    out = os.path.normpath(os.path.join(HERE, '..', 'device', 'ccfont.py'))
    with open(out, 'w', encoding='ascii') as fh:
        fh.write('# Auto-generated by host/gen_font.py from %s.\n'
                 '# Row-major, MSB-first. TINY is exactly one 8px page.\n'
                 % os.path.basename(ttf))
        h1, b1 = emit(fh, 'SML', ttf, sml_size, SML_CHARS)
        h2, b2 = emit(fh, 'BIG', ttf, big_size, BIG_CHARS)
        h3, b3 = emit(fh, 'TINY', ttf, tiny_size, TINY_CHARS, TINY_H,
                      tiny_ytop, caps=True)

    print('face %s (%s)' % (face, os.path.basename(ttf)))
    print('SML  H=%d glyphs=%d bytes=%d' % (h1, len(SML_CHARS), b1))
    print('BIG  H=%d glyphs=%d bytes=%d' % (h2, len(BIG_CHARS), b2))
    print('TINY H=%d glyphs=%d bytes=%d  (size %d, cropped from row %d)'
          % (h3, len(TINY_CHARS), b3, tiny_size, tiny_ytop))
    assert h3 == TINY_H, 'TINY must be exactly one page tall, got %d' % h3
    print('ccfont.py size = %d bytes' % os.path.getsize(out))


if __name__ == '__main__':
    main(sys.argv[1:])
