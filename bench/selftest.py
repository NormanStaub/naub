# naub U1 module acceptance test - runs on the bare RP2350-Tiny, off-board.
import board, digitalio, time, microcontroller, os, sys

PINMAP = [
    ("GP0","ENC1_A"),("GP1","ENC1_B"),("GP2","ENC1_SW"),
    ("GP3","ENC2_A"),("GP4","ENC2_B"),("GP5","ENC2_SW"),
    ("GP6","BTN1"),("GP7","BTN2"),("GP8","LCD_A0"),
    ("GP9","LCD_CS"),("GP10","LCD_SCL"),("GP11","LCD_SI"),
    ("GP12","LCD_RST"),("GP13","BL_PWM"),
    ("GP14","ENC3_A"),("GP15","ENC3_B"),
    ("GP17","ENC4_A"),("GP18","ENC4_B"),("GP19","ENC4_SW"),
    ("GP20","BTN3"),("GP21","BTN4"),("GP22","BTN5"),("GP23","ENC3_SW"),
    ("GP26","spare ADC"),("GP27","spare ADC"),("GP28","spare ADC"),("GP29","spare ADC"),
]

print("\n" + "="*58)
print("NAUB U1 MODULE ACCEPTANCE TEST")
print("="*58)
print("impl    :", sys.implementation.name, ".".join(str(x) for x in sys.implementation.version))
print("board   :", board.board_id)
print("uid     :", "".join("%02X" % b for b in microcontroller.cpu.uid))
print("cpu     : %.0f MHz" % (microcontroller.cpu.frequency/1e6))
try:
    print("temp    : %.1f C" % microcontroller.cpu.temperature)
except Exception as e:
    print("temp    : n/a", e)
try:
    print("voltage : %.2f V" % microcontroller.cpu.voltage)
except Exception:
    pass
s = os.statvfs("/")
print("user fs : %d KiB total, %d KiB free" % (s[0]*s[2]//1024, s[0]*s[3]//1024))

def pulltest(pinname):
    """Input w/ pull-up must read 1; pull-down must read 0. Catches dead or stuck pads."""
    pin = getattr(board, pinname)
    d = digitalio.DigitalInOut(pin)
    try:
        d.direction = digitalio.Direction.INPUT
        d.pull = digitalio.Pull.UP
        time.sleep(0.003)
        hi = d.value
        d.pull = digitalio.Pull.DOWN
        time.sleep(0.003)
        lo = d.value
        return hi, lo
    finally:
        d.deinit()

print("\n--- GPIO pad test (pull-up reads 1 / pull-down reads 0) ---")
fails = []
for name, fn in PINMAP:
    try:
        hi, lo = pulltest(name)
        ok = (hi is True) and (lo is False)
        print("  %-5s %-10s up=%-5s dn=%-5s  %s" % (name, fn, hi, lo, "PASS" if ok else "FAIL"))
        if not ok:
            fails.append(name)
    except Exception as e:
        print("  %-5s %-10s ERROR %s" % (name, fn, e))
        fails.append(name)

print("\n--- GP24/GP25 (not broken out on Tiny; informational) ---")
for name in ("GP24","GP25"):
    try:
        hi, lo = pulltest(name)
        print("  %-5s up=%-5s dn=%-5s" % (name, hi, lo))
    except Exception as e:
        print("  %-5s not exposed by this build (%s)" % (name, type(e).__name__))

print("\n--- ADC (floating inputs, ~0.0-1.8V expected) ---")
try:
    import analogio
    for name in ("GP26","GP27","GP28","GP29"):
        a = analogio.AnalogIn(getattr(board, name))
        v = a.value * a.reference_voltage / 65535
        print("  %-5s raw=%-6d %.3f V" % (name, a.value, v))
        a.deinit()
except Exception as e:
    print("  ADC error:", e)

print("\n--- Peripheral construction on the naub pin assignment ---")
try:
    import busio
    spi = busio.SPI(board.GP10, MOSI=board.GP11)
    spi.try_lock(); spi.configure(baudrate=8000000); spi.unlock()
    print("  SPI1 SCK=GP10 MOSI=GP11 @8MHz  PASS")
    spi.deinit()
except Exception as e:
    print("  SPI1 FAIL:", e); fails.append("SPI1")
try:
    import pwmio
    p = pwmio.PWMOut(board.GP13, frequency=20000, duty_cycle=32768)
    print("  PWM  GP13 (BL_PWM) @20kHz         PASS")
    p.deinit()
except Exception as e:
    print("  PWM GP13 FAIL:", e); fails.append("PWM13")
try:
    import rotaryio
    enc = rotaryio.IncrementalEncoder(board.GP0, board.GP1)
    print("  PIO  quadrature encoder GP0/GP1   PASS (position=%d)" % enc.position)
    enc.deinit()
except Exception as e:
    print("  PIO encoder FAIL:", e); fails.append("PIO")

print("\n--- Onboard WS2812 on GP16 ---")
led_ok = False
try:
    import neopixel_write
    np = digitalio.DigitalInOut(board.NEOPIXEL)
    np.direction = digitalio.Direction.OUTPUT
    for name, grb in (("red",(0,40,0)),("green",(40,0,0)),("blue",(0,0,40))):
        neopixel_write.neopixel_write(np, bytearray(grb))
        print("  WS2812 ->", name)
        time.sleep(0.6)
    led_ok = True
except Exception as e:
    print("  WS2812 FAIL:", e); fails.append("WS2812")

print("\n" + "="*58)
if fails:
    print("RESULT: FAIL -> " + ", ".join(fails))
    color = bytearray((0,40,0))     # red
else:
    print("RESULT: ALL PASS (%d/%d PCB pins good)" % (len(PINMAP), len(PINMAP)))
    color = bytearray((40,0,0))     # green
print("="*58)
print("LED now shows overall verdict: GREEN=pass RED=fail")
try:
    if led_ok:
        neopixel_write.neopixel_write(np, color)
except Exception:
    pass
while True:
    time.sleep(1)
