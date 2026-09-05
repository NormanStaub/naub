"""A backlight that follows you: full while you are using it, dim after a
minute, dark after five, fading between the three.

    dim = idle.Idle(bl)          # bl is a naubhw.Backlight
    ...
    dim.poke(now)                # a knob moved / a key went down
    dim.step(now)                # once a frame

Nothing here touches the framebuffer.  The panel keeps drawing at full rate
with the backlight at 0 -- an ST7565 is reflective enough to read in a bright
room with the light off, and more to the point, coming back has to be instant.
Stopping the redraw to save a milliamp would mean waking to a stale meter.

**Why a fade and not a switch.**  A backlight that snaps between levels reads
as a fault -- the eye catches the step and looks for what caused it.  Ramped
over a second or so it reads as the panel settling down, which is what it is.
Waking is the other way round: it is a response to something you just did, so
it is quick (FADE_UP_S) or it feels laggy.

**Why the ramp is not linear in duty.**  Perceived brightness goes roughly as
luminance ** (1/2.2), and the PWM duty IS the luminance, so a straight line in
duty looks like it plummets and then dawdles.  The fade interpolates in
perceptual space instead (GAMMA), which is most of what makes it look even.
Set GAMMA = 1.0 to go back to a plain duty ramp.

The percentages are duty, the same scale `bl.set()` has always used: 10 means
what `bl.set(10)` has always meant.
"""
import time

FULL = 100.0                  # what "in use" looks like

# (seconds idle, duty %).  Checked in order, last match wins, so this is a
# staircase down rather than a single timeout.
STAGES = ((60.0, 10.0),
          (300.0, 0.0))

FADE_UP_S = 0.10              # waking: fast enough to feel like a response
FADE_DOWN_S = 1.20            # settling: slow enough not to read as a fault
GAMMA = 2.2                   # see the note above


class Idle:
    def __init__(self, bl, full=FULL, stages=STAGES,
                 up=FADE_UP_S, down=FADE_DOWN_S, gamma=GAMMA):
        self.bl = bl
        self.full = full
        self.stages = stages
        self.up = up
        self.down = down
        self.gamma = gamma
        self.pct = full                  # where the backlight is now
        self.t_poke = time.monotonic()
        self._from = self._to = full
        self._t0 = self.t_poke
        self._dur = 0.0                  # 0 = not fading
        bl.set(full)

    # ---- input -----------------------------------------------------------
    def poke(self, now=None):
        """Something happened at the panel.  Restarts the clock, and brings the
        light back up if it had started to go."""
        if now is None:
            now = time.monotonic()
        self.t_poke = now
        if self._to != self.full:
            self._fade(self.full, self.up, now)

    # ---- the fade --------------------------------------------------------
    def _fade(self, target, dur, now):
        self._from = self.pct
        self._to = target
        self._t0 = now
        self._dur = dur

    def _set(self, pct):
        self.pct = pct
        self.bl.set(pct)

    def _mix(self, u):
        """Perceptual lerp with a smoothstep on top: no visible corner where
        the fade starts or stops."""
        s = u * u * (3.0 - 2.0 * u)
        g = self.gamma
        if g == 1.0:
            return self._from + (self._to - self._from) * s
        a = (self._from * 0.01) ** (1.0 / g)
        b = (self._to * 0.01) ** (1.0 / g)
        return 100.0 * (a + (b - a) * s) ** g

    def target(self, idle_s):
        """The duty this much idle time calls for."""
        t = self.full
        for after, pct in self.stages:
            if idle_s >= after:
                t = pct
        return t

    def step(self, now):
        """Call once a frame.  -> True while the backlight is moving.

        Cheap when there is nothing to do: two compares and a subtraction, which
        is what it does for all but a second or so out of every five minutes."""
        if self._dur:
            u = (now - self._t0) / self._dur
            if u >= 1.0:
                self._set(self._to)
                self._dur = 0.0
                return False
            self._set(self._mix(u))
            return True
        want = self.target(now - self.t_poke)
        if want != self._to:
            # down slowly, up quickly -- they mean different things
            self._fade(want, self.down if want < self.pct else self.up, now)
            return True
        return False
