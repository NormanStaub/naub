# Demo: the naub V2 mixer screen, with the app-switcher dropdown as the column
# picker, the mode bar along the bottom, and the aux keys wired up.
#
# THE LEVELS ARE REAL.  host/naub_bridge.py streams this PC's actual per-app
# WASAPI peaks over the USB CDC data port; the only invented part is the shape
# across the 8 bands, because Windows exposes no per-app spectrum (PROTOCOL.md
# section 11).  With no host talking, the meters sit FLAT rather than inventing
# a signal -- a meter that moves when nothing is playing is a liar, and it also
# hides the one thing you need to know, which is that the bridge is not
# running.  Set SIMULATE = True to fake them anyway for UI work.
#
# The knobs are the same story: the panel is a display, and it is the host that
# talks to WASAPI.  Without the bridge, turning a knob moves the on-screen bar
# and nothing else.
#
#   ENC1-3 (the three top knobs), one per column:
#       turn        set that app's volume
#       click       open the app switcher for that column; click again to assign
#       hold 0.6 s  mute / unmute that column
#   ENC4 (lower-right):
#       turn        system volume
#       click       system mute            (V1's system-channel click)
#   BTN1 / BTN2 (the upper pair):  previous / next mode
#   BTN3 / BTN4 / BTN5 (bottom row, left to right):
#       |<<  >||  >>|  -- media transport, sent as USB HID so it works with no
#       host software running at all (see device/media.py).  NOT in EQ mode:
#       there the leftmost key resets the EQ to a flat default instead, and
#       the other two are unmapped -- a transport key is no use while your
#       hands are on the EQ, and a reset is.
#
# The switcher's list is the device's own, not the host's (device/wheel.py).
# The host says what is streaming right now; the panel decides what is worth
# OFFERING -- an app has to play for a few seconds in one run before it earns a
# place, so notification chimes never appear, and once it has earned one it
# keeps it for ten minutes after it goes quiet.  Column assignments are a
# separate thing entirely: those are held by the host, survive the app closing,
# and come back when it relaunches.
#
# The backlight follows you: full while you are touching it, 10% after a
# minute, off after five, fading between the three (device/idle.py).  Any knob
# or key wakes it, and the wake also does whatever it was pressed for -- a mixer
# you have to poke twice is worse than one that is dim.
#
# CONFIG's ADVANCED DISPLAY submenu drives the ST7565's own registers live --
# the same ones bring-up swept by hand to get a picture at all.  Two of them
# can black the panel out (contrast 0, ALL PIXELS ON); the cursor does not
# move when they do, so the way back is the knob you just turned.  Nothing is
# saved, so a power cycle is the other way back.
#
# CONFIG is the last mode: three knobs across the top drive it, labelled on
# screen -- ENC1 the tabs, ENC2 the cursor, ENC3 the focused value, live.  Only
# DISPLAY VIEW ANGLE is a real setting and even that is a drawing for now; the
# rest are placeholders in the right shape.  CUSTOM 1 is still a stub.  The
# switcher opens only in APP CTRL, since that is the mode it belongs to.
#
# The mixer screen is binary (one plane, ~400 Hz); the switcher's icons are FRC
# grayscale, so the frame cycles four ways while the drawer is open.  n_planes
# follows whichever is on screen.
#
# Deploy: python host/deploy.py mixer
import random
import time
import naubhw, ccfont, config, drawer, eqcurve, eqscreen, idle, link, media
import mixer, wheel, wipe
import switcher_data

HOLD = 0.6            # seconds held = mute rather than open the picker
TICK_MS = 16          # meter update rate; the FALL constants are tuned to it
MARQ_MS = 140         # how often a too-long name scrolls one pixel
DROP = 0.35           # per-frame fraction of the remaining drop distance
EASE = 0.30           # ... and of the remaining scroll distance
LIVE_S = 1.0          # no `meter` for this long = the host went away
SIMULATE = False      # invent levels with no host?  See the note above.

# The glass sets the useful frame rate, not this loop.  The ST7565R scans at
# fOSC/(4*65) -- 77 Hz typical, 92 Hz at the fast end of the part's tolerance
# (device/st7565.py has the datasheet numbers).  Anything faster than the
# fastest panel this could be cannot appear as a distinct frame, so it is pure
# current and CPU: this loop free-ran at ~360 fps, i.e. it rewrote the
# controller's RAM about five times for every frame the glass actually showed.
# Paced to the top of the tolerance so a fast part is still fed everything it
# can display.
#
# Timed in nanoseconds like everything else here (see TICK_NS below).  With the
# float clock every pacing loop tried -- single sleep, sliced, sleep-then-spin
# -- came out at 96.2 Hz instead of 92.3, which looked like a bug in the loop
# and was a 0.488 ms clock quantum on a 10.8 ms frame.
PANEL_HZ = 92.3
FRAME_NS = int(1000000000.0 / PANEL_HZ)
DRAIN_NS = 2000000    # how often to empty the host port while idling; see below

# EVERY clock in this loop is time.monotonic_ns(), and it matters.
# time.monotonic() is a float of seconds since boot, and this build uses
# SINGLE precision -- 2.0**1023 is inf and t + 1e-6 == t.  Its granularity is
# therefore the float spacing at the current uptime, which DOUBLES every time
# the uptime doubles: measured 0.488 ms at 1.5 h, so ~1 ms at 3 h, ~8 ms at a
# day, ~125 ms after a week.  On a desk device that stays plugged in, a 16 ms
# tick timed against that clock eventually stops being a tick at all.
# monotonic_ns() is an integer of nanoseconds and does not decay.
TICK_NS = TICK_MS * 1000000
MARQ_NS = MARQ_MS * 1000000
HOLD_NS = int(HOLD * 1000000000.0)
LIVE_NS = int(LIVE_S * 1000000000.0)

_ZERO_SPEC = [0.0] * 21   # no host = no spectrum, so show a flat floor

APP_MODE = 0          # index into mixer.MODES that owns the mixer screen
EQ_MODE = 1           # ... and the one that owns the EQ screen
CONFIG_MODE = 3       # ... and the one that owns the settings screen

# What a detent moves, per parameter.  Frequency and Q step in their LOG
# mapping (see eqcurve), so a detent is the same musical distance everywhere
# rather than a huge jump at the bottom and nothing at the top.
EQ_GAIN_STEP = 0.5    # dB
EQ_FREQ_STEP = 0.012  # of the 20 Hz - 20 kHz span
EQ_Q_STEP = 0.02
P_GAIN, P_FREQ, P_Q = 0, 1, 2

# ADVANCED DISPLAY -> ST7565R registers.  The labels ARE the lookup: the config
# module hands back what the row says, and these turn that into a command byte.
BIAS_CMD = {'1/9': 0xA2, '1/7': 0xA3}
BOOST_ARG = {'4X': 0x00, '5X': 0x01, '6X': 0x03}

disp = naubhw.display()
bl = naubhw.Backlight(idle.FULL)
dim = idle.Idle(bl)                               # dims and sleeps the panel
encs = naubhw.encoders()                          # ENC1-4
sws = naubhw.encoder_switches()                   # all four knob switches
btns = naubhw.buttons()                           # BTN1-5
md = media.Media()

# BTN3/4/5 -- the bottom row of keyswitches, left to right.
MEDIA = {2: media.PREV, 3: media.PLAY, 4: media.NEXT}
EQ_RESET_BTN = 2          # ... and BTN3 again, but as the EQ reset in EQ mode

mx = mixer.Mixer(ccfont.TINY)
dr = drawer.Drawer(switcher_data.ICONS, switcher_data.APPS, ccfont.TINY)
wh = wheel.Wheel()                                # what the switcher offers
eq_scr = eqscreen.EqScreen(ccfont.TINY)
eq_cur = eqcurve.Curve(nband=eqscreen.NBAND)
cfg = config.Config(ccfont.TINY)
mx_views = [disp.make_views(fb) for fb in mx.planes]
dr_views = [disp.make_views(fb) for fb in dr.planes]
eq_views = [disp.make_views(fb) for fb in eq_scr.planes]
cfg_views = [disp.make_views(fb) for fb in cfg.planes]

# LOW is a low shelf, MID a peak, HIGH a high shelf: the tone-control split,
# and what the host writes as LSC / PK / HSC.  The host overwrites all of this
# with the real state on connect (eqband), so these are only what the panel
# shows before anyone has said otherwise.
EQ_INIT = ((eqcurve.LSC, 120.0, 0.7),
           (eqcurve.PK, 1000.0, 0.9),
           (eqcurve.HSC, 4000.0, 0.7))
for _i, (_k, _f, _q) in enumerate(EQ_INIT):
    eq_cur.set(_i, _k, _f, 0.0, _q, True)
    eq_scr.handles[_i] = eq_cur.column_of(_f)
eq_param = [P_GAIN] * eqscreen.NBAND      # which parameter each knob edits
eq_dirty = True
cfg_dirty = False                         # the settings screen needs redrawing
eq_ys = None                              # cached curve; None = recompute
sys_lv = [0.0] * eqscreen.NMETER          # the system spectrum, from the host

# A mode change wipes right to left.  The mode bar is left out of it so its
# ticks can run their own animation underneath -- two movements that mean two
# different things.
tr = wipe.Wipe(p0=0, p1=6)
tr_fb = bytearray(mixer.W * 8)
# a LIST of planes, like mx_views/dr_views: the frame loop indexes views[plane]
# and hands the result to show_pages(), which wants that plane's page views
tr_views = [disp.make_views(tr_fb)]

for c in range(mixer.NCOL):
    mx.set_name(c, dr.apps[c % len(dr.apps)] if dr.apps else '')
    mx.vol[c] = 0.4 + 0.25 * c
mx.sysvol = 0.62
mx.render()

lk = link.Link()
live = False              # is a host currently feeding us levels?
t_live = 0                # ... when the last one arrived

# only used when SIMULATE: a slow envelope per column plus jitter
env = [0.65, 0.85, 0.0]
host_lv = [0.0] * mixer.NCOL

mode = 0                 # 0 = mixer, 1 = switcher dropdown
sel = 0                  # which column the dropdown is editing
reveal = 0.0             # px of panel showing; DROP eases it to rev_t
rev_t = 0.0
pos = 0.0                # fractional scroll position
target = 0.0
focus = -1
bg_dirty = True          # the mixer frame behind the panel changed
bar_moving = False       # the mode ticks are mid-wipe


def announce():
    """Say who we are AND what we are showing.  A reconnect finds the panel
    already in some mode with the drawer open or shut, and the host meters
    differently for each -- it cannot infer either, so we tell it."""
    if not lk.ready:
        return
    lk.send(link.BANNER)
    lk.send("mode %d" % mx.mode)
    lk.send("wheel %d" % (1 if mode == 1 else 0))


announce()

last = [e.position for e in encs]
was = [False] * len(sws)
press_t = [0] * len(sws)
bwas = [False] * len(btns)
t_push = t_marq = t_wipe = time.monotonic_ns()

views = mx_views
n_planes = len(views)      # mixer is binary (1 plane); the drawer is FRC (4)
i = 0                      # which plane is being shown; see set_screen()
print("naub V2 mixer | %d columns | turn=volume click=switch hold=mute | ENC4=system"
      % mixer.NCOL)
print("modes: %s | BTN1/2 flip | BTN3-5 media: %s"
      % (' > '.join(mixer.MODES), "HID ready" if md.ready else "NO HID ENDPOINT"))
print("levels: %s | data port: %s"
      % ("SIMULATED" if SIMULATE else "real (host only)",
         "ready, waiting for host" if lk.ready else
         "ABSENT - run boot.py + power cycle, then host/naub_bridge.py"))


def eq_text(i):
    """What band i's header cell shows: whichever parameter its knob is on."""
    b = eq_cur.bands[i]
    p = eq_param[i]
    if p == P_FREQ:
        return '%dHZ' % int(b.f0 + 0.5) if b.f0 < 1000 else '%.1fK' % (b.f0 / 1000.0)
    if p == P_Q:
        return 'Q%.1f' % b.q
    return '%+.1f' % b.db


def eq_refresh(curve=False):
    """Redraw the EQ screen.

    `curve` recomputes the response, which only a band change can move -- the
    meter underneath ticks at 60 Hz and must not drag 3 ms of heights() along
    with it every time, so the ys are cached."""
    global eq_dirty, eq_ys
    for i in range(eqscreen.NBAND):
        eq_scr.gains[i] = eq_cur.bands[i].db
        eq_scr.handles[i] = eq_cur.column_of(eq_cur.bands[i].f0)
        eq_scr.set_label(i, eq_text(i))
    eq_scr.draw_head()
    if not eq_scr.engine:
        eq_scr.draw_no_engine()
    else:
        if curve or eq_ys is None:
            eq_ys = eq_cur.heights(eqscreen.PLOT_H)
            eq_scr.draw_plot(eq_ys)      # rebuilds the curve layer too
        else:
            eq_scr.draw_plot()           # meter only: the curve has not moved
    sync_foot()
    eq_dirty = False


# prebuilt: the source of a slice assignment must never BE a slice, or it
# allocates a copy of the page every time (see README, the 4.6 ms lesson)
_FOOT_O = mixer.PAGE_FOOT * mixer.W
_FOOT_MV = memoryview(mx.planes[0])[_FOOT_O:_FOOT_O + mixer.W]


def sync_foot():
    """The mode bar belongs to the mixer; copy it onto the screens that borrow
    it so there is exactly one implementation of it."""
    eq_scr.planes[0][_FOOT_O:_FOOT_O + mixer.W] = _FOOT_MV
    cfg.planes[0][_FOOT_O:_FOOT_O + mixer.W] = _FOOT_MV


def set_screen(v):
    """Point the frame loop at a screen.

    `views`, `n_planes` and the plane counter `i` MUST change together: the
    loop shows views[i] and advances i until it reaches n_planes, so a screen
    swap that leaves i behind indexes past the end of the new list.  That is
    not hypothetical -- it crashed with i=4 against a 1-plane screen, which is
    the drawer's four FRC planes outliving the drawer.  Everything that changes
    screens goes through here."""
    global views, n_planes, i
    views = v
    n_planes = len(v)
    i = 0


def apply_display(key):
    """Push one CONFIG value at the hardware.

    config.py draws settings and knows nothing about registers or backlights;
    this is the only place that knows both, which is what keeps that module
    importable by the PC preview with no hardware anywhere near it."""
    if key == 'contrast':
        disp.set_contrast(cfg.value('contrast'))
    elif key == 'resistor':
        disp.cmd(0x20 | (cfg.value('resistor') & 0x07))
    elif key == 'bias':
        disp.cmd(BIAS_CMD[cfg.value('bias')])
    elif key == 'booster':
        disp.cmd(0xF8, BOOST_ARG[cfg.value('booster')])
    elif key == 'invert':
        disp.cmd(0xA7 if cfg.value('invert') else 0xA6)
    elif key == 'all_on':
        disp.cmd(0xA5 if cfg.value('all_on') else 0xA4)
    elif key == 'start_line':
        disp.cmd(0x40 | (cfg.value('start_line') & 0x3F))
    elif key == 'brightness':
        dim.set_full(cfg.value('brightness'), now)
    else:
        return False
    print("display %s -> %s" % (key, cfg.value(key)))
    return True


def sync_wheel():
    """Push the wheel's list into the drawer, keeping the scroll where it is.
    The list can change under an open drawer -- something stops playing, or a
    held entry finally expires -- and the focus has to survive that."""
    global pos, target, focus
    wh.dirty = False
    dr.set_apps(wh.names)
    # A held entry is marked: it is still offered, but nothing is coming out
    # of it, and that is worth being able to see from the console.
    print("wheel: %s" % (', '.join((n + '*') if wh.held(n) else n
                                   for n in wh.names) or '(none)'))
    if mode == 1:
        n = len(dr.apps) - 1.0
        if target > n:
            pos = target = n if n > 0 else 0.0
        focus = int(target)
        dr.set_focus(focus)
        dr.draw_shelf(pos)


def gap():
    """The name cell the dropdown hangs out of: blanked, rule and all, while
    the panel is out.  Once a pick is confirmed (rev_t back to 0) the new name
    reappears in the slot and the drawer retracts into it."""
    return (mixer.PAGE_NAME, mixer.COLX[sel], mixer.COLW) if rev_t else None


def open_drawer(c):
    global mode, sel, reveal, rev_t, pos, target, focus
    mode, sel = 1, c
    lk.send("wheel 1")            # the host meters the list faster while it is up
    k = dr.index_of(mx.names[c])
    if k < 0:
        k = 0
    pos = target = float(k)
    focus = k
    dr.set_focus(k)
    dr.draw_shelf(pos)
    reveal, rev_t = 0.0, float(drawer.H)
    set_screen(dr_views)


def close_drawer():
    global mode
    mode = 0
    lk.send("wheel 0")
    mx.render()
    set_screen(screen_views())


def screen_views(m=None):
    """Whichever screen a mode owns.  One mapping, in one place: with three
    screens the was-this/is-that pairs the wipe used to need had started to
    turn into combinatorics."""
    m = mx.mode if m is None else m
    if m == EQ_MODE:
        return eq_views
    if m == CONFIG_MODE:
        return cfg_views
    return mx_views


def screen_planes(m=None):
    """... and its frame, which is what the wipe needs."""
    m = mx.mode if m is None else m
    if m == EQ_MODE:
        return eq_scr.planes[0]
    if m == CONFIG_MODE:
        return cfg.planes[0]
    return mx.planes[0]


def is_live(now):
    """Is a host still feeding us?  A bridge that dies mid-stream would
    otherwise leave the meters frozen at whatever was playing when it went,
    which reads as a hung panel."""
    global live
    if live and now - t_live > LIVE_NS:
        live = False
        print("host quiet for %.0f s - levels back to flat" % LIVE_S)
    return live


def levels(now):
    """One amplitude per column: the host's, or nothing."""
    if is_live(now):
        return host_lv
    if not SIMULATE:
        return (0.0, 0.0, 0.0)
    for c in range(mixer.NCOL):
        e = env[c]
        if e > 0.0:                            # drift the envelope a little
            e += (random.random() - 0.5) * 0.10
            env[c] = 0.25 if e < 0.25 else (1.0 if e > 1.0 else e)
    # occasional transients, to give the peak markers something to catch
    return [0.0 if (mx.mute[c] or env[c] <= 0.0)
            else min(1.0, env[c] * (0.35 + 0.5 * random.random()
                                    + (0.6 if random.random() < 0.06 else 0.0)))
            for c in range(mixer.NCOL)]


def eq_reset():
    """Every band back to the default flat profile -- gains to 0 AND the
    shapes back to LOW/MID/HIGH, since "flat" that left a band parked at some
    frequency you no longer remember is not much of a reset."""
    global eq_ys
    for i, (k, f, q) in enumerate(EQ_INIT):
        eq_cur.set(i, k, f, 0.0, q, True)
        eq_scr.handles[i] = eq_cur.column_of(f)
        eq_param[i] = P_GAIN
    eq_ys = None
    lk.send("eqflat")
    print("eq reset to flat")


def flip_mode(d):
    """Flip through the mode list.  The ticks wipe across to their new side and
    push the label along with them; bar_moving keeps stepping that until it
    settles."""
    global bar_moving, eq_ys
    was = mx.mode
    bar_moving = mx.set_mode(mx.mode + d)
    lk.send("mode %d" % mx.mode)
    print("mode %d %s" % (mx.mode, mixer.MODES[mx.mode]))
    if mx.mode == EQ_MODE:
        eq_ys = None
        eq_refresh(True)
    elif mx.mode == CONFIG_MODE:
        cfg.render()
        sync_foot()
    elif was == EQ_MODE or was == CONFIG_MODE:
        mx.render()
    # snapshot what is on screen now, then wipe to what the new mode draws
    # The wipe follows the way you moved through the list: forward sweeps
    # right to left, back sweeps left to right, so stepping back to APP CTRL
    # visibly undoes the step that left it.
    tr.start(screen_planes(was), screen_planes(),
             wipe.RIGHT_TO_LEFT if d > 0 else wipe.LEFT_TO_RIGHT)
    set_screen(tr_views)


frames = 0
guard_hit = False
pending = []              # host lines drained while idling, parsed next frame
t0 = time.monotonic_ns()
t_next = time.monotonic_ns()   # when the next plane is due on screen
while True:
    # Paced per PUSH rather than per cycle.  With FRC grayscale the grays ARE
    # the share of time each plane spends on screen, so idling at the end of a
    # cycle -- holding the last plane through the gap -- would tint them.  One
    # plane, one slot: 92 Hz for a binary screen, 4 x 92 for the drawer.
    slot = FRAME_NS // n_planes
    while True:
        # Idle in slices, emptying the host port between them.  MEASURED on
        # this board: the USB CDC receive ring holds ~200 bytes, and the
        # host's resync burst (labels + levels + mutes + the whole EQ state)
        # fills it -- at one read per frame the backlog peaked at exactly 200
        # on connect, against 25 bytes in steady state.  Free-running at
        # 360 fps the port was read every 2.8 ms and it never came up.
        # Reading every 2 ms keeps that cover regardless of the frame rate.
        # The LINES can wait for the frame's parse; the BYTES should not.
        d = t_next - time.monotonic_ns()
        if d <= 0:
            break
        time.sleep((DRAIN_NS if d > DRAIN_NS else d) * 1e-9)
        if lk.ready and lk.port.in_waiting:
            pending.extend(lk.lines())
    t_next += slot
    tn = time.monotonic_ns()
    if tn - t_next > slot:    # a whole slot behind (a long redraw): resync the
        t_next = tn           # schedule rather than sprint to catch up
    if i >= n_planes:
        # set_screen() keeps this from happening, and a 9700-transition soak
        # never tripped it -- but the observed failure was exactly this state
        # (i=4 against a 1-plane screen) and its symptom is the whole panel
        # going dark, so it is worth a comparison per frame.  Reported once:
        # if it ever fires, the invariant has a hole worth finding.
        if not guard_hit:
            guard_hit = True
            print("PLANE GUARD: i=%d n_planes=%d len(views)=%d mode=%d "
                  "mx.mode=%d -- please report this line"
                  % (i, n_planes, len(views), mode, mx.mode))
        i = 0
    disp.show_pages(views[i])
    i += 1
    frames += 1
    if i < n_planes:
        continue
    i = 0                                  # end of a gray cycle: safe to redraw
    now = time.monotonic_ns()
    dim.step(now)

    # ---- host ----
    msgs = lk.lines()
    if pending:
        pending.extend(msgs)      # what was drained while idling comes first
        msgs = pending
        pending = []
    for msg in msgs:
        t = msg.split()
        if not t:
            continue
        v = t[0]
        if v == 'meter' and len(t) >= 1 + mixer.NCOL:
            for c in range(mixer.NCOL):
                host_lv[c] = int(t[1 + c]) * 0.01
            t_live = now
            if not live:
                live = True
                print("host connected: live audio")
        elif v == 'ameter':                       # one level per LIVE app
            wh.feed([int(x) * 0.01 for x in t[1:]], now)
        elif v == 'apps':                         # apps with a live session
            wh.set_live([a for a in ' '.join(t[1:]).split(',') if a], now)
        elif v == 'label' and len(t) >= 2:
            mx.set_name(int(t[1]), ' '.join(t[2:]))
            mx.draw_names()
            bg_dirty = True
        elif v == 'level' and len(t) == 3:
            ch = int(t[1])
            if ch == mixer.NCOL:
                mx.sysvol = int(t[2]) * 0.01
                mx.draw_foot()
            elif ch < mixer.NCOL:
                mx.vol[ch] = int(t[2]) * 0.01
                mx.draw_vol()
            bg_dirty = True
        elif v == 'mute' and len(t) == 3:
            ch = int(t[1])
            if ch == mixer.NCOL:
                mx.sysmute = t[2] == '1'
                mx.draw_foot()
            elif ch < mixer.NCOL:
                mx.mute[ch] = t[2] == '1'
                mx.draw_vol()
            bg_dirty = True
        elif v == 'eqcaps' and len(t) >= 7:
            # nbands fmin fmax maxdb sr ok.  ok=0 is a normal state, not an
            # error: Equalizer APO attaches per endpoint, so it can be
            # installed and still inert on the current output.
            eq_cur.set_rate(float(t[5]))
            eq_scr.engine = t[6] == '1'
            eq_ys = None
            eq_dirty = True
            print("eq engine: %s" % ("ready" if eq_scr.engine else "INERT"))
        elif v == 'smeter':
            sys_lv = [int(x) * 0.01 for x in t[1:]]
        elif v == 'eqband' and len(t) == 7:
            i = int(t[1])
            if 0 <= i < eqscreen.NBAND:
                eq_cur.set(i, t[2], float(t[3]), float(t[4]), float(t[5]),
                           t[6] == '1')
                eq_ys = None
                eq_dirty = True
        elif v == 'ping':
            lk.send('pong')
        elif v == 'hello':
            announce()

    # ---- knobs ----
    for c in range(mixer.NCOL):
        p = encs[c].position
        d = p - last[c]
        if not d:
            continue
        last[c] = p
        dim.poke(now)
        if mode == 1 and c == sel:
            n = len(dr.apps) - 1.0
            t = target + d
            target = 0.0 if (t < 0 or n < 0) else (n if t > n else float(t))
        elif mx.mode == CONFIG_MODE:
            # The three jobs are labelled across the top of the screen, which
            # is the affordance: turn = it happens, no arming, no commit.
            if c == 0:
                cfg_dirty = cfg.nav(d) or cfg_dirty
            elif c == 1:
                cfg_dirty = cfg.scroll(d) or cfg_dirty
            elif cfg.adjust(d):
                cfg_dirty = True
                apply_display(cfg.focused_key())
        elif mx.mode == EQ_MODE:
            b = eq_cur.bands[c]
            p = eq_param[c]
            if p == P_GAIN:
                g = max(-eqcurve.MAXDB,
                        min(eqcurve.MAXDB, b.db + d * EQ_GAIN_STEP))
                eq_cur.set(c, db=g)
                eq_ys = None
                lk.send("eqp %d gain %.2f" % (c, g))
            elif p == P_FREQ:
                f = eqcurve.pos_to_freq(
                    eqcurve.freq_to_pos(b.f0) + d * EQ_FREQ_STEP)
                eq_cur.set(c, f0=f)
                eq_ys = None
                lk.send("eqp %d freq %.4g" % (c, f))
            else:
                q = eqcurve.pos_to_q(
                    eqcurve.q_to_pos(b.q) + d * EQ_Q_STEP)
                eq_cur.set(c, q=q)
                eq_ys = None
                lk.send("eqp %d q %.3g" % (c, q))
            eq_dirty = True
        elif mode == 0:
            v = mx.vol[c] + d * 0.04
            mx.vol[c] = 0.0 if v < 0 else (1.0 if v > 1.0 else v)
            mx.draw_vol()
            lk.send("vol %d %d" % (c, int(mx.vol[c] * 100 + 0.5)))
    p = encs[3].position
    if p != last[3]:
        v = mx.sysvol + (p - last[3]) * 0.04
        last[3] = p
        dim.poke(now)
        mx.sysvol = 0.0 if v < 0 else (1.0 if v > 1.0 else v)
        lk.send("vol %d %d" % (mixer.NCOL, int(mx.sysvol * 100 + 0.5)))
        mx.draw_foot()
        sync_foot()
        bg_dirty = True

    # ---- knob switches: ENC4 = system mute, ENC1-3 = pick / mute ----
    for c in range(len(sws)):
        down = not sws[c].value
        if down != was[c]:
            dim.poke(now)
        if down and not was[c]:
            press_t[c] = now
        elif not down and was[c]:
            if c == mixer.NCOL:                   # ENC4: the system channel
                mx.sysmute = not mx.sysmute
                mx.draw_foot()
                lk.send("mute %d %d" % (mixer.NCOL, 1 if mx.sysmute else 0))
                bg_dirty = True
                print("system mute %d" % mx.sysmute)
            elif now - press_t[c] >= HOLD_NS:
                if mode == 0:
                    mx.mute[c] = not mx.mute[c]
                    mx.draw_vol()
                    lk.send("mute %d %d" % (c, 1 if mx.mute[c] else 0))
                    print("mute %d %d" % (c, mx.mute[c]))
            elif mx.mode == CONFIG_MODE:
                # ENC3 click enters a submenu or flips a toggle; ENC1 click is
                # the way back out, which is also what turning it does.
                if c == 2:
                    key = cfg.focused_key()       # before the click moves it
                    if cfg.click():
                        cfg_dirty = True
                        apply_display(key)
                elif c == 0 and cfg.back():
                    cfg_dirty = True
            elif mx.mode == EQ_MODE:              # cycle gain -> freq -> Q
                eq_param[c] = (eq_param[c] + 1) % 3
                eq_dirty = True
                print("eq band %d edits %s"
                      % (c, ('gain', 'freq', 'q')[eq_param[c]]))
            elif mode == 0:                       # open the dropdown
                if mx.mode == APP_MODE:           # ... only in its own mode
                    open_drawer(c)
                    bg_dirty = True
            elif c == sel and rev_t:              # confirm and slide back up
                if dr.apps:                       # nothing playing = nothing
                    nm = dr.apps[int(target)]     # to assign, but still close
                    # One app, one column.  Taking an app that another column
                    # already holds SWAPS them rather than showing it twice --
                    # two columns driving one fader would fight each other, and
                    # the second one would look broken.  The host does the same
                    # to its pins; this is so the panel is right immediately.
                    prev = mx.names[c]
                    for j in range(mixer.NCOL):
                        if j != c and mx.names[j] == nm:
                            mx.set_name(j, prev)
                            print("swap col %d <- %s" % (j, prev or '(none)'))
                    mx.set_name(c, nm)
                    mx.draw_names()
                    lk.send("pick %d %s" % (c, nm))
                    print("assign col %d -> %s" % (c, nm))
                rev_t = 0.0
                bg_dirty = True
        was[c] = down

    # ---- aux keys: BTN1/2 flip modes, BTN3-5 are media transport ----
    # These act on PRESS, not release: a transport key that waits for the
    # release feels laggy in a way a mute toggle does not.
    for b in range(len(btns)):
        down = not btns[b].value
        if down != bwas[b]:
            dim.poke(now)
        if down and not bwas[b]:
            if b == 0:
                flip_mode(-1)
                bg_dirty = True
            elif b == 1:
                flip_mode(1)
                bg_dirty = True
            elif mx.mode == EQ_MODE:
                # No transport here: the bottom-left key resets the EQ, and
                # the other two are deliberately unmapped rather than doing
                # something surprising with your hands on the EQ knobs.
                if b == EQ_RESET_BTN:
                    eq_reset()
                    eq_dirty = True
            else:
                md.tap(MEDIA[b])
                lk.send("btn %d 1" % b)
        elif (not down) and bwas[b] and b in MEDIA and mx.mode != EQ_MODE:
            lk.send("btn %d 0" % b)
        bwas[b] = down

    # ---- animate ----
    if mode == 1:
        d = rev_t - reveal
        if d > 0.5 or d < -0.5:
            reveal += d * DROP
        elif reveal != rev_t:
            reveal = rev_t
        d = target - pos
        if d > 0.02 or d < -0.02:
            pos += d * EASE
            dr.draw_shelf(pos)
        elif pos != target:
            pos = target
            dr.draw_shelf(pos)
        f = int(target)
        if f != focus and -0.6 < target - pos < 0.6:
            focus = f
            dr.set_focus(focus)

        if now - t_push >= TICK_NS:
            t_push = now
            if bar_moving:
                bar_moving = mx.step_foot()
                bg_dirty = True
            wh.tick(now)
            if wh.dirty:
                sync_wheel()
            if is_live(now):
                v = wh.level(focus)
            elif SIMULATE:
                v = min(1.0, 0.35 + 0.5 * random.random()
                        + (0.6 if random.random() < 0.06 else 0.0))
            else:
                v = 0.0
            dr.update(v)
            dr.draw_row()
            if reveal < drawer.H:      # meters still visible under the panel
                mx.update(levels(now))
                mx.draw_dots()
                bg_dirty = True

        if bg_dirty or reveal < drawer.H:
            dr.set_background(mx.planes[0], gap())
            bg_dirty = False
        dr.compose(int(reveal))
        if reveal == 0.0 and rev_t == 0.0:
            close_drawer()
    elif mx.mode == CONFIG_MODE:
        # Nothing here is on a tick: a settings list changes when a knob does
        # and not otherwise, so between events the loop pushes the same bytes.
        if bar_moving:
            bar_moving = mx.step_foot()
            sync_foot()
        if cfg_dirty:
            cfg.render()
            sync_foot()
            cfg_dirty = False
    elif mx.mode == EQ_MODE:
        if bar_moving:
            bar_moving = mx.step_foot()
            sync_foot()
        if now - t_push >= TICK_NS:
            t_push = now
            # Cheap, and the mixer redraws the bar from half a dozen places
            # (system volume, mute, the host) without knowing this screen is
            # borrowing it.  One 128-byte copy is not worth chasing them all.
            sync_foot()
            if eq_scr.engine:
                # ONLY the meter moves on a tick.  Going through eq_refresh()
                # redrew the header too -- 3.3 ms out of every 16 spent
                # rebuilding three labels that had not changed, which is where
                # a third of the frame rate in this mode was going.
                eq_scr.update(sys_lv if live else _ZERO_SPEC)
                eq_scr.draw_plot()
        if eq_dirty:
            eq_refresh()
    else:
        if now - t_push >= TICK_NS:
            t_push = now
            wh.tick(now)              # entries expire whether or not it is open
            if wh.dirty:
                sync_wheel()
            if bar_moving:
                bar_moving = mx.step_foot()
            mx.update(levels(now))
            mx.draw_dots()
        if now - t_marq >= MARQ_NS:
            t_marq = now
            if mx.marquee():
                mx.draw_names()

    # ---- the mode wipe -------------------------------------------------
    # Last, so the screens underneath have already been updated this frame:
    # the incoming half of the wipe is live, not a still.  Stepped on the same
    # tick as everything else, or at 380 fps it would be over in 37 ms.
    if tr.active and now - t_wipe >= TICK_NS:
        t_wipe = now
        o = mixer.PAGE_FOOT * mixer.W          # the mode bar is not wiped
        tr_fb[o:o + mixer.W] = mx.planes[0][o:o + mixer.W]
        if not tr.step(tr_fb):
            set_screen(screen_views())

    if frames >= 1024:
        t1 = time.monotonic_ns()
        fps = frames * 1000000000.0 / (t1 - t0)
        print("%.1f fps (%.1f Hz cycle) %s | %s vol=%s sys=%.2f bl=%.0f%%" %
              (fps, fps / n_planes,
               "switcher" if mode else "mixer", mixer.MODES[mx.mode],
               [round(v, 2) for v in mx.vol], mx.sysvol, dim.pct))
        frames = 0
        t0 = t1
