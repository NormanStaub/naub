"""The app switcher: a dropdown that opens out of one column's name slot.

    +------------------------------------+
    | ####----- | ######--- | ########## |  volume bars stay put   (page 0)
    |           |  MUSIC    |  SPOTIFY   |  the edited column goes blank,
    |-----------............-------------|  and so does its rule -- the gap
    |             SPOTIFY                |  is what says which one you are
    |   [ic]     [ ICON ]     [ic]       |  changing
    | o o o O o  [ ICON ]     .. .. |    |
    |------------------------------------|
    |  APP CTRL |||        SYS. ######    |  mode bar stays put   (pages 6-7)
    +------------------------------------+

One panel, 32 px tall, that slides down over the meter band (pages 2..5) from
the rule under the names.  Everything outside those pages is the mixer screen,
untouched, so the volume bars and the mode bar never move.

While it slides, the leading edge carries a solid rule (see compose) -- without
it the panel reads as content fading in rather than a surface coming down.

Four pages is not much, and the layout is what buys the icons their size back:

  * the name (panel page 0) gets the WHOLE WIDTH.  It is the selected app's
    name, not a column heading, so nothing needs to share the row with it and
    a long name has the full 128 px before it has to be trimmed.
  * the shelf (panel pages 1..3) is TOP-aligned, not bottom-aligned: every tier
    starts on the same page and the focused one simply reaches further down.
    That is what lets the focus be 24 px while its neighbours are 16 px inside
    a band that is only three pages tall.
  * the counter and the level meter (panel page 3) FLANK the focused icon
    rather than sitting under it -- markers to its left, level to its right, in
    the very row the tall icon is using.  A row of their own would have cost
    the shelf a third of its height.

The icons are 4-plane FRC grayscale (host/gen_switcher.py), so the whole frame
cycles four ways while the drawer is open.  Only the icons are part-duty: the
name, markers, level and mode bar are drawn into every plane, i.e. solid, so
they do not shimmer.

Pure Python, no hardware imports -- host/preview_drawer.py renders exactly what
the panel shows.
"""
import gfx

W = gfx.W
_ZERO = bytes(W)

# ---- where the panel lives -------------------------------------------------
PANEL_P0, PANEL_P1 = 2, 5     # the mixer's meter band; the pages we take over
NPAGE = PANEL_P1 - PANEL_P0 + 1
H = NPAGE * 8                 # 32 px of drop
N_PLANES = 4

# Panel-local y runs 0..31 (screen y = panel y + PANEL_P0 * 8).

# ---- the selected app's name (panel page 0) --------------------------------
NAME_P = 0
NAME_MAXW = W - 6             # the whole width, less a margin either side

# ---- the icon shelf (panel pages 1..3) ------------------------------------
# (size, panel page0).  Both tiers start on panel page 1 -- TOP-aligned, so the
# focused icon grows downward into the counter row instead of needing a taller
# band.  Both sizes are multiples of 8, so a blit is a byte-slice copy.
TIERS = ((24, 1), (16, 1))
SHELF_P0, SHELF_P1 = 1, 3
PITCH = 42                    # px between neighbouring slots: exactly 3 visible
CX = 64                       # the focused slot's centre
NEAR = 0.30                   # |distance| under this = the big tier.  Kept
                              # tight so the 24px icon never reaches the
                              # counter or the level meter beside it.

# ---- counter + level, sharing panel page 3 with the tall icon --------------
ROW_P = 3
CNT_X = 1                     # scroll counter: one marker per app, to the LEFT
CNT_END = 38                  # ... of the focused icon
LVL_X = 90                    # level meter, to its RIGHT
LVL_END = W - 2

RING = (0x1C, 0x22, 0x22, 0x22, 0x1C)     # 5px hollow marker, panel y26..y30
DISC = (0x1C, 0x3E, 0x3E, 0x3E, 0x1C)     # ... filled: the current selection
RING_W, RING_PITCH = 5, 7
SMALL = (0x1C, 0x14, 0x1C)                # 3px, for when 5px will not fit
SDISC = (0x1C, 0x1C, 0x1C)
SMALL_W, SMALL_PITCH = 3, 4

BAR_MASK = 0x18               # panel y27..28: the 2px-tall dot row
PEAK_MASK = 0x3E              # panel y26..30: the peak tick, taller than the bar

# Same ballistics as the mixer's bands (tuned for a ~16 ms tick): jump to any
# new peak, settle back smoothly, and let the peak tick fall ~6x slower so it
# detaches and hangs above the bar.
FALL = 0.18
PEAK_FALL = 0.028


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
    initial.  The audio session list and the icon catalogue are gathered
    separately (apps making noise vs. apps with a window), so a mismatch is
    normal and should look deliberate."""
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
    def __init__(self, icons, catalog, font, n=N_PLANES):
        """icons[app][tier][plane] and catalog[app] come from switcher_data.py;
        `font` (ccfont.TINY) draws the name and the initial on a placeholder."""
        self.icons = icons
        self.catalog = tuple(c.upper() for c in catalog)
        self.font = font
        self.n = n
        self.planes = [gfx.new() for _ in range(n)]
        self.panel = [bytearray(NPAGE * W) for _ in range(n)]
        # memoryviews built once, for both the icon blocks and the panel's own
        # pages.  A bytearray slice on the RIGHT of an assignment allocates a
        # copy: composing through raw slices measured 4.6 ms a frame against
        # 0.41 ms through views, and the allocation means GC, which is flicker.
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
        playing, so there are no markers and no icons.  The dropdown still
        opens (a dead gesture is worse than an empty shelf), it just has
        nothing to assign."""
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
            b = memoryview(_placeholder(size, self.font, key[1]))
            self._ph[key] = b
        return b

    # ---- the animated part ------------------------------------------------
    def draw_shelf(self, pos):
        """Icons with the focus at fractional index `pos`.  Redraws the counter
        and the level afterwards: the focused icon reaches into their page, so
        they have to be laid back over it."""
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
        self.draw_counter()
        self.draw_level()

    def update(self, level):
        """One amplitude (0..1) for the app under the focus; advance the level
        meter's ballistics one step."""
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
        self.draw_name()
        self.draw_counter()

    def draw_name(self):
        """The selected app, centred across the full width."""
        pan = self.panel[0]
        o = NAME_P * W
        pan[o:o + W] = _ZERO
        if self._name is not None:
            w, buf = self._name
            x = (W - w) >> 1
            for j in range(w):
                pan[o + x + j] |= buf[j]
        self._mirror(NAME_P, NAME_P + 1)

    def draw_counter(self):
        """One marker per app with a live session, filled at the selection,
        sitting to the LEFT of the focused icon."""
        pan = self.panel[0]
        o = ROW_P * W
        for x in range(o + CNT_X, o + CNT_END):
            pan[x] = 0
        n = len(self.apps)
        if n:
            avail = CNT_END - CNT_X
            # 5px rings while they fit; 3px markers when there are more apps
            # than that -- the count still has to be readable at a glance.
            if n * RING_PITCH - (RING_PITCH - RING_W) <= avail:
                ring, disc, wide, pitch = RING, DISC, RING_W, RING_PITCH
            else:
                ring, disc, wide, pitch = SMALL, SDISC, SMALL_W, SMALL_PITCH
            room = (avail + pitch - wide) // pitch
            if n > room:
                n = room
            for i in range(n):
                m = disc if i == self.idx else ring
                x = CNT_X + i * pitch
                for j in range(wide):
                    if x + j < CNT_END:
                        pan[o + x + j] |= m[j]
        self._mirror_row()

    def draw_level(self):
        """The selected app's level: a short dot bar with a peak tick on its
        leading edge, to the RIGHT of the focused icon."""
        pan = self.panel[0]
        o = ROW_P * W
        for x in range(o + LVL_X, o + W):
            pan[x] = 0
        avail = LVL_END - LVL_X
        fill = int(self.lvl * avail + 0.5)
        if fill > avail:
            fill = avail
        for i in range(fill):
            if (i & 3) < 2:                      # 2 px on, 2 px off
                pan[o + LVL_X + i] |= BAR_MASK
        if self.pk > 0.0:
            d = int(self.pk * avail + 0.5)
            if d < 1:
                d = 1
            elif d > avail:
                d = avail
            pan[o + LVL_X + d - 1] |= PEAK_MASK
        self._mirror_row()

    draw_row = draw_level        # the level is what moves every tick

    def _mirror_row(self):
        """Only the two ends of the row are solid chrome; the middle belongs to
        the focused icon, which is per-plane FRC and must not be flattened."""
        o = ROW_P * W
        src = self.pmv[0]
        for pan in self.panel[1:]:
            pan[o + CNT_X:o + CNT_END] = src[o + CNT_X:o + CNT_END]
            pan[o + LVL_X:o + W] = src[o + LVL_X:o + W]

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
        meters underneath, and while it is moving the last revealed row is
        forced solid -- the wipe's leading edge."""
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
        if reveal < H:
            # The wipe's edge: a solid rule on the last revealed row, so the
            # panel reads as a surface sliding over the meters rather than as
            # content appearing out of nowhere.
            y = reveal - 1
            row = self.fpage
            b = 1 << (y & 7)
            p = PANEL_P0 + (y >> 3)
            for k in range(self.n):
                dst = row[k][p]
                for x in range(W):
                    dst[x] |= b
