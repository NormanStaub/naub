"""An eased line wipe between two full-screen framebuffers.

    w = wipe.Wipe(p0=0, p1=6)
    w.start(old_fb, new_fb, direction=-1)   # -1 right-to-left, +1 left-to-right
    while w.step(dst):                      # composite one frame into dst
        show(dst)

Used when the mode bar flips: a vertical line sweeps across and the new screen
follows behind it, so a mode change reads as one screen replacing another
rather than the panel simply becoming different.

**The direction carries meaning.**  It follows the way you are moving through
the mode list -- forward wipes right to left, back wipes left to right -- so
stepping back to APP CTRL undoes the movement that left it, rather than looking
like another step forward.

**The motion is eased**, the same way the app-switcher drawer eases open: the
line takes a fixed fraction of the remaining distance each frame, so it starts
quickly and settles rather than arriving at a constant speed and stopping dead.
A linear wipe reads as mechanical at this size; the ease is most of what makes
it feel like one surface sliding over another.

Cheap for the same reason everything else here is: in this framebuffer the byte
index IS the x position (index = page*W + x), so "these columns from the old
screen, the rest from the new" is two slice copies per page -- 16 of them for
the whole frame -- rather than any per-pixel work.

The page range is deliberate.  The mode bar (page 7) is excluded so its ticks
can run their own animation underneath: they mean "where you are in the list",
the wipe means "the screen is changing", and overlaying one on the other would
muddle both.
"""
import gfx

W = gfx.W

EASE = 0.30                   # fraction of the remaining distance per frame,
                              # matching demo_mixer's drawer DROP so the two
                              # animations feel like the same hand
SNAP = 1.5                    # px: below this, land on the target.  An
                              # exponential approach never actually arrives,
                              # and the last pixel would crawl for ever.
EDGE = 0xFF                   # the leading line, full height of the band
EDGE_W = 2                    # px.  One pixel was too thin to read: at the
                              # panel's default contrast (25, the top-down end
                              # of the view-angle range) a single column of ink
                              # that is on the glass for one animation step is
                              # most of the way to invisible, and the wipe read
                              # as content changing rather than as an edge
                              # sweeping over it.  The extra column is added
                              # BEHIND the line -- on the side already showing
                              # the new screen -- so the boundary between old
                              # and new stays exactly where the maths puts it
                              # and the thickness reads as a trail.

RIGHT_TO_LEFT = -1
LEFT_TO_RIGHT = 1


class Wipe:
    def __init__(self, p0=0, p1=6, ease=EASE):
        self.p0 = p0
        self.p1 = p1
        self.ease = ease
        self.dir = RIGHT_TO_LEFT
        self.pos = 0.0
        self.active = False
        self._old = None
        self._new = None

    def start(self, old_fb, new_fb, direction=RIGHT_TO_LEFT):
        """`old_fb` must be a snapshot: the caller's screen keeps being redrawn
        underneath, so the wipe cannot hold a live reference to it."""
        self._old = bytes(old_fb)
        self._new = new_fb
        self.dir = -1 if direction < 0 else 1
        # start just off the edge the line enters from, finish just off the
        # far one, so the first and last frames are wholly old and wholly new
        self.pos = float(W - 1) if self.dir < 0 else 0.0
        self.target = -1.0 if self.dir < 0 else float(W)
        self.active = True

    def step(self, dst):
        """Composite one frame into dst.  -> True while still wiping."""
        if not self.active:
            return False
        x = int(self.pos + 0.5)
        old = self._old
        new = self._new
        lo = 0 if x < 0 else (W if x > W else x)
        for p in range(self.p0, self.p1 + 1):
            o = p * W
            if self.dir < 0:
                # travelling left; what it has passed (to its right) is new
                if lo > 0:
                    dst[o:o + lo] = old[o:o + lo]
                if lo < W:
                    dst[o + lo:o + W] = new[o + lo:o + W]
            else:
                # travelling right; what it has passed (to its left) is new
                if lo > 0:
                    dst[o:o + lo] = new[o:o + lo]
                if lo < W:
                    dst[o + lo:o + W] = old[o + lo:o + W]
        # the line, plus EDGE_W-1 columns of trail behind it.  Travelling
        # left, "behind" is to the right (the columns it has already turned
        # new); travelling right, it is to the left.
        for j in range(EDGE_W):
            xj = x - j * self.dir
            if 0 <= xj < W:
                for p in range(self.p0, self.p1 + 1):
                    dst[p * W + xj] = EDGE

        d = self.target - self.pos
        if -SNAP < d < SNAP:
            for p in range(self.p0, self.p1 + 1):       # land on the new screen
                o = p * W
                dst[o:o + W] = new[o:o + W]
            self.active = False
            self._old = None
            return False
        self.pos += d * self.ease
        return True
