"""A line wipe between two full-screen framebuffers.

    w = wipe.Wipe(p0=0, p1=6)
    w.start(old_fb, new_fb)
    while w.step(dst):        # composite one frame into dst
        show(dst)

Used when the mode bar flips: a vertical line sweeps RIGHT TO LEFT and the new
screen follows behind it, so a mode change reads as one screen replacing
another rather than the panel simply becoming different.

Cheap for the same reason everything else here is: in this framebuffer the byte
index IS the x position (index = page*W + x), so "columns 0..k from the old
screen, the rest from the new" is two slice copies per page -- 16 of them for
the whole frame -- rather than any per-pixel work.

The page range is deliberate.  The mode bar (page 7) is excluded so its ticks
can run their own animation underneath: they mean "where you are in the list",
the wipe means "the screen is changing", and overlaying one on the other would
muddle both.
"""
import gfx

W = gfx.W

STEP = 9                      # px per frame; 128 px in ~14 frames, which at
                              # the 16 ms tick is ~230 ms -- near enough the
                              # tick animation's own 15 frames that the two
                              # read as a single movement
EDGE = 0xFF                   # the leading line, full height of the band


class Wipe:
    def __init__(self, p0=0, p1=6, step=STEP):
        self.p0 = p0
        self.p1 = p1
        self.step_px = step
        self.x = -1               # -1 = idle
        self._old = None
        self._new = None

    @property
    def active(self):
        return self.x >= 0

    def start(self, old_fb, new_fb):
        """`old_fb` must be a snapshot: the caller's screen keeps being redrawn
        underneath, so the wipe cannot hold a live reference to it."""
        self._old = bytes(old_fb)
        self._new = new_fb
        self.x = W - 1

    def step(self, dst):
        """Composite one frame into dst.  -> True while still wiping."""
        if self.x < 0:
            return False
        x = self.x
        old = self._old
        new = self._new
        for p in range(self.p0, self.p1 + 1):
            o = p * W
            if x >= 0:
                dst[o:o + x + 1] = old[o:o + x + 1]
            if x + 1 < W:
                dst[o + x + 1:o + W] = new[o + x + 1:o + W]
        if x < W - 1:                    # the leading line, once it is on screen
            for p in range(self.p0, self.p1 + 1):
                dst[p * W + x] = EDGE
        self.x -= self.step_px
        if self.x < 0:
            # land exactly on the new screen, with no line left behind
            for p in range(self.p0, self.p1 + 1):
                o = p * W
                dst[o:o + W] = new[o:o + W]
            self.x = -1
            self._old = None
            return False
        return True
