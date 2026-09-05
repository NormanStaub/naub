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

Two kinds of face can be used, and the difference matters more than the
choice of face:

  * **raster** ('gdi'): a Windows .FON bitmap font, DRAWN by hand at a fixed
    pixel size.  Rendered through GDI (see gdifont.py) because FreeType, and
    therefore PIL, cannot open a .FON at all -- which is why these never turned
    up in the first survey.  At 8px this is not a close call: an outline
    rasterised to a 5-6px cap height loses strokes and joins others no matter
    how it is hinted, while a raster face has every pixel where its designer
    put it.
  * **outline** ('ttf'): rasterised by PIL.  Fine at SML/BIG sizes, and the
    only option for a face that ships no bitmap strikes.

The default is **Small Fonts at height 8** -- an 8-row cell, i.e. exactly one
page, with 5 px caps and SPOTIFY in 29 px (against 31-32 px for the outline
faces tried).  Slightly shorter caps than an outline face at the same box, and
considerably more legible, because nothing is being approximated.

A raster face must be asked for a height it actually ships or GDI will scale a
strike it does not have, which reintroduces exactly the problem it was chosen
to avoid.  gdifont.strikes() reports the real ones; Small Fonts has 5, 6, 8,
10..13 and 16.

TINY is generated ALL-CAPS: lowercase is rasterized from its uppercase form.
Eight rows cannot hold ascenders, caps AND descenders at a legible cap height,
and the UI is uppercase throughout anyway (the host upper()s every app name).
"""
import os
import sys

from PIL import Image, ImageFont, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
WIN = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
SRC = os.path.join(HERE, 'font_src')

# name -> (ttf path, tiny_size, tiny_ytop, sml_size, big_size)
# tiny_size/tiny_ytop are the one-page crop; see the note above.
# name -> (kind, source, tiny_size, tiny_ytop, sml_size, big_size)
#   kind 'gdi' -> source is an installed RASTER face name, sizes are cell
#                 heights and must be strikes the face really ships
#   kind 'ttf' -> source is a file path, sizes are point sizes for PIL
FACES = {
    'small': ('gdi', 'Small Fonts', 8, 1, 16, 16),
    'terminal': ('gdi', 'Terminal', 8, 1, 16, 16),
    'sserif': ('gdi', 'MS Sans Serif', 13, 2, 16, 16),
    'tahoma': ('ttf', os.path.join(WIN, 'tahoma.ttf'), 8, 1, 16, 26),
    'tahomabd': ('ttf', os.path.join(WIN, 'tahomabd.ttf'), 8, 1, 16, 26),
    'consolas': ('ttf', os.path.join(WIN, 'consola.ttf'), 9, 1, 16, 26),
    'lucida': ('ttf', os.path.join(WIN, 'lucon.ttf'), 9, 1, 16, 26),
    'franklin': ('ttf', os.path.join(WIN, 'framd.ttf'), 9, 2, 16, 26),
    # extract the repo-root c_c_red_alert_inet.zip into host/font_src/ first
    'redalert': ('ttf', os.path.join(SRC, 'C&C Red Alert [INET].ttf'),
                 11, 3, 16, 26),
}
DEFAULT = 'small'

TINY_H = 8                      # one page.  Non-negotiable: see the note above.
ALLOW_CLIP = set('Qq')          # Q's tail does not fit and never will; the UI
                                # would rather have 6px caps than a legible Q.
SML_CHARS = ''.join(chr(c) for c in range(32, 127))
BIG_CHARS = '0123456789% .-'
TINY_CHARS = SML_CHARS


def build_gdi(face, size, chars, height=None, ytop=0, caps=False):
    """Rasterize `chars` from an installed RASTER face through GDI.

    Simpler than the outline path: a bitmap glyph never overhangs its advance,
    so the width GDI reports is the width to store."""
    import gdifont
    H = height or size
    glyphs = {}
    for ch in chars:
        draw = ch.upper() if caps else ch
        w, rows = gdifont.render(face, size, draw)
        w = max(w, 1)
        stride = (w + 7) // 8
        data = bytearray(stride * H)
        for y in range(H):
            sy = y + ytop
            if sy >= len(rows):
                break
            row = rows[sy]
            for x in range(min(w, len(row))):
                if row[x]:
                    data[y * stride + (x >> 3)] |= 0x80 >> (x & 7)
        glyphs[ch] = (w, bytes(data))
    return H, glyphs


def clipped_gdi(face, size, chars, height, ytop, caps=False):
    import gdifont
    lost = []
    for ch in chars:
        draw = ch.upper() if caps else ch
        _, rows = gdifont.render(face, size, draw)
        for y, row in enumerate(rows):
            if any(row) and not (ytop <= y < ytop + height):
                lost.append(ch)
                break
    return sorted(set(lost))


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


def emit(fh, name, kind, src, size, chars, height=None, ytop=0, caps=False):
    if kind == 'gdi':
        H, glyphs = build_gdi(src, size, chars, height, ytop, caps)
    else:
        H, glyphs = build(src, size, chars, height, ytop, caps)
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
                kind, src = v[0], v[1]
                if kind == 'gdi':
                    ok, shown = 'raster', src
                else:
                    ok = 'ok' if os.path.exists(src) else 'MISSING'
                    shown = os.path.basename(src)
                print('  %-10s %-6s size %2d ytop %d  %-7s %s%s'
                      % (k, kind, v[2], v[3], ok, shown, mark))
            return
        else:
            sys.exit(__doc__)
    if face not in FACES:
        sys.exit('unknown face %r; try --list' % face)
    kind, src, tiny_size, tiny_ytop, sml_size, big_size = FACES[face]
    if kind == 'ttf' and not os.path.exists(src):
        sys.exit('font file not found: %s' % src)
    if kind == 'gdi':
        import gdifont
        have = [h for h, _, _, _ in gdifont.strikes(src, 4, 20)]
        for want in (tiny_size, sml_size, big_size):
            if want not in have:
                sys.exit('%s ships no %dpx strike (has %s) -- GDI would scale '
                         'it, which is the whole thing we are avoiding'
                         % (src, want, have))

    check = clipped_gdi if kind == 'gdi' else clipped
    lost = [c for c in check(src, tiny_size, TINY_CHARS, TINY_H, tiny_ytop,
                             caps=True) if c not in ALLOW_CLIP]
    if lost:
        sys.exit('%s at size %d ytop %d clips ink on %d glyph(s): %s\n'
                 'Retune the crop -- host/preview_fonts.py shows the row spans.'
                 % (face, tiny_size, tiny_ytop, len(lost), ' '.join(lost)))

    out = os.path.normpath(os.path.join(HERE, '..', 'device', 'ccfont.py'))
    with open(out, 'w', encoding='ascii') as fh:
        fh.write('# Auto-generated by host/gen_font.py from %s.\n'
                 '# Row-major, MSB-first. TINY is exactly one 8px page.\n'
                 % (src if kind == 'gdi' else os.path.basename(src)))
        h1, b1 = emit(fh, 'SML', kind, src, sml_size, SML_CHARS)
        h2, b2 = emit(fh, 'BIG', kind, src, big_size, BIG_CHARS)
        h3, b3 = emit(fh, 'TINY', kind, src, tiny_size, TINY_CHARS, TINY_H,
                      tiny_ytop, caps=True)

    print('face %s (%s, %s)'
          % (face, kind, src if kind == 'gdi' else os.path.basename(src)))
    print('SML  H=%d glyphs=%d bytes=%d' % (h1, len(SML_CHARS), b1))
    print('BIG  H=%d glyphs=%d bytes=%d' % (h2, len(BIG_CHARS), b2))
    print('TINY H=%d glyphs=%d bytes=%d  (size %d, cropped from row %d)'
          % (h3, len(TINY_CHARS), b3, tiny_size, tiny_ytop))
    assert h3 == TINY_H, 'TINY must be exactly one page tall, got %d' % h3
    print('ccfont.py size = %d bytes' % os.path.getsize(out))


if __name__ == '__main__':
    main(sys.argv[1:])
