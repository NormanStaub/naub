"""Which apps the switcher offers, and for how long.

    wh = wheel.Wheel()
    wh.set_live(['SPOTIFY', 'CHROME'], now)   # from the host's `apps`
    wh.feed([62, 0], now)                     # from the host's `ameter`
    wh.tick(now)                              # expire what has gone quiet
    if wh.dirty:
        dr.set_apps(wh.names)

The host tells us what is streaming *right now*.  That alone makes a bad list:
close a browser tab and the app vanishes from under your finger, while a
notification chime that lasted a second and a half appears as something you
could assign a knob to.

So the device keeps its own list, and it keeps it on two rules:

  * **A run of audio, not a blip.**  An app has to be audible for QUALIFY_S
    within one run -- a run being broken by GAP_S of silence -- before it counts
    as something you were listening to.  A Windows notification or error sound
    is one to three seconds and does not come back, so it never qualifies and
    never clutters the wheel.
  * **Qualified apps linger.**  Once an app has earned its place it stays
    offered for HOLD_S after it goes quiet, so pausing a track, or closing the
    thing that was playing, does not take the entry away before you have
    finished with it.

This lives on the device deliberately.  The host would have to hold the same
state and stream the same conclusions, and it is the user's PC: the panel has a
whole core doing nothing between frames, so the accounting belongs here.

An app the user has *assigned to a column* is a different matter and is not
handled here -- that assignment is held by the host and survives the app
closing entirely (see host/naub_bridge.py).  This list is only about what is
worth OFFERING, and offering a column an app that has been shut for an hour is
not useful.

Order is stable on purpose: a name keeps its place in the wheel from the moment
it first appears until it expires, so the list never reshuffles under a finger
that is already scrolling it.
"""
import time

AUDIBLE = 0.02        # level that counts as "playing"; below this is silence
QUALIFY_S = 4.0       # audible for this long in one run = a real stream
GAP_S = 2.0           # ... and this much silence ends the run
HOLD_S = 600.0        # how long a qualified app stays offered after it stops
CHECK_S = 0.5         # how often expiry is even worth looking at

NS = 1000000000

HEARD, LAST, OK = 0, 1, 2      # fields of the per-app record


class Wheel:
    def __init__(self, qualify=QUALIFY_S, gap=GAP_S, hold=HOLD_S,
                 audible=AUDIBLE):
        self.qualify = int(qualify * NS)
        self.gap = int(gap * NS)
        self.hold = int(hold * NS)
        self.audible = audible
        self.order = []       # every name known, in the order first seen
        self.live = []        # what the host says is streaming, in its order
        self.names = []       # the wheel: live + retained, in `order` order
        self._sig = []        # ... and which of them are live, for `dirty`
        self._st = {}         # name -> [heard ns, last audible ns, qualified]
        self._lv = {}         # name -> level, for the live ones
        self._t_feed = 0
        self._t_check = 0
        # False, not True: with no host talking there is no list to push, and
        # overwriting the drawer's baked-in demo list with an empty one would
        # make the switcher useless standalone.  The first `apps` sets it.
        self.dirty = False

    # ---- input -----------------------------------------------------------
    def set_live(self, names, now):
        """The host's `apps` list.  New names join the end of the order; ones
        that have gone are kept only if they earned it."""
        self.live = names
        for n in names:
            if n not in self._st:
                self._st[n] = [0, now, False]
                self.order.append(n)
        self._rebuild(now)

    def feed(self, levels, now):
        """One level per entry in `live`, same order.  Runs the accounting that
        decides whether an app is a stream or a chime."""
        dt = now - self._t_feed
        self._t_feed = now
        if dt <= 0 or dt > NS:       # first call, or a long stall: no credit
            dt = 0
        n = len(levels)
        for i, name in enumerate(self.live):
            v = levels[i] if i < n else 0.0
            self._lv[name] = v
            if v < self.audible:
                continue
            st = self._st.get(name)
            if st is None:
                st = self._st[name] = [0, now, False]
                self.order.append(name)
                self.dirty = True
            if now - st[LAST] > self.gap:
                st[HEARD] = 0                     # the run was broken
            st[HEARD] += dt
            st[LAST] = now
            if not st[OK] and st[HEARD] >= self.qualify:
                st[OK] = True

    def tick(self, now):
        """Expire anything that has been quiet for too long."""
        if now - self._t_check < CHECK_S * NS:
            return
        self._t_check = now
        self._rebuild(now)

    # ---- output ----------------------------------------------------------
    def level(self, i):
        """The level of wheel entry i.  A retained app is not playing, so it
        reads zero -- which is the truth, and is also how you can tell."""
        if 0 <= i < len(self.names):
            n = self.names[i]
            return self._lv.get(n, 0.0) if n in self.live else 0.0
        return 0.0

    def held(self, name):
        """Is this entry only in the list because we are holding it?"""
        return name not in self.live

    # ---- internals -------------------------------------------------------
    def _rebuild(self, now):
        keep = []
        drop = []
        for n in self.order:
            if n in self.live:
                keep.append(n)
                continue
            st = self._st.get(n)
            if st and st[OK] and now - st[LAST] < self.hold:
                keep.append(n)
            else:
                drop.append(n)
        for n in drop:
            self.order.remove(n)
            if n in self._st:
                del self._st[n]
            if n in self._lv:
                del self._lv[n]
        # Dirty on the HELD flags too, not just the membership.  An entry that
        # stops playing keeps its slot, so the list is unchanged -- but it has
        # gone from something you can hear to something being held for you, and
        # anything drawing the list is entitled to know.
        sig = [n in self.live for n in keep]
        if keep != self.names or sig != self._sig:
            self.names = keep
            self._sig = sig
            self.dirty = True
