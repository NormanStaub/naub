# Demo: the naub V2 mixer screen, with the carousel wired in as the column
# picker.
#
# Runs either way:
#   * with host/naub_bridge.py talking to it over the USB CDC data port, the
#     levels, names and volumes are this PC's REAL audio;
#   * with nothing attached (or before boot.py + a power cycle enables the data
#     port), it simulates levels so the interaction is still judgeable.
#
#   top-left / top-middle / top-right knobs (ENC1-3), one per column:
#       turn        set that app's volume
#       click       open the app carousel for that column; click again to assign
#       hold 0.6 s  mute / unmute that column
#   lower-right knob (ENC4): system volume
#
# Deploy: python host/deploy.py mixer
import random
import time
import naubhw, ccfont, carousel, link, mixer, switcher_data

HOLD = 0.6            # seconds held = mute rather than open the picker
TICK_MS = 16          # meter update rate; mixer.FALL/PEAK_FALL are tuned to it
MARQ_MS = 140         # how often a too-long name scrolls one pixel

disp = naubhw.display()
bl = naubhw.Backlight(80)
encs = naubhw.encoders()                       # ENC1-4
sws = naubhw.encoder_switches((0, 1, 2))       # the three column knobs

mx = mixer.Mixer(ccfont.TINY)
car = carousel.Carousel(switcher_data.ICONS, switcher_data.APPS)
mx_views = [disp.make_views(fb) for fb in mx.planes]
car_views = [disp.make_views(fb) for fb in car.planes]

n_apps = len(switcher_data.APPS)
col_app = [i % n_apps for i in range(mixer.NCOL)]   # which app each column shows
for c in range(mixer.NCOL):
    mx.set_name(c, switcher_data.APPS[col_app[c]])
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

mode = 0                 # 0 = mixer, 1 = carousel
sel = 0                  # which column the carousel is editing
pos = 0.0
target = 0.0
focus = -1

last = [e.position for e in encs]
was = [False] * mixer.NCOL
press_t = [0.0] * mixer.NCOL
t_push = t_marq = time.monotonic()

views = mx_views
n_planes = len(views)      # mixer is binary (1 plane); the carousel is FRC (4)
print("naub V2 mixer | %d columns | turn=volume click=pick hold=mute | ENC4=system"
      % mixer.NCOL)
print("data port: %s" % ("ready, waiting for host" if lk.ready else
                         "ABSENT - simulating (deploy boot.py + power cycle)"))

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
        elif v == 'label' and len(t) >= 2:
            mx.set_name(int(t[1]), ' '.join(t[2:]))
            if mode == 0:
                mx.draw_names()
        elif v == 'level' and len(t) == 3:
            ch = int(t[1])
            if ch == mixer.NCOL:
                mx.sysvol = int(t[2]) * 0.01
                if mode == 0:
                    mx.draw_foot()
            elif ch < mixer.NCOL:
                mx.vol[ch] = int(t[2]) * 0.01
                if mode == 0:
                    mx.draw_vol()
        elif v == 'mute' and len(t) == 3:
            mx.mute[int(t[1])] = t[2] == '1'
            if mode == 0:
                mx.draw_vol()
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
            t = target + d
            target = 0.0 if t < 0 else (n_apps - 1.0 if t > n_apps - 1 else float(t))
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
        if mode == 0:
            mx.draw_foot()

    # ---- switches: click = pick, hold = mute ----
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
            elif mode == 0:                       # open the picker
                mode, sel = 1, c
                pos = target = float(col_app[c])
                focus = -1
                views = car_views
                n_planes = len(views)
                i = 0
                car.render(pos)
            elif c == sel:                        # confirm the pick
                col_app[c] = int(target)
                mx.set_name(c, switcher_data.APPS[col_app[c]])
                mx.render()
                mode = 0
                views = mx_views
                n_planes = len(views)
                i = 0
                print("assign col %d -> %s" % (c, switcher_data.APPS[col_app[c]]))
        was[c] = down

    # ---- animate ----
    if mode == 1:
        d = target - pos
        if d > 0.02 or d < -0.02:
            pos += d * 0.30
            car.render(pos)
        elif pos != target:
            pos = target
            car.render(pos)
        f = int(target)
        if f != focus and -0.6 < target - pos < 0.6:
            focus = f
            car.set_focus(ccfont.SML, focus)
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
               "picker" if mode else "mixer",
               [round(v, 2) for v in mx.vol], mx.sysvol))
        frames = 0
        t0 = t1
