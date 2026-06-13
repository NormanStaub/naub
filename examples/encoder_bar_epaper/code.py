# naub - encoder-driven volume bar on SSD1680 2.9" e-paper (296x128)
# Clean local partial refresh (OTP 0xFC, GxEPD2 T94 recipe) + incremental drawing.
# Display: CLK=GP10 DIN=GP11 CS=GP9 DC=GP8 RST=GP12 BUSY=GP13   Encoder: A=GP0 B=GP1
import time, board, busio, digitalio, rotaryio, terminalio, displayio
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
        if time.monotonic() - t0 > timeout:
            print("  ! BUSY timeout"); return
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

# partial windowed to the bar/number gate lines (rest of panel untouched & clean)
GY0, GY1 = 0, 56
def refresh_partial():
    cmd(0x11, [0x03]); cmd(0x44, [0x00, ROWBYTES - 1])
    cmd(0x45, [GY0 & 0xFF, GY0 >> 8, GY1 & 0xFF, GY1 >> 8])
    cmd(0x4E, [0x00]); cmd(0x4F, [GY0 & 0xFF, GY0 >> 8])
    cmd(0x24, fb[GY0*ROWBYTES : (GY1+1)*ROWBYTES])
    cmd(0x22, [0xFC]); cmd(0x20); wait_busy()

BX0, BX1, BY0, BY1 = 250, 286, 26, 116
IX0, IX1, IY0, IY1 = BX0+2, BX1-2, BY0+2, BY1-2
INNER_H = IY1 - IY0

def draw_base():
    rect(0,0,WIDTH,5); rect(0,HEIGHT-5,WIDTH,HEIGHT); rect(0,0,5,HEIGHT); rect(WIDTH-5,0,WIDTH,HEIGHT)
    text("naub", 4, 18, 24)
    text("display OK - 2.9in SSD1680", 1, 18, 92)
    rect(BX0,BY0,BX1,BY0+2); rect(BX0,BY1-2,BX1,BY1); rect(BX0,BY0,BX0+2,BY1); rect(BX1-2,BY0,BX1,BY1)

DIGIT = {d: bitmap_label.Label(terminalio.FONT, text=d).bitmap for d in "0123456789"}
def draw_number(v):
    rect(250, 6, 289, 24, False)
    ox = 252
    for ch in str(v):
        b = DIGIT[ch]
        for y in range(b.height):
            for x in range(b.width):
                if b[x, y]:
                    for j in range(2):
                        for i in range(2):
                            px(ox + x*2 + i, 7 + y*2 + j, True)
        ox += 12

def fill_px(v):
    return int(round(v / 100.0 * INNER_H))

shown_fill = 0
def draw_value(v):                 # incremental: only the changed band
    global shown_fill
    f = fill_px(v)
    if f > shown_fill:
        rect(IX0, IY1 - f, IX1, IY1 - shown_fill, True)
    elif f < shown_fill:
        rect(IX0, IY1 - shown_fill, IX1, IY1 - f, False)
    shown_fill = f
    draw_number(v)

STEP = 2
def value_from(pos):
    v = pos * STEP
    return 0 if v < 0 else (100 if v > 100 else v)

print("init..."); panel_init()
draw_base(); draw_value(0); refresh_full()
print("ready")

enc = rotaryio.IncrementalEncoder(board.GP0, board.GP1)
last_v = 0; pcount = 0
while True:
    v = value_from(enc.position)
    if v != last_v:
        draw_value(v)
        if pcount >= 40:
            refresh_full(); pcount = 0     # occasional whole-screen ghost sweep
        else:
            refresh_partial(); pcount += 1
        last_v = v
    time.sleep(0.01)
