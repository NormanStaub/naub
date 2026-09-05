"""The app switcher: a dropdown that opens out of one column's name slot.

    +------------------------------------+
    | ####----- | ######--- | ########## |  volume bars stay put   (page 0)
    |           |  MUSIC    |  SPOTIFY   |  the edited column goes blank,
    |-----------............-------------|  and so does its rule -- the gap
    |        o o o O o o                 |  is what says which one you are
    |   [ic]   [ ICON ]   [ic]           |  changing
    |  SPOTIFY  .. .. .. .. ..     |     |
    | APP CTRL  |||  SYS. ############   |  footer stays put       (page 7)
    +------------------------------------+

One panel, 40 px tall, that slides down over the meter band (pages 2..6) from
the rule under the names.  Everything outside those pages is the mixer screen,
untouched, so the volume bars and the footer never move.

Three moving parts, and they update at different rates, which is the whole
reason the panel is built page by page:

  * the counter (panel page 0) -- one ring per app with live audio, filled at
    the current selection.  Redrawn only when the selection or the app list
    changes.
  * the shelf (panel pages 1..3) -- the focused icon at 24 px flanked by its
    neighbours at 16 px, all bottom-aligned so they zoom off a common floor.
    Redrawn while scrolling.
  * the bottom row (panel page 4) -- the selected app's name and a horizontal
    level meter for what THAT app is playing, with the same rise-instantly /
    settle-slowly ballistics as the mixer's bands and a peak tick riding the
    leading edge.  Redrawn every tick.

The icons are 4-plane FRC grayscale (host/gen_switcher.py), so the whole frame
cycles four ways while the drawer is open.  Only the icons are part-duty: the
bars, names, meter and footer are drawn into every plane, i.e. solid, so they
do not shimmer.

Pure Python, no hardware imports -- host/preview_drawer.py renders exactly what
the panel shows.
"""
import gfx

W = gfx.W
_ZERO = bytes(W)

# ---- where the panel lives -------------------------------------------------
PANEL_P0, PANEL_P1 = 2, 6     # the mixer's meter band; the pages we take over
NPAGE = PANEL_P1 - PANEL_P0 + 1
H = NPAGE * 8                 # 40 px of drop
N_PLANES = 4

# Panel-local y runs 0..39 (screen y = panel y + PANEL_P0 * 8).

# ---- the scroll counter (panel page 0) ------------------------------------
# 5x5 rings, one per app with a live audio session, the current one filled.
# Column masks for panel y1..y5, so the row sits clear of both page edges.
RING = (0x1C, 0x22, 0x22, 0x22, 0x1C)
DISC = (0x1C, 0x3E, 0x3E, 0x3E, 0x1C)
CNT_PITCH = 7                 # 5 px ring + 2 px gap
CNT_MAX = 17                  # more than fits; the rest are simply not drawn

# ---- the icon shelf (panel pages 1..3) ------------------------------------
# (size, panel page0).  Both tiers bottom-align on panel y31 and both sizes are
# multiples of 8, so a blit is a byte-slice copy with no shifting.
TIERS = ((24, 1), (16, 2))
SHELF_P0, SHELF_P1 = 1, 3
PITCH = 40                    # px between neighbouring slots
CX = 64                       # the focused slot's centre
NEAR = 0.5                    # |distance| under this = the big tier

# ---- the bottom row (panel page 4) ----------------------------------------
ROW_P = 4
NAME_X = 1
NAME_MAXW = 46                # names longer than this are trimmed with '..'
BAR_GAP = 5                   # px between the name and the bar
BAR_MASK = 0x0C               # panel y34..35: the 2px-tall dot row
PEAK_MASK = 0x3E              # panel y33..37: the peak tick, taller than the bar
RULE = 0x80                   # panel y39: the line above the footer
BAR_END = W - 2

# Same ballistics as the mixer's bands (tuned for a ~16 ms tick): jump to any
# new peak, settle back smoothly, and let the peak tick fall ~6x slower so it
# detaches and hangs above the bar.
FALL = 0.18
PEAK_FALL = 0.028

_ROW_RULE = bytes((RULE,)) * W       # clears the row AND lays the rule


def fit(font, s, width=NAME_MAXW):
    """Trim a label to `width`, marking it when something was cut."""
    if gfx.measure(font, s, 0) <= width:
        return s
    while s and gfx.measure(font, s + '..', 0) > width:
        s = s[:-1]
    return s + '..'


def _blit(dst, src, size, page0, x):
    """Copy a size x size page-aligned block into the panel at column x."""
    s = 0
    w = size
    if x < 0:
        s = -x
        w += x
        x = 0
    if x + w > W:
        w = W - x
    if w <= 0:
        return
    o = page0 * W + x
    for _ in range(size >> 3):
        dst[o:o + w] = src[s:s + w]
        o += W
        s += size


def _bpx(b, size, x, y):
    if 0 <= x < size and 0 <= y < size:
        b[(y >> 3) * size + x] |= 1 << (y & 7)


def _placeholder(size, font=None, ch=''):
    """A block for an app we have no icon for: a rounded frame, plus its
    initial when the tier is big enough to read one.  The audio session list
    and the icon catalogue are gathered separately (apps making noise vs. apps
    with a window), so a mismatch is normal and should look deliberate."""
    b = bytearray((size >> 3) * size)
    e = size - 1
    for x in range(2, e - 1):
        _bpx(b, size, x, 0)
        _bpx(b, size, x, e)
    for y in range(2, e - 1):
        _bpx(b, size, 0, y)
        _bpx(b, size, e, y)
    for x, y in ((1, 1), (e - 1, 1), (1, e - 1), (e - 1, e - 1)):
        _bpx(b, size, x, y)
    g = font['G'].get(ch) if font else None
    if g:
        gw, data = g
        gh = font['H']
        stride = (gw + 7) // 8
        ox = (size - gw) >> 1
        oy = (size - gh) >> 1
        for gy in range(gh):
            base = gy * stride
            for gx in range(gw):
                if (data[base + (gx >> 3)] >> (7 - (gx & 7))) & 1:
                    _bpx(b, size, ox + gx, oy + gy)
    return bytes(b)


class Drawer:
    def __init__(self, icons, catalog, font, big=None, n=N_PLANES):
        """icons[app][tier][plane] and catalog[app] come from switcher_data.py;
        `font` draws the bottom-row name (ccfont.TINY) and `big` the initial on
        a placeholder icon (ccfont.SML)."""
        self.icons = icons
        self.catalog = tuple(c.upper() for c in catalog)
        self.font = font
        self.big = big
        self.n = n
        self.planes = [gfx.new() for _ in range(n)]
        self.panel = [bytearray(NPAGE * W) for _ in range(n)]
        # memoryviews built once, for both the icon blocks and the panel's own
        # pages.  A bytearray slice on the RIGHT of an assignment allocates a
        # copy: composing through raw slices measured 4.6 ms a frame against
        # 0.6 ms through views, and the allocation means GC, i.e. flicker.
        self.mv = [[[memoryview(b) for b in tier] for tier in app]
                   for app in icons]
        self.pmv = [memoryview(pan) for pan in self.panel]
        self.ppage = [[mv[p * W:(p + 1) * W] for p in range(NPAGE)]
                      for mv in self.pmv]
        self.fpage = [[memoryview(fb)[p * W:(p + 1) * W]
                       for p in range(gfx.PAGES)] for fb in self.planes]
        self._ph = {}
        self.idx = 0
        self.lvl = 0.0
        self.pk = 0.0
        self._name = None
        self.set_apps(self.catalog)

    # ---- what is in the list ---------------------------------------------
    def set_apps(self, names):
        """The apps with a live audio session, in the host's order.  Names we
        have no icon for still get a slot -- the counter is meant to be the
        number of things making noise, not the number we have art for.

        An EMPTY list is legal and means exactly what it looks like: nothing is
        playing, so there are no rings and no icons.  The dropdown still opens
        (a dead gesture is worse than an empty shelf), it just has nothing to
        assign."""
        self.apps = tuple(n for n in names if n)
        self.cat = tuple(self.catalog.index(a.upper())
                         if a.upper() in self.catalog else -1
                         for a in self.apps)
        if self.idx >= len(self.apps):
            self.idx = len(self.apps) - 1
        if self.idx < 0:
            self.idx = 0

    def index_of(self, name):
        u = name.upper()
        for i, a in enumerate(self.apps):
            if a.upper() == u:
                return i
        return -1

    def _icon(self, i, t, k):
        c = self.cat[i]
        if c >= 0:
            return self.mv[c][t][k]
        size = TIERS[t][0]
        key = (size, self.apps[i][:1])
        b = self._ph.get(key)
        if b is None:
            # cached as a memoryview for the same reason the icons are: _blit
            # slices its source every frame, and slicing bytes allocates.
            b = memoryview(_placeholder(
                size, self.big if size >= 24 else None, key[1]))
            self._ph[key] = b
        return b

    # ---- the animated part ------------------------------------------------
    def draw_shelf(self, pos):
        """Icons with the focus at fractional index `pos`."""
        for pan in self.panel:
            for p in range(SHELF_P0, SHELF_P1 + 1):
                pan[p * W:(p + 1) * W] = _ZERO
        i = int(pos) - 1
        if i < 0:
            i = 0
        last = int(pos) + 3
        if last > len(self.apps):
            last = len(self.apps)
        while i < last:
            d = i - pos
            ad = -d if d < 0 else d
            t = 0 if ad < NEAR else 1
            size, p0 = TIERS[t]
            x = CX + int(round(d * PITCH)) - (size >> 1)
            if x < W and x + size > 0:
                for k in range(self.n):
                    _blit(self.panel[k], self._icon(i, t, k), size, p0, x)
            i += 1

    def update(self, level):
        """One amplitude (0..1) for the app under the focus; advance the
        bottom row's ballistics one step."""
        if level < 0.0:
            level = 0.0
        elif level > 1.0:
            level = 1.0
        x = self.lvl
        x = level if level >= x else x + (level - x) * FALL
        self.lvl = x
        p = self.pk
        self.pk = x if x >= p else p + (x - p) * PEAK_FALL

    # ---- the parts that change only with the selection --------------------
    def set_focus(self, idx):
        self.idx = idx
        nm = self.apps[idx] if 0 <= idx < len(self.apps) else ''
        self._name = gfx.text_strip(self.font, fit(self.font, nm), 0) \
            if nm else None
        self.draw_counter()
        self.draw_row()

    def draw_counter(self):
        pan = self.panel[0]
        pan[0:W] = _ZERO
        n = len(self.apps)
        if n > CNT_MAX:
            n = CNT_MAX
        x0 = (W - (n * CNT_PITCH - 2)) >> 1
        if x0 < 0:
            x0 = 0
        for i in range(n):
            m = DISC if i == self.idx else RING
            x = x0 + i * CNT_PITCH
            for j in range(5):
                if 0 <= x + j < W:
                    pan[x + j] |= m[j]
        self._mirror(0, 1)

    def draw_row(self):
        """Name at the left, then a dot bar for that app's level with a peak
        tick on its leading edge.  The rule above the footer comes free: the
        row is cleared TO the rule rather than to zero."""
        pan = self.panel[0]
        o = ROW_P * W
        pan[o:o + W] = _ROW_RULE
        x = NAME_X
        if self._name is not None:
            w, buf = self._name
            for j in range(w):
                pan[o + x + j] |= buf[j]
            x += w
        start = x + BAR_GAP
        avail = BAR_END - start
        if avail > 0:
            fill = int(self.lvl * avail + 0.5)
            if fill > avail:
                fill = avail
            for i in range(fill):
                if (i & 3) < 2:                  # 2 px on, 2 px off
                    pan[o + start + i] |= BAR_MASK
            if self.pk > 0.0:
                d = int(self.pk * avail + 0.5)
                if d < 1:
                    d = 1
                elif d > avail:
                    d = avail
                pan[o + start + d - 1] |= PEAK_MASK
        self._mirror(ROW_P, ROW_P + 1)

    def _mirror(self, p0, p1):
        """Solid chrome is identical in every FRC plane, so draw it once."""
        a = p0 * W
        b = p1 * W
        src = self.pmv[0][a:b]
        for pan in self.panel[1:]:
            pan[a:b] = src

    # ---- compositing ------------------------------------------------------
    def set_background(self, fb, gap=None):
        """Put the (binary) mixer frame behind the panel, in every plane.

        `gap` is (page, x, w): the name cell of the column being edited, which
        is blanked along with its slice of the rule -- that hole is what the
        drawer appears to come out of."""
        for p in self.planes:
            p[:] = fb
        if gap:
            page, x, w = gap
            o = page * W + x
            blank = _ZERO[:w]
            for p in self.planes:
                p[o:o + w] = blank

    def compose(self, reveal):
        """Overlay the top `reveal` px of the panel onto the background.  Rows
        below it are left alone, so a half-open drawer still shows the live
        meters underneath."""
        if reveal <= 0:
            return
        if reveal > H:
            reveal = H
        full = reveal >> 3
        rem = reveal & 7
        for k in range(self.n):
            fb = self.planes[k]
            pages = self.ppage[k]
            for p in range(full):
                o = (PANEL_P0 + p) * W
                fb[o:o + W] = pages[p]
            if rem:
                # The one page the panel edge cuts through: panel above the
                # edge, whatever was behind below it, merged byte by byte.
                # Only ever one page, and only while the drawer is moving.
                m = (1 << rem) - 1
                keep = ~m & 0xFF
                src = pages[full]
                dst = self.fpage[k][PANEL_P0 + full]
                for x in range(W):
                    dst[x] = (src[x] & m) | (dst[x] & keep)
