# naub - two encoder bars + press-blobs on SSD1680 2.9" e-paper
# ENC1: A=GP0 B=GP1 SW=GP2   ENC2: A=GP3 B=GP4 SW=GP5
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
            if dx*dx + dy*dy <= rr:
                px(cx + dx, cy + dy, black)

def ring(cx, cy, r):
    ro, ri = r*r, (r-2)*(r-2)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            d = dx*dx + dy*dy
            if ri <= d <= ro:
                px(cx + dx, cy + dy, True)

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

# window covers both bars + blobs (native gate lines ~5..90); "naub"/label stay untouched
GY0, GY1 = 0, 100
def refresh_partial():
    cmd(0x11, [0x03]); cmd(0x44, [0x00, ROWBYTES - 1])
    cmd(0x45, [GY0 & 0xFF, GY0 >> 8, GY1 & 0xFF, GY1 >> 8])
    cmd(0x4E, [0x00]); cmd(0x4F, [GY0 & 0xFF, GY0 >> 8])
    cmd(0x24, fb[GY0*ROWBYTES : (GY1+1)*ROWBYTES])
    cmd(0x22, [0xFC]); cmd(0x20); wait_busy()

# ---- layout: two channels (right=ENC1, left=ENC2) ----
BAR_Y0, BAR_Y1 = 40, 118
NUM_Y, BLOB_Y, R_BLOB = 22, 12, 6
CH1 = dict(x0=256, x1=286, nox=259, bcx=271)   # Encoder 1
CH2 = dict(x0=212, x1=242, nox=215, bcx=227)   # Encoder 2
INNER_H = (BAR_Y1 - 2) - (BAR_Y0 + 2)
DIGIT = {d: bitmap_label.Label(terminalio.FONT, text=d).bitmap for d in "0123456789"}

def draw_base():
    rect(0,0,WIDTH,5); rect(0,HEIGHT-5,WIDTH,HEIGHT); rect(0,0,5,HEIGHT); rect(WIDTH-5,0,WIDTH,HEIGHT)
    text("naub", 4, 18, 30)
    text("encoders 1 & 2 - hold to blob", 1, 18, 96)
    for c in (CH1, CH2):
        x0, x1 = c['x0'], c['x1']
        rect(x0,BAR_Y0,x1,BAR_Y0+2); rect(x0,BAR_Y1-2,x1,BAR_Y1)
        rect(x0,BAR_Y0,x0+2,BAR_Y1); rect(x1-2,BAR_Y0,x1,BAR_Y1)
        ring(c['bcx'], BLOB_Y, R_BLOB)

def draw_number(c, v):
    nox = c['nox']
    rect(nox-1, NUM_Y-1, nox+20, NUM_Y+15, False)
    ox = nox
    for ch in str(v):
        b = DIGIT[ch]
        for y in range(b.height):
            for x in range(b.width):
                if b[x, y]: px(ox + x, NUM_Y + y, True)
        ox += 6

shown_fill = {"1": 0, "2": 0}
def fill_px(v): return int(round(v / 100.0 * INNER_H))
def draw_value(key, c, v):
    f = fill_px(v); sf = shown_fill[key]
    ix0, ix1, iy1 = c['x0']+2, c['x1']-2, BAR_Y1-2
    if f > sf:   rect(ix0, iy1-f, ix1, iy1-sf, True)
    elif f < sf: rect(ix0, iy1-sf, ix1, iy1-f, False)
    shown_fill[key] = f
    draw_number(c, v)

def draw_blob(c, on):
    cx = c['bcx']
    if on:
        fill_circle(cx, BLOB_Y, R_BLOB, True)
    else:
        rect(cx-R_BLOB-1, BLOB_Y-R_BLOB-1, cx+R_BLOB+1, BLOB_Y+R_BLOB+1, False)
        ring(cx, BLOB_Y, R_BLOB)

def inp(pin):
    d = digitalio.DigitalInOut(pin); d.switch_to_input(pull=digitalio.Pull.UP); return d

STEP = 2
def value_from(pos):
    v = pos * STEP
    return 0 if v < 0 else (100 if v > 100 else v)

print("init..."); panel_init()
draw_base(); draw_value("1", CH1, 0); draw_value("2", CH2, 0)
draw_blob(CH1, False); draw_blob(CH2, False)
refresh_full()
print("ready - two bars + press-blobs")

enc1 = rotaryio.IncrementalEncoder(board.GP0, board.GP1)
enc2 = rotaryio.IncrementalEncoder(board.GP3, board.GP4)
sw1 = inp(board.GP2); sw2 = inp(board.GP5)
lv1 = 0; lv2 = 0; b1 = False; b2 = False; pcount = 0
while True:
    changed = False
    v1 = value_from(enc1.position)
    if v1 != lv1: draw_value("1", CH1, v1); lv1 = v1; changed = True
    v2 = value_from(enc2.position)
    if v2 != lv2: draw_value("2", CH2, v2); lv2 = v2; changed = True
    p1 = not sw1.value
    if p1 != b1: draw_blob(CH1, p1); b1 = p1; changed = True
    p2 = not sw2.value
    if p2 != b2: draw_blob(CH2, p2); b2 = p2; changed = True
    if changed:
        if pcount >= 40: refresh_full(); pcount = 0
        else: refresh_partial(); pcount += 1
    time.sleep(0.01)
