"""App-switcher carousel: a focused icon flanked by smaller neighbours.

    +----------------------------------+
    |  [ic]   [  ICON  ]   [ic]        |   zoomed shelf of app icons
    |         . . o . . .              |   position rail
    |            CHROME                |   app name
    +----------------------------------+

Pure Python, no hardware imports, so host/preview_switcher.py renders exactly
what the panel shows.  Icon pixels come precomputed from host/gen_switcher.py:
each app is FRC-encoded once per size tier into page-aligned blocks, so drawing
a frame is byte-slice copies instead of per-pixel work.

    car = carousel.Carousel(switcher_data.ICONS, switcher_data.APPS)
    car.render(pos)                 # pos is fractional -> animates
    car.set_focus(ccfont.SML, 2)    # name + rail, only on change
    # then cycle car.planes through disp.show_pages() at max rate

Two things make this cheap enough to animate at ~138 Hz on the RP2350:

  * every tier size is a multiple of 8 and sits on a page boundary, so a blit
    is `fb[o:o+w] = src[s:s+w]` — no bit shifting, no per-pixel loop;
  * unfocused icons are dimmed by drawing them into *fewer FRC planes*, which
    lowers their duty cycle and therefore their gray.  Depth cue for free, no
    second copy of the pixel data.
"""
import gfx

W = gfx.W
_ZERO = bytes(W)

# (icon size, top page).  All tiers are bottom-aligned on page 4 (y=39) so the
# icons zoom off a common shelf.  Sizes are multiples of 8 -> page-aligned.
TIERS = ((40, 0), (32, 1), (24, 2))
BAND = (0, 4)                 # pages the icon shelf owns
RAIL_Y = 44                   # page 5: the position rail
LABEL_PAGE = 6                # pages 6..7: the 16px app name

PITCH = 44                    # px between neighbouring app slots
N_PLANES = 4

# Which FRC planes an icon is drawn into, by ink level.  4 = full black,
# 2 = half duty ~ mid gray.  Spread the kept planes over the cycle so the
# dimmed icons stay steady rather than flickering in bursts.
_KEEP = {4: (0, 1, 2, 3), 3: (0, 1, 2), 2: (0, 2)}


def tier_of(d):
    """|distance from focus| -> size tier.  Crossing these while animating is
    what produces the zoom; at rest a neighbour is always the small tier."""
    return 0 if d < 0.35 else (1 if d < 0.80 else 2)


def ink_of(d):
    """|distance from focus| -> how many planes the icon is drawn into."""
    return 4 if d < 0.35 else (3 if d < 1.30 else 2)


def _blit(fb, src, size, page0, x):
    """Copy a size x size page-aligned block to column x, clipped to the screen."""
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
        fb[o:o + w] = src[s:s + w]
        o += W
        s += size


def fit(font, s, width=W - 4):
    """Trim a label to the screen, marking it when something was cut."""
    if gfx.measure(font, s) <= width:
        return s
    while s and gfx.measure(font, s + '..') > width:
        s = s[:-1]
    return s + '..'


class Carousel:
    def __init__(self, icons, names, pitch=PITCH, n=N_PLANES):
        """icons[app][tier][plane] -> bytes block (see host/gen_switcher.py)."""
        self.icons = icons
        self.names = names
        self.pitch = pitch
        self.planes = [gfx.new() for _ in range(n)]
        # memoryviews built once: render() slices these every frame and bytes
        # slicing would copy.  Allocation in the frame loop = GC = visible flicker.
        self.mv = [[[memoryview(b) for b in tier] for tier in app] for app in icons]

    # ---- the animated part ----------------------------------------------
    def render(self, pos):
        """Draw the icon shelf with the focus at fractional index `pos`."""
        p0, p1 = BAND
        for fb in self.planes:
            for p in range(p0, p1 + 1):
                fb[p * W:(p + 1) * W] = _ZERO
        i = int(pos) - 2
        if i < 0:
            i = 0
        last = int(pos) + 3
        if last > len(self.icons):
            last = len(self.icons)
        while i < last:
            d = i - pos
            ad = -d if d < 0 else d
            t = tier_of(ad)
            size, page0 = TIERS[t]
            x = 64 + int(round(d * self.pitch)) - (size >> 1)
            if x < W and x + size > 0:
                mvs = self.mv[i][t]
                for k in _KEEP[ink_of(ad)]:
                    _blit(self.planes[k], mvs[k], size, page0, x)
            i += 1

    # ---- the parts that only change when the selection does --------------
    def set_focus(self, font, idx, invert=False):
        """Redraw the app name and the position rail for app `idx`."""
        self._rail(idx)
        self._label(font, self.names[idx], invert)
        self._mirror(RAIL_Y >> 3, LABEL_PAGE + 2)

    def _rail(self, idx):
        fb = self.planes[0]
        p = RAIL_Y >> 3
        for i in range(p * W, (p + 1) * W):
            fb[i] = 0
        n = len(self.names)
        step = 5
        x0 = (W - (n - 1) * step) >> 1
        for i in range(n):
            x = x0 + i * step
            if i == idx:
                gfx.rect(fb, x - 1, RAIL_Y - 1, x + 1, RAIL_Y + 1)
            else:
                gfx.px(fb, x, RAIL_Y)

    def _label(self, font, s, invert=False):
        fb = self.planes[0]
        v = 0xFF if invert else 0
        for i in range(LABEL_PAGE * W, (LABEL_PAGE + 2) * W):
            fb[i] = v
        gfx.text_center(fb, font, fit(font, s), LABEL_PAGE * 8, on=not invert)

    def _mirror(self, page0, page1):
        """Copy pages [page0, page1) from plane 0 into the rest — solid black
        chrome (text, rail) is identical in every FRC plane."""
        a = page0 * W
        b = page1 * W
        src = self.planes[0]
        for fb in self.planes[1:]:
            fb[a:b] = src[a:b]
