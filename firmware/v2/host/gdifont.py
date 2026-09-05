"""Rasterize text with Windows GDI -- the only way to reach BITMAP fonts.

PIL/FreeType cannot open a Windows .FON at all, so the whole family of raster
faces (Small Fonts, Terminal, Fixedsys, MS Sans Serif, ...) is invisible to it.
Those are the faces actually *drawn* at a fixed pixel size rather than an
outline crushed onto a 6px grid, which is exactly what a 128x64 panel wants.

    import gdifont
    w, rows = gdifont.render('Small Fonts', 8, 'SPOTIFY')

`rows` is a list of bool lists, top row first.  Rendering goes through a 1-bit
memory DC with NONANTIALIASED_QUALITY, so what comes back is precisely the
glyph bitmap the face defines -- no greys, nothing to threshold.

For a raster face, `height` must be one the face actually ships; GDI will
happily scale a strike it does not have and the result looks like the outline
problem all over again.  strikes() reports which heights a face really has, by
rendering at each and checking the metrics GDI reports back.
"""
import win32con
import win32gui
import win32ui

MAX_W = 512


def _font(face, height, weight=win32con.FW_NORMAL):
    return win32ui.CreateFont({
        'name': face,
        'height': height,               # positive: cell height, matches raster strikes
        'weight': weight,
        'quality': win32con.NONANTIALIASED_QUALITY,
        'charset': win32con.ANSI_CHARSET,
    })


def metrics(face, height):
    """-> dict of what GDI actually gave us, which may not be what we asked."""
    dc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
    mdc = dc.CreateCompatibleDC()
    f = _font(face, height)
    mdc.SelectObject(f)
    tm = mdc.GetTextMetrics()
    mdc.DeleteDC()
    win32gui.ReleaseDC(0, dc.GetSafeHdc())
    return tm


def render(face, height, text, weight=win32con.FW_NORMAL):
    """-> (width, rows) where rows[y][x] is True for ink.  Top row first."""
    screen = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
    mdc = screen.CreateCompatibleDC()
    f = _font(face, height, weight)
    mdc.SelectObject(f)
    tm = mdc.GetTextMetrics()
    h = tm['tmHeight']
    w = min(mdc.GetTextExtent(text)[0], MAX_W)
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(screen, max(w, 1), max(h, 1))
    mdc.SelectObject(bmp)
    mdc.SetBkColor(0x000000)
    mdc.SetTextColor(0xFFFFFF)
    mdc.SetBkMode(win32con.OPAQUE)
    mdc.FillSolidRect((0, 0, max(w, 1), max(h, 1)), 0x000000)
    mdc.TextOut(0, 0, text)
    bits = bmp.GetBitmapBits(True)          # BGRX, bottom-up? no: top-down rows
    stride = max(w, 1) * 4
    rows = []
    for y in range(h):
        base = y * stride
        rows.append([bits[base + x * 4] > 127 for x in range(w)])
    win32gui.DeleteObject(bmp.GetHandle())
    mdc.DeleteDC()
    win32gui.ReleaseDC(0, screen.GetSafeHdc())
    return w, rows


def ink_span(rows):
    used = [i for i, r in enumerate(rows) if any(r)]
    return (used[0], used[-1]) if used else (0, -1)


def strikes(face, lo=5, hi=20):
    """Heights a raster face really ships: ask for each and keep the ones where
    GDI hands back exactly what was asked for."""
    out = []
    for h in range(lo, hi + 1):
        tm = metrics(face, h)
        if tm['tmHeight'] == h:
            out.append((h, tm['tmAscent'], tm['tmDescent'],
                        tm['tmInternalLeading']))
    return out
