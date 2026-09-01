# naub V2 - STAGE 4: EA DOGM128W-6 / ST7565R bring-up.  Runs on the board.
# Pins: A0=GP8 CS=GP9 SCL=GP10(SPI1 SCK) SI=GP11(SPI1 TX) RST=GP12 BL=GP13
import time, sys, supervisor, busio, digitalio, pwmio
from microcontroller import pin as m

W, H = 128, 64
PAGES = H // 8
COL_OFFSET = 0        # DOGM128 uses SEG0..127 with no offset. Flip to 4 if shifted.
ORIENT_180 = False    # True -> 0xA0/0xC8 instead of 0xA1/0xC0

def wait(msg):
    print("\n>>> %s" % msg)
    print("    [ENTER to continue]")
    while supervisor.runtime.serial_bytes_available:
        sys.stdin.read(1)
    while not supervisor.runtime.serial_bytes_available:
        time.sleep(0.05)
    sys.stdin.read(1)

print("\n" + "=" * 60)
print("STAGE 4 - ST7565R / DOGM128W-6 BRING-UP")
print("=" * 60)

# ---------------------------------------------------------------- A: shorts
print("\n--- A. Short check on the display bus (you just soldered U2) ---")
BUS = [("LCD_A0", 8), ("LCD_CS", 9), ("LCD_SCL", 10), ("LCD_SI", 11), ("LCD_RST", 12)]
pins = {}
for n, g in BUS:
    d = digitalio.DigitalInOut(getattr(m, "GPIO%d" % g))
    d.direction = digitalio.Direction.INPUT
    d.pull = digitalio.Pull.UP
    pins[n] = d
time.sleep(0.05)
bad = [n for n, g in BUS if not pins[n].value]
if bad:
    print("  STUCK LOW: %s  -> solder bridge to GND under the glass" % ", ".join(bad))
else:
    print("  all 5 bus pins idle HIGH")
bridges = []
for n, g in BUS:
    if n in bad:
        continue
    pins[n].direction = digitalio.Direction.OUTPUT
    pins[n].value = False
    time.sleep(0.015)
    for n2, g2 in BUS:
        if n2 == n or n2 in bad:
            continue
        if not pins[n2].value:
            bridges.append((n, n2)); print("  BRIDGE %s <-> %s" % (n, n2))
    pins[n].direction = digitalio.Direction.INPUT
    pins[n].pull = digitalio.Pull.UP
    time.sleep(0.01)
if not bridges and not bad:
    print("  no bridges on the display bus")
for d in pins.values():
    d.deinit()
if bad or bridges:
    print("\n  !! STOP. Fix the bridge before powering the panel.")
    raise SystemExit

# ---------------------------------------------------------------- B: init
print("\n--- B. Bus + ST7565 init ---")
spi = busio.SPI(m.GPIO10, MOSI=m.GPIO11)
while not spi.try_lock():
    pass
spi.configure(baudrate=1000000, polarity=0, phase=0)   # ST7565: SCK idle low, sample rising
print("  SPI1 up: SCK=GP10 MOSI=GP11 @1MHz mode0")

a0  = digitalio.DigitalInOut(m.GPIO8);  a0.direction  = digitalio.Direction.OUTPUT
cs  = digitalio.DigitalInOut(m.GPIO9);  cs.direction  = digitalio.Direction.OUTPUT
rst = digitalio.DigitalInOut(m.GPIO12); rst.direction = digitalio.Direction.OUTPUT
cs.value = True; a0.value = False

rst.value = False; time.sleep(0.005)
rst.value = True;  time.sleep(0.005)
print("  RST pulsed low->high")

def cmd(*bs):
    a0.value = False; cs.value = False
    spi.write(bytes(bs)); cs.value = True

def data(buf):
    a0.value = True; cs.value = False
    spi.write(buf); cs.value = True

CONTRAST = 0x16
def init(contrast=CONTRAST):
    cmd(0xE2); time.sleep(0.005)                 # soft reset
    cmd(0x40)                                    # start line 0
    cmd(0xA0 if ORIENT_180 else 0xA1)            # ADC / SEG direction
    cmd(0xC8 if ORIENT_180 else 0xC0)            # COM direction
    cmd(0xA6)                                    # normal (not inverted)
    cmd(0xA2)                                    # bias 1/9
    cmd(0x2F)                                    # booster + regulator + follower ON
    cmd(0xF8, 0x00)                              # booster ratio 4x
    cmd(0x27)                                    # V0 resistor ratio
    cmd(0x81, contrast)                          # electronic volume
    cmd(0xAC, 0x00)                              # static indicator off
    cmd(0xAF)                                    # display ON
init()
print("  init sent (EA DOGM128-6 'Single Supply 3.0/3.3V' sequence)")

def show(fb):
    for p in range(PAGES):
        cmd(0xB0 | p, 0x10 | ((COL_OFFSET >> 4) & 0x0F), 0x00 | (COL_OFFSET & 0x0F))
        data(fb[p * W:(p + 1) * W])

blank = bytearray(W * PAGES)
show(blank)

print("\n  The pump is now running (or not). This is the measurement that")
print("  splits the whole fault tree - take it before looking at the glass.")
wait("Measure TP_VOUT.  9-13 V = pump running.  ~3.3 V = pump dead (check\n    the CAP3P->CAP1N pairing, PCB_DESIGN 4.2).  Then measure TP_V0.")

# ---------------------------------------------------------------- C: patterns
print("\n--- C. Test patterns ---")
def mk(fn):
    fb = bytearray(W * PAGES)
    for p in range(PAGES):
        for x in range(W):
            fb[p * W + x] = fn(x, p)
    return fb

PATTERNS = [
    ("ALL PIXELS ON  - every SEG and COM driven; look for dead rows/columns",
     mk(lambda x, p: 0xFF)),
    ("ALL PIXELS OFF - any stuck-on pixel shows here",
     mk(lambda x, p: 0x00)),
    ("VERTICAL STRIPES - 1px on/off; checks column (SEG) addressing",
     mk(lambda x, p: 0xFF if x % 2 == 0 else 0x00)),
    ("HORIZONTAL STRIPES - checks row (COM) drive",
     mk(lambda x, p: 0x55)),
    ("CHECKERBOARD 8x8 - checks page addressing",
     mk(lambda x, p: 0xFF if ((x // 8) + p) % 2 == 0 else 0x00)),
    ("BORDER + DIAGONAL - proves full 128x64 extent and orientation",
     None),
]
bd = bytearray(W * PAGES)
for x in range(W):
    bd[x] |= 0x01                      # top row
    bd[7 * W + x] |= 0x80              # bottom row
for p in range(PAGES):
    bd[p * W] = 0xFF
    bd[p * W + W - 1] = 0xFF
for x in range(W):                     # diagonal: y = x/2
    y = x // 2
    bd[(y // 8) * W + x] |= 1 << (y % 8)
PATTERNS[-1] = (PATTERNS[-1][0], bd)

for label, fb in PATTERNS:
    show(fb)
    print("\n  showing: %s" % label)
    wait("Confirm on the glass, then continue")

# ---------------------------------------------------------------- D: contrast
print("\n--- D. Contrast sweep (electronic volume 0x81) ---")
show(PATTERNS[-1][1])
for v in range(0x00, 0x40, 0x04):
    cmd(0x81, v)
    print("  contrast 0x%02X" % v)
    time.sleep(0.7)
cmd(0x81, CONTRAST)
print("  restored to 0x%02X - if a different value looked better, set CONTRAST" % CONTRAST)
wait("Note the value that looked best")

# ---------------------------------------------------------------- E: backlight
print("\n--- E. Backlight (only if JP_BL is fitted) ---")
print("  If JP_BL is still open this does nothing - that is expected.")
bl = pwmio.PWMOut(m.GPIO13, frequency=20000, duty_cycle=0)
print("  ramping GP13 0 -> 100%%; TP_BLPWM should swing 0<->3.3V,")
print("  TP_BLK (Q1 drain) should swing with it. Gate moving + drain stuck = dead FET.")
for d in range(0, 65536, 4096):
    bl.duty_cycle = d
    time.sleep(0.15)
for d in range(65535, -1, -4096):
    bl.duty_cycle = d
    time.sleep(0.15)
bl.duty_cycle = 32768
print("  left at 50%% - total backlight current should be ~31 mA (3 x ~10 mA)")
wait("Confirm brightness, then finish")
bl.deinit()

print("\n" + "=" * 60)
print("STAGE 4 COMPLETE")
print("  bus:      no shorts")
print("  init:     sent, no bus errors")
print("  patterns: judged by eye above")
print("If the glass stayed blank but TP_VOUT read 9-13 V, the pump is fine and")
print("the fault is downstream: clip a logic analyzer on J_LCD and check whether")
print("SI/SCL/A0/CS are moving at all.  That splits MCU-not-talking from")
print("display-not-listening. If TP_VOUT ~= 3.3 V, it is the cap network.")
print("=" * 60)
show(PATTERNS[-1][1])
while True:
    time.sleep(1)
