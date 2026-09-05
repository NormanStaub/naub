# Demo: open-app icons in 4-plane FRC grayscale — the canonical example of
# displaying grayscale content.  Planes come precomputed from the PC
# (host/gen_appicons.py -> appicons.py); the loop just cycles them at max
# rate (~340 fps => ~85 Hz per gray cycle) and the panel's slow LC response
# averages them into real grays.
#
#   top-middle knob (ENC2): backlight
#   top-left   knob (ENC1): panel contrast
#
# Deploy: python host/deploy.py icons
import time
import naubhw, appicons

disp = naubhw.display()
bl = naubhw.Backlight(80)
enc_bl, enc_con = naubhw.encoders((1, 0))
lb = enc_bl.position
lc = enc_con.position

views = [disp.make_views(fb) for fb in appicons.PLANES]

print("naub V2 app icons | %d FRC planes | knobs: top-middle=backlight "
      "top-left=contrast" % len(views))

i = 0
frames = 0
t0 = time.monotonic()
while True:
    disp.show_pages(views[i])
    i += 1
    if i >= len(views):
        i = 0
    frames += 1
    if frames >= 1024:
        t1 = time.monotonic()
        print("%.1f fps (%.1f Hz per gray cycle) C=%d BL=%d%%" %
              (frames / (t1 - t0), frames / (t1 - t0) / len(views),
               disp.contrast, bl.pct))
        frames = 0
        t0 = t1

    d = enc_bl.position - lb
    if d:
        lb = enc_bl.position
        bl.set(bl.pct + d * 4)

    d = enc_con.position - lc
    if d:
        lc = enc_con.position
        disp.set_contrast(disp.contrast + d)
