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
#       host software running at all (see device/media.py)
#
# Only APP CTRL is implemented; flipping to EQ / CUSTOM 1 / CUSTOM 2 moves the
# mode bar's ticks and label, and those screens land later.  The switcher opens
# only in APP CTRL, since that is the mode it belongs to.
#
# The mixer screen is binary (one plane, ~400 Hz); the switcher's icons are FRC
# grayscale, so the frame cycles four ways while the drawer is open.  n_planes
# follows whichever is on screen.
#
# Deploy: python host/deploy.py mixer
import random
import time
import naubhw, ccfont, drawer, eqcurve, eqscreen, link, media, mixer
import switcher_data

HOLD = 0.6            # seconds held = mute rather than open the picker
TICK_MS = 16          # meter update rate; the FALL constants are tuned to it
MARQ_MS = 140         # how often a too-long name scrolls one pixel
DROP = 0.35           # per-frame fraction of the remaining drop distance
EASE = 0.30           # ... and of the remaining scroll distance
LIVE_S = 1.0          # no `meter` for this long = the host went away
SIMULATE = False      # invent levels with no host?  See the note above.

APP_MODE = 0          # index into mixer.MODES that owns the mixer screen
EQ_MODE = 1           # ... and the one that owns the EQ screen

# What a detent moves, per parameter.  Frequency and Q step in their LOG
# mapping (see eqcurve), so a detent is the same musical distance everywhere
# rather than a huge jump at the bottom and nothing at the top.
EQ_GAIN_STEP = 0.5    # dB
EQ_FREQ_STEP = 0.012  # of the 20 Hz - 20 kHz span
EQ_Q_STEP = 0.02
P_GAIN, P_FREQ, P_Q = 0, 1, 2

disp = naubhw.display()
bl = naubhw.Backlight(80)
encs = naubhw.encoders()                          # ENC1-4
sws = naubhw.encoder_switches()                   # all four knob switches
btns = naubhw.buttons()                           # BTN1-5
md = media.Media()

# BTN3/4/5 -- the bottom row of keyswitches, left to right.
MEDIA = {2: media.PREV, 3: media.PLAY, 4: media.NEXT}

mx = mixer.Mixer(ccfont.TINY)
dr = drawer.Drawer(switcher_data.ICONS, switcher_data.APPS, ccfont.TINY)
eq_scr = eqscreen.EqScreen(ccfont.TINY)
eq_cur = eqcurve.Curve(nband=eqscreen.NBAND)
mx_views = [disp.make_views(fb) for fb in mx.planes]
dr_views = [disp.make_views(fb) for fb in dr.planes]
eq_views = [disp.make_views(fb) for fb in eq_scr.planes]

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

for c in range(mixer.NCOL):
    mx.set_name(c, dr.apps[c % len(dr.apps)] if dr.apps else '')
    mx.vol[c] = 0.4 + 0.25 * c
mx.sysvol = 0.62
mx.render()

lk = link.Link()
live = False              # is a host currently feeding us levels?
t_live = 0.0              # ... when the last one arrived
if lk.ready:
    lk.send(link.BANNER)

# only used when SIMULATE: a slow envelope per column plus jitter
env = [0.65, 0.85, 0.0]
host_lv = [0.0] * mixer.NCOL
app_lv = [0.0] * len(dr.apps)          # one per entry in the switcher list

mode = 0                 # 0 = mixer, 1 = switcher dropdown
sel = 0                  # which column the dropdown is editing
reveal = 0.0             # px of panel showing; DROP eases it to rev_t
rev_t = 0.0
pos = 0.0                # fractional scroll position
target = 0.0
focus = -1
bg_dirty = True          # the mixer frame behind the panel changed
bar_moving = False       # the mode ticks are mid-wipe

last = [e.position for e in encs]
was = [False] * len(sws)
press_t = [0.0] * len(sws)
bwas = [False] * len(btns)
t_push = t_marq = time.monotonic()

views = mx_views
n_planes = len(views)      # mixer is binary (1 plane); the drawer is FRC (4)
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


def eq_refresh(full=True):
    """Redraw the EQ screen.  The curve is the expensive part (~13 ms for a
    band change), so the header alone can be redrawn without it."""
    global eq_dirty
    for i in range(eqscreen.NBAND):
        eq_scr.gains[i] = eq_cur.bands[i].db
        eq_scr.handles[i] = eq_cur.column_of(eq_cur.bands[i].f0)
        eq_scr.set_label(i, eq_text(i))
    eq_scr.draw_head()
    if full:
        if eq_scr.engine:
            eq_scr.draw_plot(eq_cur.heights(eqscreen.PLOT_H))
        else:
            eq_scr.draw_no_engine()
    sync_foot()
    eq_dirty = False


def sync_foot():
    """The mode bar belongs to the mixer; copy it onto the EQ screen so there
    is exactly one implementation of it."""
    o = mixer.PAGE_FOOT * 128
    eq_scr.planes[0][o:o + 128] = mx.planes[0][o:o + 128]


def gap():
    """The name cell the dropdown hangs out of: blanked, rule and all, while
    the panel is out.  Once a pick is confirmed (rev_t back to 0) the new name
    reappears in the slot and the drawer retracts into it."""
    return (mixer.PAGE_NAME, mixer.COLX[sel], mixer.COLW) if rev_t else None


def open_drawer(c):
    global mode, sel, reveal, rev_t, pos, target, focus, views, n_planes
    mode, sel = 1, c
    k = dr.index_of(mx.names[c])
    if k < 0:
        k = 0
    pos = target = float(k)
    focus = k
    dr.set_focus(k)
    dr.draw_shelf(pos)
    reveal, rev_t = 0.0, float(drawer.H)
    views = dr_views
    n_planes = len(views)


def close_drawer():
    global mode, views, n_planes
    mode = 0
    mx.render()
    views = screen_views()
    n_planes = len(views)


def screen_views():
    """Whichever screen the current mode owns."""
    return eq_views if mx.mode == EQ_MODE else mx_views


def is_live(now):
    """Is a host still feeding us?  A bridge that dies mid-stream would
    otherwise leave the meters frozen at whatever was playing when it went,
    which reads as a hung panel."""
    global live
    if live and now - t_live > LIVE_S:
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


def flip_mode(d):
    """Flip through the mode list.  The ticks wipe across to their new side and
    push the label along with them; bar_moving keeps stepping that until it
    settles."""
    global bar_moving, views, n_planes
    was_eq = mx.mode == EQ_MODE
    bar_moving = mx.set_mode(mx.mode + d)
    lk.send("mode %d" % mx.mode)
    print("mode %d %s" % (mx.mode, mixer.MODES[mx.mode]))
    if mx.mode == EQ_MODE:
        eq_refresh()
    elif was_eq:
        mx.render()
    views = screen_views()
    n_planes = len(views)


i = 0
frames = 0
t0 = time.monotonic()
while True:
    disp.show_pages(views[i])
    i += 1
    frames += 1
    if i < n_planes:
        continue
    i = 0                                  # end of a gray cycle: safe to redraw
    now = time.monotonic()

    # ---- host ----
    for msg in lk.lines():
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
        elif v == 'ameter':                       # one level per switcher entry
            app_lv = [int(x) * 0.01 for x in t[1:]]
        elif v == 'apps':                         # apps with a live session
            dr.set_apps(' '.join(t[1:]).split(','))
            app_lv = [0.0] * len(dr.apps)
            if mode == 1:
                n = len(dr.apps) - 1.0
                if target > n:
                    pos = target = n if n > 0 else 0.0
                focus = int(target)
                dr.set_focus(focus)
                dr.draw_shelf(pos)
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
            eq_dirty = True
            print("eq engine: %s" % ("ready" if eq_scr.engine else "INERT"))
        elif v == 'eqband' and len(t) == 7:
            i = int(t[1])
            if 0 <= i < eqscreen.NBAND:
                eq_cur.set(i, t[2], float(t[3]), float(t[4]), float(t[5]),
                           t[6] == '1')
                eq_dirty = True
        elif v == 'ping':
            lk.send('pong')
        elif v == 'hello':
            lk.send(link.BANNER)

    # ---- knobs ----
    for c in range(mixer.NCOL):
        p = encs[c].position
        d = p - last[c]
        if not d:
            continue
        last[c] = p
        if mode == 1 and c == sel:
            n = len(dr.apps) - 1.0
            t = target + d
            target = 0.0 if (t < 0 or n < 0) else (n if t > n else float(t))
        elif mx.mode == EQ_MODE:
            b = eq_cur.bands[c]
            p = eq_param[c]
            if p == P_GAIN:
                g = max(-eqcurve.MAXDB,
                        min(eqcurve.MAXDB, b.db + d * EQ_GAIN_STEP))
                eq_cur.set(c, db=g)
                lk.send("eqp %d gain %.2f" % (c, g))
            elif p == P_FREQ:
                f = eqcurve.pos_to_freq(
                    eqcurve.freq_to_pos(b.f0) + d * EQ_FREQ_STEP)
                eq_cur.set(c, f0=f)
                lk.send("eqp %d freq %.4g" % (c, f))
            else:
                q = eqcurve.pos_to_q(
                    eqcurve.q_to_pos(b.q) + d * EQ_Q_STEP)
                eq_cur.set(c, q=q)
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
        mx.sysvol = 0.0 if v < 0 else (1.0 if v > 1.0 else v)
        lk.send("vol %d %d" % (mixer.NCOL, int(mx.sysvol * 100 + 0.5)))
        mx.draw_foot()
        sync_foot()
        bg_dirty = True

    # ---- knob switches: ENC4 = system mute, ENC1-3 = pick / mute ----
    for c in range(len(sws)):
        down = not sws[c].value
        if down and not was[c]:
            press_t[c] = now
        elif not down and was[c]:
            if c == mixer.NCOL:                   # ENC4: the system channel
                mx.sysmute = not mx.sysmute
                mx.draw_foot()
                lk.send("mute %d %d" % (mixer.NCOL, 1 if mx.sysmute else 0))
                bg_dirty = True
                print("system mute %d" % mx.sysmute)
            elif now - press_t[c] >= HOLD:
                if mode == 0:
                    mx.mute[c] = not mx.mute[c]
                    mx.draw_vol()
                    lk.send("mute %d %d" % (c, 1 if mx.mute[c] else 0))
                    print("mute %d %d" % (c, mx.mute[c]))
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
        if down and not bwas[b]:
            if b == 0:
                flip_mode(-1)
                bg_dirty = True
            elif b == 1:
                flip_mode(1)
                bg_dirty = True
            else:
                md.tap(MEDIA[b])
                lk.send("btn %d 1" % b)
        elif (not down) and bwas[b] and b in MEDIA:
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

        if (now - t_push) * 1000 >= TICK_MS:
            t_push = now
            if bar_moving:
                bar_moving = mx.step_foot()
                bg_dirty = True
            if is_live(now):
                v = app_lv[focus] if 0 <= focus < len(app_lv) else 0.0
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
    elif mx.mode == EQ_MODE:
        if bar_moving:
            bar_moving = mx.step_foot()
            sync_foot()
        if eq_dirty:
            eq_refresh()
    else:
        if (now - t_push) * 1000 >= TICK_MS:
            t_push = now
            if bar_moving:
                bar_moving = mx.step_foot()
            mx.update(levels(now))
            mx.draw_dots()
        if (now - t_marq) * 1000 >= MARQ_MS:
            t_marq = now
            if mx.marquee():
                mx.draw_names()

    if frames >= 1024:
        t1 = time.monotonic()
        print("%.1f fps (%.1f Hz cycle) %s | %s vol=%s sys=%.2f" %
              (frames / (t1 - t0), frames / (t1 - t0) / n_planes,
               "switcher" if mode else "mixer", mixer.MODES[mx.mode],
               [round(v, 2) for v in mx.vol], mx.sysvol))
        frames = 0
        t0 = t1
