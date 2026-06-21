# naub firmware V1 - per-app mixer over USB CDC (deej-style) + e-paper w/ icons
#
# Speaks the naub protocol (see PROTOCOL.md) on the usb_cdc DATA port:
#   D->H  naub proto=1 channels=4 fw=...   (banner, on boot + on `hello`)
#   D->H  vol <ch> <0..100>                (live, per detent)
#   D->H  mute <ch> <0|1>                  (encoder press = mute toggle)
#   D->H  btn 0 <0|1>                      (Button 1 latch)
#   H->D  hello | ping | label | icon | iconclear | level | mute
#
# Wiring (repo WIRING.md): ENC1 A/B/SW=GP0/GP1/GP2  ENC2 A/B/SW=GP3/GP4/GP5
#   ENC3 A/B/SW=GP14/GP15/GP16  ENC4 A/B/SW=GP17/GP18/GP19  (BTN1 GP6 unwired)
#   display DC/CS/CLK/DIN/RST/BUSY=GP8/GP9/GP10/GP11/GP12/GP13
# Libs needed in CIRCUITPY/lib: adafruit_display_text/, adafruit_ticks.mpy
#
# The SSD1680 driver (px/cmd/panel_init/refresh_*) is the validated code from
# examples/dithered_preview -- do not "improve" the refresh path, see WRITEUP.md.
import board, busio, digitalio, rotaryio, terminalio, displayio, time, binascii, math
try:
    import usb_cdc
    DATA = usb_cdc.data
    if DATA is not None:
        DATA.timeout = 0          # non-blocking reads (we poll in_waiting)
        DATA.write_timeout = 0    # non-blocking writes: never stall the loop
except Exception:
    DATA = None
displayio.release_displays()
from adafruit_display_text import bitmap_label

FW = "v1.0"
NCH = 4

WIDTH, HEIGHT = 296, 128
NX, NY = 128, 296
ROWBYTES = NX // 8
# Both mirror flags set -> 180deg rotation (the panel is upside-down in the case).
# Every primitive draws through px(), so flipping these flips the whole UI.
MNX, MNY = True, False
fb = bytearray(b"\xff" * (ROWBYTES * NY))

def px(lx, ly, black=True):
    if lx < 0 or lx >= WIDTH or ly < 0 or ly >= HEIGHT: return
    nx, ny = ly, lx
    if MNX: nx = (NX - 1) - nx
    if MNY: ny = (NY - 1) - ny
    bi = ny * ROWBYTES + (nx >> 3); mask = 0x80 >> (nx & 7)
    if black: fb[bi] &= ~mask
    else:     fb[bi] |= mask

def rect(x0, y0, x1, y1, black=True):
    for y in range(y0, y1):
        for x in range(x0, x1):
            px(x, y, black)

def fill_circle(cx, cy, r, black=True):
    rr = r * r
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx*dx + dy*dy <= rr: px(cx + dx, cy + dy, black)

def line(x0, y0, x1, y1):                # Bresenham, for the mute strike
    dx = abs(x1 - x0); dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1; sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        px(x0, y0, True)
        if x0 == x1 and y0 == y1: break
        e2 = 2 * err
        if e2 >= dy: err += dy; x0 += sx
        if e2 <= dx: err += dx; y0 += sy

DIGIT = {d: bitmap_label.Label(terminalio.FONT, text=d).bitmap for d in "0123456789"}

def text(s, ox, oy):                     # scale-1 terminalio text into fb
    bmp = bitmap_label.Label(terminalio.FONT, text=s).bitmap
    for y in range(bmp.height):
        for x in range(bmp.width):
            if bmp[x, y]: px(ox + x, oy + y, True)
    return bmp.width

# ---------- SSD1680 driver (validated, from dithered_preview) ----------
dc  = digitalio.DigitalInOut(board.GP8);  dc.switch_to_output()
cs  = digitalio.DigitalInOut(board.GP9);  cs.switch_to_output(value=True)
rst = digitalio.DigitalInOut(board.GP12); rst.switch_to_output(value=True)
busy = digitalio.DigitalInOut(board.GP13); busy.switch_to_input()
spi = busio.SPI(board.GP10, MOSI=board.GP11)
while not spi.try_lock(): pass
spi.configure(baudrate=4_000_000, polarity=0, phase=0)

def cmd(c, data=None):
    dc.value = False; cs.value = False; spi.write(bytes([c])); cs.value = True
    if data is not None:
        dc.value = True; cs.value = False; spi.write(bytes(data)); cs.value = True

_input_ready = False
def wait_busy(timeout=5):
    # The panel refresh is a ~0.5s blocking wait. Keep reading the encoders and
    # the CDC link *during* it so volume events stay near-realtime even though the
    # display only catches up after the refresh finishes.
    t0 = time.monotonic()
    while busy.value:
        if time.monotonic() - t0 > timeout: print("  ! BUSY timeout"); return
        if _input_ready: _service()
        time.sleep(0.001)

def _ram_area():
    cmd(0x11, [0x03]); cmd(0x44, [0x00, ROWBYTES - 1])
    cmd(0x45, [0x00, 0x00, (NY-1) & 0xFF, (NY-1) >> 8])
    cmd(0x4E, [0x00]); cmd(0x4F, [0x00, 0x00])

def panel_init():
    rst.value = False; time.sleep(0.01); rst.value = True; time.sleep(0.01); wait_busy()
    cmd(0x12); time.sleep(0.01); wait_busy()
    cmd(0x01, [0x27, 0x01, 0x00]); cmd(0x11, [0x03])
    cmd(0x3C, [0x05]); cmd(0x21, [0x00, 0x80]); cmd(0x18, [0x80])
    _ram_area(); wait_busy()

def refresh_full():
    _ram_area(); cmd(0x26, fb); _ram_area(); cmd(0x24, fb)
    cmd(0x22, [0xF7]); cmd(0x20); wait_busy()

# Full-panel partial: refresh time is waveform-bound (~0.5s) regardless of area
# (see WRITEUP.md), so windowing buys nothing here -- keep it simple & correct.
GY0, GY1 = 0, NY - 1
def refresh_partial():
    cmd(0x11, [0x03]); cmd(0x44, [0x00, ROWBYTES - 1])
    cmd(0x45, [GY0 & 0xFF, GY0 >> 8, GY1 & 0xFF, GY1 >> 8])
    cmd(0x4E, [0x00]); cmd(0x4F, [GY0 & 0xFF, GY0 >> 8])
    cmd(0x24, fb[GY0*ROWBYTES : (GY1+1)*ROWBYTES])
    cmd(0x22, [0xFC]); cmd(0x20); wait_busy()

# ---------- channel layout ----------
# 3 app encoders sit below the screen (spread across its length); the master sits
# off to the right of the screen -> mirror that here: the app arcs fan across the
# width and the master pill is squashed against the right edge.
CX = [46, 139, 232, 289]                 # app0..app2 spread, master far right
SYS_CH = 3                               # rightmost knob = system/master pill
                                         # (matches config: ch3 = __system__)

# App channels: a C-shaped arc gauge (open at the bottom) wrapping the app icon,
# with the app's name underneath. Just the filled stroke -- no track/border.
ARC_CY = 52                              # gauge+icon centered high; label sits below
ARC_RO, ARC_RI = 40, 30                  # outer/inner radius -> 10px stroke
ARC_START = 135.0                        # vol=0 tip (bottom-left), degrees (y-down)
ARC_SWEEP = 270.0                        # vol=100 sweeps round to the bottom-right tip
ICON_SZ = 40                             # icon box (must match icons.rs SIZE)
LABEL_Y = 94                             # app-name row, under the icon/gauge

# System channel: a tall, narrow capsule squashed to the right edge. Fills from
# the bottom and keeps its outline (border), unlike the borderless app arcs.
PILL_CY, PILL_H, PILL_R = 64, 96, 6      # center / height / cap radius (-> 12px wide)

# ---------- per-channel state ----------
labels = ["", "", "", ""]
icons  = [None, None, None, None]        # each: (w, h, bytes)
mute   = [False, False, False, False]
live   = [0, 0, 0, 0]
settled= [0, 0, 0, 0]
ct     = [0.0, 0.0, 0.0, 0.0]
pend   = [False, False, False, False]
dirty  = [False, False, False, False]

def _dither_ok(x, y):                    # 50% checkerboard -> reads as gray e-ink
    return ((x + y) & 1) == 0

def draw_arc(c, v, muted):
    # Just the filled stroke -- volume = how far the arc sweeps from the bottom-
    # left tip round to the bottom-right. No track/border behind the empty part.
    cx = CX[c]; cy = ARC_CY
    end = ARC_START + (v / 100.0) * ARC_SWEEP
    ri2 = ARC_RI * ARC_RI; ro2 = ARC_RO * ARC_RO
    for dy in range(-ARC_RO, ARC_RO + 1):
        for dx in range(-ARC_RO, ARC_RO + 1):
            d2 = dx * dx + dy * dy
            if d2 < ri2 or d2 > ro2: continue
            ang = math.degrees(math.atan2(dy, dx))
            if ang < 0: ang += 360.0
            if ang < ARC_START: ang += 360.0            # lift into [135, 495)
            if ang > end: continue                      # past the fill (or in the gap)
            if muted and not _dither_ok(cx + dx, cy + dy): continue
            px(cx + dx, cy + dy, True)

def draw_icon_center(c):
    ic = icons[c]
    if not ic: return
    w, h, data = ic
    stride = (w + 7) // 8
    if len(data) < stride * h: return            # guard: never index past short data
    ox = CX[c] - w // 2; oy = ARC_CY - h // 2
    for yy in range(min(h, ICON_SZ)):
        for xx in range(min(w, ICON_SZ)):
            if (data[yy * stride + (xx >> 3)] >> (7 - (xx & 7))) & 1:
                px(ox + xx, oy + yy, True)        # icon always solid (no mute dither)

def draw_label(c):
    s = labels[c][:11]
    if not s: return
    text(s, CX[c] - (len(s) * 6) // 2, LABEL_Y)  # centered terminalio text

def draw_pill(c, v, muted):
    cx = CX[c]
    top = PILL_CY - PILL_H // 2; bot = PILL_CY + PILL_H // 2; r = PILL_R
    fill_top = bot - int(round(v / 100.0 * PILL_H))
    for y in range(top, bot + 1):
        if   y < top + r: dy = (top + r) - y            # rounded caps
        elif y > bot - r: dy = y - (bot - r)
        else:             dy = 0                        # straight middle
        if dy > r: continue
        hw = int((r * r - dy * dy) ** 0.5)
        for x in range(cx - hw, cx + hw + 1):
            if abs(x - cx) >= hw - 1:                    # capsule outline (always solid)
                px(x, y, True)
            elif y >= fill_top:                         # fill from the bottom
                if muted and not _dither_ok(x, y): continue
                px(x, y, True)

def draw_channel(c):
    # Commits the *settled* value (after the knob stops); audio already moved per
    # detent. App channels get the arc+icon, the system knob gets the pill.
    if c == SYS_CH:
        rect(WIDTH - 18, 0, WIDTH, HEIGHT, False)            # clear the right strip
        draw_pill(c, settled[c], mute[c])
    else:
        rect(CX[c] - ARC_RO - 6, 0, CX[c] + ARC_RO + 6, HEIGHT, False)  # clear cell
        draw_arc(c, settled[c], mute[c])
        draw_icon_center(c)
        draw_label(c)

# ---------- CDC protocol ----------
_rxbuf = bytearray()
_last_rx = 0.0          # monotonic time of the last CDC byte (for burst-idle gate)

DEBUG = False
def send(s):
    if DATA is None: return
    try:
        DATA.write((s + "\n").encode("utf-8"))
        if DEBUG: print("DBG tx:", s)
    except Exception as e:
        if DEBUG: print("DBG tx err:", e)

def send_banner():
    # Banner only. The host is authoritative for levels at startup (it reads each
    # app's real volume and pushes `level`), so we must NOT emit `vol` snapshots
    # here -- the host would apply them as user input and zero every app.
    send("naub proto=1 channels=%d fw=%s" % (NCH, FW))

def handle_line(b):
    try: s = b.decode("utf-8").strip()
    except Exception: return
    if not s: return
    p = s.split(" ", 4)               # at most: icon <ch> <w> <h> <base64>
    v = p[0]
    if v == "hello":
        send_banner(); return
    if v == "ping":
        send("pong"); return
    if v == "label" and len(p) >= 3:
        c = _ci(p[1])
        if c is not None:
            labels[c] = " ".join(p[2:]); dirty[c] = True
        return
    if v == "iconclear" and len(p) >= 2:
        c = _ci(p[1])
        if c is not None: icons[c] = None; dirty[c] = True
        return
    if v == "icon" and len(p) >= 5:
        c = _ci(p[1])
        if c is None: return
        try:
            w = int(p[2]); h = int(p[3])
            data = binascii.a2b_base64(p[4])
            if len(data) >= ((w + 7) // 8) * h:   # ignore a short/corrupt frame
                icons[c] = (w, h, data); dirty[c] = True
        except Exception as e:
            print("icon parse err:", e)
        return
    if v == "level" and len(p) >= 3:
        c = _ci(p[1])
        if c is not None:
            nv = _cv(p[2]); settled[c] = nv; live[c] = nv; pend[c] = False; dirty[c] = True
        return
    if v == "mute" and len(p) >= 3:
        c = _ci(p[1])
        if c is not None: mute[c] = (p[2].strip() == "1"); dirty[c] = True
        return
    # unknown verb: ignore (forward compat)

def _ci(t):
    try:
        c = int(t)
        return c if 0 <= c < NCH else None
    except Exception:
        return None

def _cv(t):
    try: v = int(t)
    except Exception: return 0
    return 0 if v < 0 else (100 if v > 100 else v)

def poll_cdc():
    global _rxbuf, _last_rx
    if DATA is None: return
    n = DATA.in_waiting
    if n:
        if DEBUG: print("DBG rx:", n)
        _rxbuf.extend(DATA.read(n)); _last_rx = time.monotonic()
    while True:
        i = _rxbuf.find(b"\n")
        if i < 0: break
        ln = bytes(_rxbuf[:i])
        _rxbuf = _rxbuf[i + 1:]      # slice -> new bytearray (no item-delete)
        handle_line(ln)

# ---------- inputs ----------
def inp(pin):
    d = digitalio.DigitalInOut(pin); d.switch_to_input(pull=digitalio.Pull.UP); return d

STEP = 2                                 # volume units per detent

encs = [rotaryio.IncrementalEncoder(board.GP0, board.GP1),
        rotaryio.IncrementalEncoder(board.GP3, board.GP4),
        rotaryio.IncrementalEncoder(board.GP14, board.GP15),
        rotaryio.IncrementalEncoder(board.GP17, board.GP18)]
sws  = [inp(board.GP2), inp(board.GP5), inp(board.GP16), inp(board.GP19)]
btn  = None                              # Button 1 (GP6) disconnected for now
# Encoders are read as RELATIVE deltas, not absolute position, so a host `level`
# push and a physical turn compose instead of fighting each other.
last_pos = [e.position if e is not None else 0 for e in encs]
press_prev = [False, False, False, False]    # switch held last loop
press_turned = [False, False, False, False]  # rotated during the current hold?
btn_on = False; btn_prev = False

def _clampv(v):
    return 0 if v < 0 else (100 if v > 100 else v)

SETTLE_S = 0.13    # commit the bar this long after the last detent; must exceed
                   # the gap between detents in a slow scroll, or the bar repaints
                   # mid-turn (audio still moves per detent regardless)
def poll_inputs(now):
    # Reads encoders/switches/button and emits events + updates state. Does NO
    # drawing -- only sets dirty[] flags. Safe to call from inside wait_busy.
    global btn_on, btn_prev
    for c in range(NCH):
        sw = sws[c]
        held = (sw is not None) and (not sw.value)    # active-low: pressed = LOW
        if sw is not None and held and not press_prev[c]:
            press_turned[c] = False                   # new press: assume it's a click
        e = encs[c]
        if e is not None:
            pos = e.position
            dv = pos - last_pos[c]
            if dv != 0:
                last_pos[c] = pos
                if held:                              # hold + turn = cycle source
                    press_turned[c] = True
                    s = 1 if dv > 0 else -1
                    for _ in range(abs(dv)):
                        send("cycle %d %d" % (c, s))   # one step per detent
                else:                                 # turn = volume
                    nv = _clampv(live[c] + dv * STEP)
                    if nv != live[c]:
                        live[c] = nv; ct[c] = now; pend[c] = True
                        send("vol %d %d" % (c, nv))   # live audio, per detent
            # Display commits only after the knob stops -- no dirty/refresh while
            # turning, so the bar never scrolls. (Audio already moved, above.)
            if pend[c] and (now - ct[c]) > SETTLE_S:
                pend[c] = False
                if settled[c] != live[c]:
                    settled[c] = live[c]; dirty[c] = True
        if sw is not None:
            if (not held) and press_prev[c] and not press_turned[c]:
                mute[c] = not mute[c]; dirty[c] = True  # click (no turn) = mute toggle
                send("mute %d %d" % (c, 1 if mute[c] else 0))
            press_prev[c] = held
    if btn is not None:
        pb = not btn.value
        if pb and not btn_prev:
            btn_on = not btn_on
            send("btn 0 %d" % (1 if btn_on else 0))
        btn_prev = pb

def _service():
    poll_cdc()
    poll_inputs(time.monotonic())

# ---------- boot ----------
print("naub firmware", FW, "- init display...")
panel_init()
for c in range(NCH): draw_channel(c)
refresh_full()
send_banner()
if DATA is None:
    print("WARN: usb_cdc.data is None -- did boot.py enable it? (power-cycle)")
print("ready")

FULL_AFTER = 60
IDLE_FULL_S = 12.0
pcount = 0
last_act = time.monotonic()
_input_ready = True          # wait_busy may now service inputs during refreshes

while True:
    # Drain the CDC RX buffer fully each pass. The reconnect burst pushes ~2 KB
    # of icon data and the device buffer is small, so we must keep reading.
    poll_cdc()
    while DATA is not None and DATA.in_waiting:
        poll_cdc()
    poll_inputs(time.monotonic())

    # Defer the (slow) redraws until the CDC burst goes quiet: a channel redraw
    # blocks reads for tens of ms, long enough for an incoming icon line to
    # overrun the buffer and arrive truncated. Reading the whole burst first,
    # then drawing, keeps icons intact.
    burst_idle = (time.monotonic() - _last_rx) > 0.03
    any_dirty = False
    if burst_idle:
        for c in range(NCH):
            if dirty[c]:
                draw_channel(c); dirty[c] = False; any_dirty = True
    if any_dirty:
        last_act = time.monotonic()
        refresh_partial(); pcount += 1     # wait_busy keeps inputs live during this
    elif pcount >= FULL_AFTER and (time.monotonic() - last_act) > IDLE_FULL_S:
        refresh_full(); pcount = 0
        print("idle cleanup (full refresh)")
    time.sleep(0.002)
