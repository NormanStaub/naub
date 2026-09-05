# Demo: the naub V2 mixer screen, with the app-switcher dropdown wired in as
# the column picker.
#
# Runs either way:
#   * with host/naub_bridge.py talking to it over the USB CDC data port, the
#     levels, names, volumes and the switcher's app list are this PC's REAL
#     audio;
#   * with nothing attached (or before boot.py + a power cycle enables the data
#     port), it simulates levels so the interaction is still judgeable.
#
#   top-left / top-middle / top-right knobs (ENC1-3), one per column:
#       turn        set that app's volume
#       click       open the app switcher for that column; click again to assign
#       hold 0.6 s  mute / unmute that column
#   lower-right knob (ENC4): system volume
#
# The mixer screen is binary (one plane, ~390 Hz); the switcher's icons are FRC
# grayscale, so the frame cycles four ways while the drawer is open.  n_planes
# follows whichever is on screen.
#
# Deploy: python host/deploy.py mixer
import random
import time
import naubhw, ccfont, drawer, link, mixer, switcher_data

HOLD = 0.6            # seconds held = mute rather than open the picker
TICK_MS = 16          # meter update rate; the FALL constants are tuned to it
MARQ_MS = 140         # how often a too-long name scrolls one pixel
DROP = 0.35           # per-frame fraction of the remaining drop distance
EASE = 0.30           # ... and of the remaining scroll distance

disp = naubhw.display()
bl = naubhw.Backlight(80)
encs = naubhw.encoders()                       # ENC1-4
sws = naubhw.encoder_switches((0, 1, 2))       # the three column knobs

mx = mixer.Mixer(ccfont.TINY)
dr = drawer.Drawer(switcher_data.ICONS, switcher_data.APPS,
                   ccfont.TINY, ccfont.SML)
mx_views = [disp.make_views(fb) for fb in mx.planes]
dr_views = [disp.make_views(fb) for fb in dr.planes]

for c in range(mixer.NCOL):
    mx.set_name(c, dr.apps[c % len(dr.apps)] if dr.apps else '')
    mx.vol[c] = 0.4 + 0.25 * c
mx.sysvol = 0.62
mx.render()

lk = link.Link()
live = False              # flips true as soon as the host says anything
if lk.ready:
    lk.send(link.BANNER)

# fallback when no host is driving us: a slow envelope per column plus jitter
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

last = [e.position for e in encs]
was = [False] * mixer.NCOL
press_t = [0.0] * mixer.NCOL
t_push = t_marq = time.monotonic()

views = mx_views
n_planes = len(views)      # mixer is binary (1 plane); the drawer is FRC (4)
print("naub V2 mixer | %d columns | turn=volume click=switch hold=mute | ENC4=system"
      % mixer.NCOL)
print("data port: %s" % ("ready, waiting for host" if lk.ready else
                         "ABSENT - simulating (deploy boot.py + power cycle)"))


def gap():
    """The name cell the dropdown hangs out of: blanked, rule and all, while
    the panel is out.  Once a pick is confirmed (rev_t back to 0) the new name
    reappears in the slot and the drawer retracts into it."""
    return (mixer.PAGE_NAME, mixer.COLX[sel], mixer.COLW) if rev_t else None


def open_drawer(c):
    global mode, sel, reveal, rev_t, pos, target, focus, views, n_planes
    mode, sel = 1, c
    mx.preset = 'APP CTRL'
    mx.draw_foot()
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
    mx.preset = 'PRESET 1'
    mx.render()
    views = mx_views
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
            mx.mute[int(t[1])] = t[2] == '1'
            mx.draw_vol()
            bg_dirty = True
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
        bg_dirty = True

    # ---- switches: click = switch apps, hold = mute ----
    for c in range(mixer.NCOL):
        down = not sws[c].value
        if down and not was[c]:
            press_t[c] = now
        elif not down and was[c]:
            if now - press_t[c] >= HOLD:
                if mode == 0:
                    mx.mute[c] = not mx.mute[c]
                    mx.draw_vol()
                    lk.send("mute %d %d" % (c, 1 if mx.mute[c] else 0))
                    print("mute %d %d" % (c, mx.mute[c]))
            elif mode == 0:                       # open the dropdown
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
            if live:
                v = app_lv[focus] if 0 <= focus < len(app_lv) else 0.0
            else:
                v = min(1.0, 0.35 + 0.5 * random.random()
                        + (0.6 if random.random() < 0.06 else 0.0))
            dr.update(v)
            dr.draw_row()
            if reveal < drawer.H:      # meters still visible under the panel
                mx.update(host_lv if live else
                          [env[c] * random.random() for c in range(mixer.NCOL)])
                mx.draw_dots()
                bg_dirty = True

        if bg_dirty or reveal < drawer.H:
            dr.set_background(mx.planes[0], gap())
            bg_dirty = False
        dr.compose(int(reveal))
        if reveal == 0.0 and rev_t == 0.0:
            close_drawer()
    else:
        if (now - t_push) * 1000 >= TICK_MS:
            t_push = now
            if live:
                mx.update(host_lv)
            else:
                for c in range(mixer.NCOL):
                    e = env[c]
                    if e > 0.0:                    # drift the envelope a little
                        e += (random.random() - 0.5) * 0.10
                        env[c] = 0.25 if e < 0.25 else (1.0 if e > 1.0 else e)
                # occasional transients, to give the peak markers something
                mx.update([0.0 if (mx.mute[c] or env[c] <= 0.0)
                           else min(1.0, env[c] * (0.35 + 0.5 * random.random()
                                                   + (0.6 if random.random() < 0.06
                                                      else 0.0)))
                           for c in range(mixer.NCOL)])
            mx.draw_dots()
        if (now - t_marq) * 1000 >= MARQ_MS:
            t_marq = now
            if mx.marquee():
                mx.draw_names()

    if frames >= 1024:
        t1 = time.monotonic()
        print("%.1f fps (%.1f Hz per gray cycle) mode=%s vol=%s sys=%.2f" %
              (frames / (t1 - t0), frames / (t1 - t0) / n_planes,
               "switcher" if mode else "mixer",
               [round(v, 2) for v in mx.vol], mx.sysvol))
        frames = 0
        t0 = t1
