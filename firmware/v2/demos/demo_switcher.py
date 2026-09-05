# Demo: the app-switcher carousel — scroll a shelf of app icons, focused one
# large and black, neighbours small and gray.  This is the first real naub V2
# UI screen; carousel.py holds the reusable part.
#
#   top-left   knob (ENC1): pick app       click: "launch" (prints + flashes)
#   top-middle knob (ENC2): backlight
#   top-right  knob (ENC3): panel contrast
#
# Structure worth copying into other screens: the 4 FRC planes are cycled
# unconditionally at full rate, and everything else (animation step, input
# polling) happens once per completed gray cycle.  That keeps the gray steady
# and the frame loop allocation-free while at rest.
#
# Regenerate the icons for whatever you have open:  python host/gen_switcher.py
# Deploy:                                           python host/deploy.py switch
import time
import naubhw, ccfont, carousel, switcher_data

EASE = 0.30           # per gray cycle; ~0.15 s to settle one slot
SNAP = 0.02           # closer than this and we stop animating
SETTLED = 0.60        # only redraw the name once the scroll is nearly done:
                      # gfx text is per-pixel Python (~16 ms) and would stall a
                      # fast spin, whereas render() is a 1.9 ms byte copy
FLASH = 0.25          # seconds the label stays inverted after a click

disp = naubhw.display()
bl = naubhw.Backlight(80)
enc_sel, enc_bl, enc_con = naubhw.encoders((0, 1, 2))
sw_sel = naubhw.encoder_switches((0,))[0]

car = carousel.Carousel(switcher_data.ICONS, switcher_data.APPS)
views = [disp.make_views(fb) for fb in car.planes]
n_apps = len(switcher_data.APPS)
n_planes = len(views)

pos = 0.0
target = 0.0
focus = 0
car.render(pos)
car.set_focus(ccfont.SML, focus)

ls = enc_sel.position
lb = enc_bl.position
lc = enc_con.position
was_down = False
flash_until = 0.0

print("naub V2 app switcher | %d apps | knobs: left=select middle=backlight "
      "right=contrast | click left to launch" % n_apps)

i = 0
frames = 0
t0 = time.monotonic()
while True:
    disp.show_pages(views[i])
    i += 1
    frames += 1
    if i < n_planes:
        continue
    i = 0                      # one full gray cycle done: safe point to change

    # ---- animation ----
    d = target - pos
    if d > SNAP or d < -SNAP:
        pos += d * EASE
        car.render(pos)
    elif pos != target:
        pos = target
        car.render(pos)

    # ---- select knob ----
    p = enc_sel.position
    if p != ls:
        c = max(0, min(n_apps - 1, p))
        if c != p:             # clamp the encoder so it cannot wind up
            enc_sel.position = c
        ls = c
        target = float(c)

    f = int(target)
    if f != focus and -SETTLED < target - pos < SETTLED:
        focus = f
        car.set_focus(ccfont.SML, focus)

    # ---- select click ----
    down = not sw_sel.value
    if down and not was_down:
        print("LAUNCH: %s" % switcher_data.APPS[focus])
        car.set_focus(ccfont.SML, focus, invert=True)
        flash_until = time.monotonic() + FLASH
    was_down = down
    if flash_until and time.monotonic() > flash_until:
        flash_until = 0.0
        car.set_focus(ccfont.SML, focus)

    # ---- tuning knobs ----
    p = enc_bl.position
    if p != lb:
        bl.set(bl.pct + (p - lb) * 4)
        lb = p
    p = enc_con.position
    if p != lc:
        disp.set_contrast(disp.contrast + (p - lc))
        lc = p

    if frames >= 1024:
        t1 = time.monotonic()
        print("%.1f fps (%.1f Hz per gray cycle) app=%s C=%d BL=%d%%" %
              (frames / (t1 - t0), frames / (t1 - t0) / n_planes,
               switcher_data.APPS[focus], disp.contrast, bl.pct))
        frames = 0
        t0 = t1
