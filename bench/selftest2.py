# Retest the back solder-through pads via microcontroller.pin (board.* omits them)
import board, digitalio, microcontroller, time

BACK = [("GPIO17","ENC4_A"),("GPIO18","ENC4_B"),("GPIO19","ENC4_SW"),
        ("GPIO20","BTN3"),("GPIO21","BTN4"),("GPIO22","BTN5"),("GPIO23","ENC3_SW")]
OTHER = [("GPIO16","onboard WS2812"),("GPIO24","mode-select NC"),("GPIO25","mode-select NC")]

def pulltest(pin):
    d = digitalio.DigitalInOut(pin)
    try:
        d.direction = digitalio.Direction.INPUT
        d.pull = digitalio.Pull.UP;   time.sleep(0.003); hi = d.value
        d.pull = digitalio.Pull.DOWN; time.sleep(0.003); lo = d.value
        return hi, lo
    finally:
        d.deinit()

print("\n" + "="*58)
print("RETEST: back solder-through pads via microcontroller.pin")
print("="*58)
names = sorted(n for n in dir(microcontroller.pin) if n.startswith("GPIO"))
print("microcontroller.pin exposes:", len(names), "GPIO ->", ", ".join(names))
print("board.* exposes:", len([n for n in dir(board) if n.startswith("GP")]), "GP names")

fails = []
print("\n--- naub back-pad assignments ---")
for pname, fn in BACK:
    try:
        hi, lo = pulltest(getattr(microcontroller.pin, pname))
        ok = (hi is True) and (lo is False)
        print("  %-7s %-10s up=%-5s dn=%-5s  %s" % (pname, fn, hi, lo, "PASS" if ok else "FAIL"))
        if not ok: fails.append(pname)
    except Exception as e:
        print("  %-7s %-10s ERROR %s: %s" % (pname, fn, type(e).__name__, e))
        fails.append(pname)

print("\n--- other on-die pins ---")
for pname, note in OTHER:
    try:
        hi, lo = pulltest(getattr(microcontroller.pin, pname))
        print("  %-7s %-16s up=%-5s dn=%-5s" % (pname, note, hi, lo))
    except Exception as e:
        print("  %-7s %-16s %s: %s" % (pname, note, type(e).__name__, e))

print("\n--- peripherals on the back pads ---")
try:
    import rotaryio
    e4 = rotaryio.IncrementalEncoder(microcontroller.pin.GPIO17, microcontroller.pin.GPIO18)
    print("  PIO quadrature ENC4 on GPIO17/18   PASS (position=%d)" % e4.position)
    e4.deinit()
except Exception as ex:
    print("  PIO ENC4 FAIL:", ex); fails.append("ENC4-PIO")

print("\n" + "="*58)
print(("RESULT: FAIL -> " + ", ".join(fails)) if fails else "RESULT: ALL BACK PADS PASS")
print("="*58)
while True:
    time.sleep(1)
