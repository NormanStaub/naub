"""Windows program-icon extraction -> 1-bpp packed bitmap for the e-paper.

Pipeline: executable HICON -> RGBA -> composite over white -> grayscale ->
resize to SIZE -> Floyd-Steinberg dither to 1-bpp -> pack rows (MSB=leftmost,
1=black ink). Falls back to a text glyph when an app has no extractable icon.
"""
from PIL import Image, ImageDraw, ImageFont

SIZE = 32  # matches the firmware's per-channel icon box

try:
    import win32api
    import win32con
    import win32gui
    import win32ui
    _HAVE_WIN32 = True
except ImportError:
    _HAVE_WIN32 = False


def icon_for(exe_path, label="", size=SIZE):
    """Return (w, h, packed_bytes) for a channel, never None.

    Tries the executable's icon; falls back to a letter glyph from `label` if the
    exe has no icon or the extraction comes back degenerate (near blank / solid),
    so the panel always shows something recognisable.
    """
    if exe_path and _HAVE_WIN32:
        img = _extract_exe_icon(exe_path, size)
        if img is not None:
            packed = pack_1bpp(_to_1bpp(img, size))
            if not _degenerate(packed):
                return packed
    return pack_1bpp(_to_1bpp(_text_icon(label, size), size))


def _to_1bpp(img, size):
    # The source already has a white background baked into RGB (the exe icon is
    # drawn over a white fill; the glyph is drawn on a white canvas), so flatten
    # via RGB. Do NOT alpha-composite: GDI often returns a zeroed alpha channel,
    # which would wipe the image to all-white.
    g = img.convert("RGB").convert("L")
    if g.size != (size, size):
        g = g.resize((size, size), Image.LANCZOS)
    # Threshold, not dither: at 32x32 on e-paper a luminance threshold keeps logos
    # crisp and recognisable; Floyd-Steinberg turns detailed icons into noise.
    return g.point(lambda v: 0 if v < 128 else 255, mode="1")


def _degenerate(packed):
    """True if the bitmap is almost all white or all black (a failed extraction)."""
    _w, _h, data = packed
    ink = sum(bin(b).count("1") for b in data)
    total = len(data) * 8
    ratio = ink / total if total else 0
    return ratio < 0.02 or ratio > 0.98


def pack_1bpp(img1):
    """Pack a PIL mode-'1' image: row-major, stride=ceil(w/8), MSB=x0, 1=ink."""
    w, h = img1.size
    stride = (w + 7) // 8
    data = bytearray(stride * h)
    px = img1.load()
    for y in range(h):
        base = y * stride
        for x in range(w):
            if px[x, y] == 0:  # mode '1': 0 = black = ink
                data[base + (x >> 3)] |= 0x80 >> (x & 7)
    return w, h, bytes(data)


def _text_icon(label, size):
    img = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    s = (label or "?").strip()
    glyph = s[0].upper() if s else "?"
    try:
        font = ImageFont.truetype("segoeui.ttf", int(size * 0.7))
    except Exception:
        font = ImageFont.load_default()
    try:
        bbox = d.textbbox((0, 0), glyph, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), glyph,
               fill=(0, 0, 0, 255), font=font)
    except Exception:
        d.text((size // 4, size // 4), glyph, fill=(0, 0, 0, 255), font=font)
    d.rectangle([0, 0, size - 1, size - 1], outline=(0, 0, 0, 255))
    return img


def _extract_exe_icon(exe_path, size):
    large = small = None
    try:
        large, small = win32gui.ExtractIconEx(exe_path, 0)
    except Exception:
        return None
    handles = list(large) + list(small)
    hicon = (large[0] if large else (small[0] if small else None))
    if hicon is None:
        return None
    try:
        screen_dc = win32gui.GetDC(0)
        hdc = win32ui.CreateDCFromHandle(screen_dc)
        membmp = win32ui.CreateBitmap()
        membmp.CreateCompatibleBitmap(hdc, size, size)
        memdc = hdc.CreateCompatibleDC()
        old = memdc.SelectObject(membmp)
        memdc.FillSolidRect((0, 0, size, size), 0xFFFFFF)  # white background
        win32gui.DrawIconEx(memdc.GetSafeHdc(), 0, 0, hicon, size, size, 0,
                            None, win32con.DI_NORMAL)
        bits = membmp.GetBitmapBits(True)
        img = Image.frombuffer("RGBA", (size, size), bits, "raw", "BGRA", 0, 1)
        memdc.SelectObject(old)
        return img.copy()
    except Exception:
        return None
    finally:
        try:
            win32gui.ReleaseDC(0, screen_dc)
        except Exception:
            pass
        for h in handles:
            try:
                win32gui.DestroyIcon(h)
            except Exception:
                pass
