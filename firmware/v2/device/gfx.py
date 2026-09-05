"""Pure-Python 1-bit page framebuffer for the ST7565R (128x64, 8 pages).

Byte layout matches the panel: index = page*W + x, bit (1<<(y & 7)) = row within
the page (LSB = top). Identical on CPython and CircuitPython, so a PC preview of
`fb` is exactly what the panel shows.
"""
W = 128
H = 64
PAGES = H // 8

def new():
    return bytearray(W * PAGES)

def clear(fb, on=False):
    v = 0xFF if on else 0x00
    for i in range(len(fb)):
        fb[i] = v

def px(fb, x, y, on=True):
    if x < 0 or x >= W or y < 0 or y >= H:
        return
    i = (y >> 3) * W + x
    b = 1 << (y & 7)
    if on:
        fb[i] |= b
    else:
        fb[i] &= ~b & 0xFF

def rect(fb, x0, y0, x1, y1, on=True):        # filled, inclusive
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            px(fb, x, y, on)

def frame(fb, x0, y0, x1, y1, on=True):       # 1px outline, inclusive
    for x in range(x0, x1 + 1):
        px(fb, x, y0, on); px(fb, x, y1, on)
    for y in range(y0, y1 + 1):
        px(fb, x0, y, on); px(fb, x1, y, on)

def glyph(fb, font, ch, x, y, on=True):
    g = font["G"].get(ch)
    if g is None:
        g = font["G"].get("?")
        if g is None:
            return 0
    w, data = g
    stride = (w + 7) // 8
    Hh = font["H"]
    for gy in range(Hh):
        base = gy * stride
        row = y + gy
        for gx in range(w):
            if (data[base + (gx >> 3)] >> (7 - (gx & 7))) & 1:
                px(fb, x + gx, row, on)
    return w

def blit_icon(fb, icon, ox, oy, on=True, scale=1):
    # V1 icon logic (icons.rs / draw_icon_center), forced binary: 1 bit = solid
    # black ink, no dither. icon = (w, h, data) row-major MSB-first, stride (w+7)//8.
    w, h, data = icon
    stride = (w + 7) // 8
    for yy in range(h):
        base = yy * stride
        for xx in range(w):
            if (data[base + (xx >> 3)] >> (7 - (xx & 7))) & 1:
                if scale == 1:
                    px(fb, ox + xx, oy + yy, on)
                else:
                    px0 = ox + xx * scale
                    py0 = oy + yy * scale
                    for sy in range(scale):
                        for sx in range(scale):
                            px(fb, px0 + sx, py0 + sy, on)

def measure(font, s, spacing=1):
    total = 0
    G = font["G"]
    for ch in s:
        g = G.get(ch) or G.get("?")
        if g:
            total += g[0] + spacing
    return total - spacing if total else 0

def text(fb, font, s, x, y, on=True, spacing=1):
    cx = x
    for ch in s:
        w = glyph(fb, font, ch, cx, y, on)
        cx += w + spacing
    return cx - x - spacing

def text_center(fb, font, s, y, on=True, spacing=1):
    w = measure(font, s, spacing)
    text(fb, font, s, (W - w) // 2, y, on, spacing)
    return w
