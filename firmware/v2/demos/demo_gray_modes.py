# Demo: the grayscale technique comparison (8 modes on one knob).  Bar modes
# are built on-device by graybars.py; gradient modes use precomputed FRC
# planes (graygrad.py).  This is the experiment that picked "GRAD 4F" as the
# naub grayscale recipe.
#
#   top-middle knob (ENC2): switch mode (label on screen)
#   top-left   knob (ENC1): panel contrast (shown as C= in the label)
#
# Deploy: python host/deploy.py gray
import time
import naubhw, graybars

disp = naubhw.display()
bl = naubhw.Backlight(80)
enc_mode, enc_con = naubhw.encoders((1, 0))
lm = enc_mode.position
lc = enc_con.position

mode = 6                    # boot on GRAD 4F, the user-picked winner

def label():
    return "%s C=%d" % (graybars.MODES[mode], disp.contrast)

def rebuild():
    global planes, views
    planes = graybars.build(mode, label())
    views = [disp.make_views(fb) for fb in planes]

rebuild()
print("naub V2 grayscale test | mode knob=top-middle contrast knob=top-left")
print("mode: " + label())

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
        print("%s | %.1f fps (%d planes -> %.1f Hz per gray cycle)" %
              (graybars.MODES[mode], frames / (t1 - t0),
               len(views), frames / (t1 - t0) / len(views)))
        frames = 0
        t0 = t1

    d = enc_mode.position - lm
    if d:
        lm = enc_mode.position
        mode = (mode + d) % len(graybars.MODES)
        rebuild()
        i = 0; frames = 0; t0 = time.monotonic()
        print("mode: " + label())

    d = enc_con.position - lc
    if d:
        lc = enc_con.position
        disp.set_contrast(disp.contrast + d)
        graybars.set_label(planes, label())
