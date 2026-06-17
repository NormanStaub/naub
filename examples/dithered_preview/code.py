# naub - live numeric readout (fast, on turn) + bar settles on stop; blobs + B1 toggle
# ENC1: A=GP0 B=GP1 SW=GP2   ENC2: A=GP3 B=GP4 SW=GP5   BTN1: GP6
import board, busio, digitalio, rotaryio, terminalio, displayio, time
displayio.release_displays()
from adafruit_display_text import bitmap_label

WIDTH, HEIGHT = 296, 128
NX, NY = 128, 296
ROWBYTES = NX // 8
MNX, MNY = False, True
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

def ring(cx, cy, r):
    ro, ri = r*r, (r-2)*(r-2)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            d = dx*dx + dy*dy
            if ri <= d <= ro: px(cx + dx, cy + dy, True)

def text(s, scale, ox, oy):
    bmp = bitmap_label.Label(terminalio.FONT, text=s).bitmap
    for y in range(bmp.height):
        for x in range(bmp.width):
            if bmp[x, y]:
                for j in range(scale):
                    for i in range(scale):
                        px(ox + x*scale + i, oy + y*scale + j, True)

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

def wait_busy(timeout=5):
    t0 = time.monotonic()
    while busy.value:
        if time.monotonic() - t0 > timeout: print("  ! BUSY timeout"); return
        time.sleep(0.002)

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

GY0, GY1 = 0, 150
def refresh_partial():
    cmd(0x11, [0x03]); cmd(0x44, [0x00, ROWBYTES - 1])
    cmd(0x45, [GY0 & 0xFF, GY0 >> 8, GY1 & 0xFF, GY1 >> 8])
    cmd(0x4E, [0x00]); cmd(0x4F, [GY0 & 0xFF, GY0 >> 8])
    cmd(0x24, fb[GY0*ROWBYTES : (GY1+1)*ROWBYTES])
    cmd(0x22, [0xFC]); cmd(0x20); wait_busy()

# ---- layout ----
BAR_Y0, BAR_Y1 = 40, 118
BLOB_Y, R_BLOB = 12, 6
NBOX_Y0, NBOX_Y1 = 71, 91     # number box overlaid mid-bar
CH1 = dict(x0=256, x1=286, bcx=271)
CH2 = dict(x0=212, x1=242, bcx=227)
INNER_H = (BAR_Y1 - 2) - (BAR_Y0 + 2)
BTN_X0, BTN_X1, BTN_Y0, BTN_Y1 = 155, 187, 52, 84
DIGIT = {d: bitmap_label.Label(terminalio.FONT, text=d).bitmap for d in "0123456789"}

def draw_base():
    rect(0,0,WIDTH,5); rect(0,HEIGHT-5,WIDTH,HEIGHT); rect(0,0,5,HEIGHT); rect(WIDTH-5,0,WIDTH,HEIGHT)
    text("naub", 4, 18, 22); text("USB mixer", 1, 18, 90)
    for c in (CH1, CH2):
        x0, x1 = c['x0'], c['x1']
        rect(x0,BAR_Y0,x1,BAR_Y0+2); rect(x0,BAR_Y1-2,x1,BAR_Y1)
        rect(x0,BAR_Y0,x0+2,BAR_Y1); rect(x1-2,BAR_Y0,x1,BAR_Y1)
        ring(c['bcx'], BLOB_Y, R_BLOB)
    text("B1", 1, 158, 40)
    rect(BTN_X0,BTN_Y0,BTN_X1,BTN_Y0+2); rect(BTN_X0,BTN_Y1-2,BTN_X1,BTN_Y1)
    rect(BTN_X0,BTN_Y0,BTN_X0+2,BTN_Y1); rect(BTN_X1-2,BTN_Y0,BTN_X1,BTN_Y1)

def fill_px(v): return int(round(v / 100.0 * INNER_H))

def draw_num(c, v):              # live: white box + centered black digits, overlaid mid-bar
    cx = c['bcx']
    rect(cx-11, NBOX_Y0, cx+11, NBOX_Y1, False)
    s = str(v); ox = cx - (len(s)*6)//2
    for ch in s:
        b = DIGIT[ch]
        for y in range(b.height):
            for x in range(b.width):
                if b[x, y]: px(ox + x, NBOX_Y0+4 + y, True)
        ox += 6

def draw_bar(c, v):              # committed: solid fill to value, number on top
    ix0, ix1 = c['x0']+2, c['x1']-2
    iy0, iy1 = BAR_Y0+2, BAR_Y1-2
    rect(ix0, iy0, ix1, iy1, False)
    f = fill_px(v)
    if f > 0: rect(ix0, iy1-f, ix1, iy1, True)
    draw_num(c, v)

DITHER_STEP = 2                  # black dot every Nth px both axes; higher = sparser/lighter
def _dither(x0, y0, x1, y1):     # sparse dot grid -> light gray on e-paper
    for y in range(y0, y1):
        if y % DITHER_STEP: continue
        for x in range(x0, x1):
            if x % DITHER_STEP == 0: px(x, y, True)

def draw_preview(c, settled, live):   # solid up to original level, dithered for the pending change
    ix0, ix1 = c['x0']+2, c['x1']-2
    iy1 = BAR_Y1-2
    rect(ix0, BAR_Y0+2, ix1, iy1, False)
    lo = fill_px(min(settled, live)); hi = fill_px(max(settled, live))
    if lo > 0: rect(ix0, iy1-lo, ix1, iy1, True)         # solid: original level
    if hi > lo: _dither(ix0, iy1-hi, ix1, iy1-lo)        # dithered: the change
    draw_num(c, live)

def draw_blob(c, on):
    cx = c['bcx']
    if on: fill_circle(cx, BLOB_Y, R_BLOB, True)
    else:
        rect(cx-R_BLOB-1, BLOB_Y-R_BLOB-1, cx+R_BLOB+1, BLOB_Y+R_BLOB+1, False)
        ring(cx, BLOB_Y, R_BLOB)

def draw_btn(on): rect(BTN_X0+2, BTN_Y0+2, BTN_X1-2, BTN_Y1-2, on)

def inp(pin):
    d = digitalio.DigitalInOut(pin); d.switch_to_input(pull=digitalio.Pull.UP); return d

STEP = 2
def value_from(pos):
    v = pos * STEP
    return 0 if v < 0 else (100 if v > 100 else v)

SETTLE_S = 0.125
print("init..."); panel_init()
draw_base()
draw_bar(CH1, 0); draw_bar(CH2, 0)
draw_blob(CH1, False); draw_blob(CH2, False); draw_btn(False)
refresh_full()
print("ready - live number on turn, bar on settle")

enc1 = rotaryio.IncrementalEncoder(board.GP0, board.GP1)
enc2 = rotaryio.IncrementalEncoder(board.GP3, board.GP4)
sw1 = inp(board.GP2); sw2 = inp(board.GP5); btn = inp(board.GP6)
st = {"1": {"live":0,"settled":0,"ct":0.0,"pend":False,"c":CH1,"e":enc1},
      "2": {"live":0,"settled":0,"ct":0.0,"pend":False,"c":CH2,"e":enc2}}
bl1 = False; bl2 = False; btn_on = False; btn_prev = False; pcount = 0
last_act = time.monotonic()
FULL_AFTER = 60     # partials to accumulate before a cleanup is worthwhile (higher = rarer flash + relax artifact, a bit more ghosting)
IDLE_FULL_S = 12.0  # only run the cleanup full refresh after this idle gap
while True:
    changed = False; action = ""
    now = time.monotonic()
    for k in ("1", "2"):
        s = st[k]
        v = value_from(s["e"].position)
        if v != s["live"]:
            s["live"] = v; s["ct"] = now; s["pend"] = True
            draw_preview(s["c"], s["settled"], v)        # live: original solid + change dithered
            changed = True; action = "live"
        elif s["pend"] and (now - s["ct"]) > SETTLE_S and s["settled"] != s["live"]:
            draw_bar(s["c"], s["live"]); s["settled"] = s["live"]; s["pend"] = False
            changed = True; action = "set"
    p1 = not sw1.value
    if p1 != bl1: draw_blob(CH1, p1); bl1 = p1; changed = True; action = "blob"
    p2 = not sw2.value
    if p2 != bl2: draw_blob(CH2, p2); bl2 = p2; changed = True; action = "blob"
    pb = not btn.value
    if pb and not btn_prev: btn_on = not btn_on; draw_btn(btn_on); changed = True; action = "btn"
    btn_prev = pb
    if changed:
        last_act = now
        refresh_partial(); pcount += 1     # always partial during use (no mid-spin flash)
    elif pcount >= FULL_AFTER and (now - last_act) > IDLE_FULL_S:
        refresh_full(); pcount = 0         # deferred ghost-sweep, only when idle
        print("idle cleanup (full refresh)")
    time.sleep(0.01)
