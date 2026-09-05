# Demo: refresh-rate / motion test.  Solid 16px ball bouncing horizontally,
# drawn with dirty-span partial updates (~2240 updates/s measured at 8 MHz).
# Shows the panel's real motion limit: the STN liquid crystal (~tens of ms
# response) motion-blurs the ball at speed — electronics are not the bottleneck.
#
#   top-middle knob (ENC2): ball speed, x1.2 per detent (10..6000 px/s)
#   top-left   knob (ENC1): panel contrast
#
# Deploy: python host/deploy.py ball
import time
import naubhw

disp = naubhw.display()                    # 8 MHz, contrast 31, 180 flip
bl = naubhw.Backlight(80)
enc_spd, enc_con = naubhw.encoders((1, 0))
ls = enc_spd.position
lc = enc_con.position

W = disp.W
D = 16                     # ball diameter; top at y=24 -> exactly pages 3+4
PAGE_A, PAGE_B = 3, 4
XMAX = W - D

# ball sprite: per-column 16-bit masks split into the two pages
SPR_T = bytearray(D)
SPR_B = bytearray(D)
for x in range(D):
    m = 0
    for y in range(D):
        if (x - 7.5) ** 2 + (y - 7.5) ** 2 <= 64:
            m |= 1 << y
    SPR_T[x] = m & 0xFF
    SPR_B[x] = m >> 8
MVS_T = memoryview(SPR_T)
MVS_B = memoryview(SPR_B)

ZERO = bytes(48)
BUF_T = bytearray(48)
BUF_B = bytearray(48)

disp.clear()
print("naub V2 refresh test | knobs: top-middle=speed top-left=contrast")

speed = 300.0
pos = 0.0
dirn = 1
oldx = -100
frames = 0
t0 = time.monotonic()
last_ns = time.monotonic_ns()

while True:
    now = time.monotonic_ns()
    dt = (now - last_ns) * 1e-9
    last_ns = now
    if dt > 0.02:          # cap physics step across GC/print hiccups
        dt = 0.02
    pos += speed * dt * dirn
    if pos < 0:
        pos = -pos; dirn = 1
    elif pos > XMAX:
        pos = 2 * XMAX - pos; dirn = -1
    xi = int(pos)

    if xi != oldx:
        if oldx < 0 or not -32 < xi - oldx < 32:
            disp.clear()                       # teleport: wipe and redraw
            BUF_T[:] = ZERO; BUF_B[:] = ZERO
            BUF_T[0:D] = SPR_T; BUF_B[0:D] = SPR_B
            minx = xi; span = D
        else:
            minx = xi if xi < oldx else oldx
            span = (xi if xi > oldx else oldx) + D - minx
            off = xi - minx
            BUF_T[:] = ZERO; BUF_B[:] = ZERO
            BUF_T[off:off + D] = SPR_T
            BUF_B[off:off + D] = SPR_B
        disp.span(PAGE_A, BUF_T, minx, span)
        disp.span(PAGE_B, BUF_B, minx, span)
        oldx = xi
    else:
        # unchanged: still push the sprite so the measured rate is real writes
        disp.span(PAGE_A, MVS_T, oldx)
        disp.span(PAGE_B, MVS_B, oldx)

    frames += 1
    if frames >= 4096:
        t1 = time.monotonic()
        print("%.0f updates/s | speed=%.0f px/s C=%d" %
              (frames / (t1 - t0), speed, disp.contrast))
        frames = 0
        t0 = t1

    d = enc_spd.position - ls
    if d:
        ls = enc_spd.position
        speed = max(10.0, min(6000.0, speed * (1.2 ** d)))

    d = enc_con.position - lc
    if d:
        lc = enc_con.position
        disp.set_contrast(disp.contrast + d)
