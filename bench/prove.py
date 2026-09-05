import board, microcontroller, digitalio, time

mp = microcontroller.pin
print("\n" + "="*60)
print("IS board.* JUST AN ALIAS TABLE?")
print("="*60)
# If board.GPn is literally the same object as microcontroller.pin.GPIOn,
# then board.* is a name table and nothing more.
for n in (0, 10, 13, 16, 26):
    b = getattr(board, "GP%d" % n)
    m = getattr(mp, "GPIO%d" % n)
    print("  board.GP%-2d is microcontroller.pin.GPIO%-2d  -> %s   (%s)"
          % (n, n, b is m, type(b).__name__))
print("  type(mp.GPIO17) == type(board.GP0) ->", type(mp.GPIO17) is type(board.GP0))

BACK = [(17,"ENC4_A"),(18,"ENC4_B"),(19,"ENC4_SW"),
        (20,"BTN3"),(21,"BTN4"),(22,"BTN5"),(23,"ENC3_SW")]

print("\n" + "="*60)
print("OUTPUT DRIVER + INPUT BUFFER ON EACH BACK PAD")
print("="*60)
fails = []
for n, fn in BACK:
    p = getattr(mp, "GPIO%d" % n)
    d = digitalio.DigitalInOut(p)
    try:
        d.direction = digitalio.Direction.OUTPUT
        d.value = True;  time.sleep(0.002); rb_hi = d.value
        d.value = False; time.sleep(0.002); rb_lo = d.value
        # now the exact config the naub firmware uses for a switch:
        d.direction = digitalio.Direction.INPUT
        d.pull = digitalio.Pull.UP
        time.sleep(0.003)
        idle = d.value          # un-pressed switch idles high
        ok = rb_hi and not rb_lo and idle
        print("  GPIO%-2d %-8s drive H/L=%s/%s  input+pullup idle=%s  %s"
              % (n, fn, rb_hi, rb_lo, idle, "PASS" if ok else "FAIL"))
        if not ok: fails.append(n)
    finally:
        d.deinit()

print("\n" + "="*60)
print("REAL PERIPHERALS BOUND TO THE BACK PADS")
print("="*60)
try:
    import rotaryio
    e = rotaryio.IncrementalEncoder(mp.GPIO17, mp.GPIO18)   # ENC4 A/B
    print("  rotaryio.IncrementalEncoder(GPIO17, GPIO18)   PASS  pos=%d" % e.position)
    e.deinit()
except Exception as ex:
    print("  rotaryio FAIL:", ex); fails.append("rotaryio")
try:
    import keypad
    k = keypad.Keys((mp.GPIO19, mp.GPIO20, mp.GPIO21, mp.GPIO22, mp.GPIO23),
                    value_when_pressed=False, pull=True)
    time.sleep(0.05)
    print("  keypad.Keys(GPIO19..23) 5 switches         PASS  events=%d" % len(k.events))
    k.deinit()
except Exception as ex:
    print("  keypad FAIL:", ex); fails.append("keypad")
try:
    import pwmio
    w = pwmio.PWMOut(mp.GPIO23, frequency=1000, duty_cycle=32768)
    print("  pwmio.PWMOut(GPIO23)                       PASS")
    w.deinit()
except Exception as ex:
    print("  pwmio FAIL:", ex)

print("\n" + "="*60)
print(("VERDICT: FAIL -> %s" % fails) if fails else
      "VERDICT: all 7 back pads fully usable via microcontroller.pin")
print("="*60)
while True:
    time.sleep(1)
