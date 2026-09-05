# naub V2 - STAGE 3 acceptance test: encoders + Chocs, display NOT yet fitted.
#
# Copy to CIRCUITPY/code.py, then watch the console:
#     screen /dev/cu.usbmodem101 115200      (ctrl-a k to quit)
#
# Covers BRINGUP.md Stage 3 in full:
#   - re-run of the short/bridge scan (you just soldered 9 more parts)
#   - all 17 inputs proved one at a time
#   - quadrature direction, both ways, on all 4 encoders
#   - fast-spin missed-count check  (PCB_DESIGN 2.1 RC-filter question)
#   - bounce/chatter measurement on all 9 switches
#
# Press ENTER on the console at any prompt to skip that step.

import time, sys, supervisor
import digitalio, rotaryio, keypad
from microcontroller import pin as mp

# ---- V2 pin map (PCB_DESIGN.md 1). GP17-23 are back pads: board.* omits them. ----
ENCODERS = [                       # name,  A,        B,        SW
    ("ENC1", mp.GPIO0,  mp.GPIO1,  mp.GPIO2),
    ("ENC2", mp.GPIO3,  mp.GPIO4,  mp.GPIO5),
    ("ENC3", mp.GPIO14, mp.GPIO15, mp.GPIO23),   # SW remapped GP16 -> GP23
    ("ENC4", mp.GPIO17, mp.GPIO18, mp.GPIO19),
]
BUTTONS = [("BTN1", mp.GPIO6), ("BTN2", mp.GPIO7), ("BTN3", mp.GPIO20),
           ("BTN4", mp.GPIO21), ("BTN5", mp.GPIO22)]
SWITCHES = [(e[0] + "_SW", e[3]) for e in ENCODERS] + BUTTONS         # 9 switches
ALL_INPUTS = ([(n + "_A", a) for n, a, b, s in ENCODERS] +
              [(n + "_B", b) for n, a, b, s in ENCODERS] + SWITCHES)  # 17 inputs

COUNTS_PER_REV = 20       # PEC11L is DETENTLESS 20-PPR (PCB_DESIGN 2).
                          # 20 pulses/rev x 4 edges / rotaryio divisor 4 = 20 counts/rev.
results = []
def record(name, ok, note=""):
    results.append((name, ok, note))
    print("      %s %s %s" % ("PASS" if ok else "FAIL", name, note))

def enter_pressed():
    if supervisor.runtime.serial_bytes_available:
        sys.stdin.read(1)
        return True
    return False

def banner(t):
    print("\n" + "=" * 62); print(t); print("=" * 62)

# ----------------------------------------------------------------- PART A
banner("PART A - short / bridge scan (you just soldered 9 parts)")
pins = {}
for n, p in ALL_INPUTS:
    d = digitalio.DigitalInOut(p)
    d.direction = digitalio.Direction.INPUT
    d.pull = digitalio.Pull.UP
    pins[n] = d
time.sleep(0.05)

def idle_low():
    time.sleep(0.05)
    return [n for n, _ in ALL_INPUTS if not pins[n].value]

def scan(skip):
    """Drive each input low, watch the others. Pins in `skip` are excluded BOTH
    as driver and as observer - which is exactly how a bridge can hide, so the
    caller must get `skip` down to empty before trusting a clean result."""
    found = []
    for n, _ in ALL_INPUTS:
        if n in skip:
            continue
        pins[n].direction = digitalio.Direction.OUTPUT
        pins[n].value = False
        time.sleep(0.015)
        for n2, _ in ALL_INPUTS:
            if n2 == n or n2 in skip:
                continue
            if not pins[n2].value:
                found.append((n, n2)); print("    BRIDGE %s <-> %s" % (n, n2))
        pins[n].direction = digitalio.Direction.INPUT
        pins[n].pull = digitalio.Pull.UP
        time.sleep(0.01)
    return found

low_at_idle = idle_low()
if low_at_idle:
    print("  low with pull-up: " + ", ".join(low_at_idle))
    print("  A detentless encoder can rest with a contact closed - that is normal,")
    print("  but those pins CANNOT be bridge-tested while they read low.")
    print("\n  >>> Nudge those encoders until all 17 read HIGH, then press ENTER.")
    t0 = time.monotonic()
    while time.monotonic() - t0 < 90:
        low_at_idle = idle_low()
        if not low_at_idle:
            print("  all 17 now HIGH - full scan possible"); break
        if enter_pressed():
            break
        time.sleep(0.1)

print("  driving each input low, watching the others...")
bridges = scan(set(low_at_idle))
if low_at_idle:
    print("  !! INCOMPLETE - these stayed low and were NOT bridge-tested:")
    print("     " + ", ".join(low_at_idle))
    print("     A bridge between any two of them would not be seen. Re-run.")
record("no pin-to-pin bridges", not bridges and not low_at_idle,
       ("%d found" % len(bridges)) if bridges else
       ("INCOMPLETE: %d pin(s) untestable" % len(low_at_idle)) if low_at_idle else "all 17 tested")
for d in pins.values():
    d.deinit()

# ----------------------------------------------------------------- PART B
banner("PART B - every switch, one at a time (9 switches)")
for name, p in SWITCHES:
    d = digitalio.DigitalInOut(p)
    d.direction = digitalio.Direction.INPUT
    d.pull = digitalio.Pull.UP
    time.sleep(0.02)
    if not d.value:
        record(name, False, "already LOW before you touched it - stuck or shorted")
        d.deinit(); continue
    print("\n  >>> PRESS AND RELEASE %s   (ENTER to skip)" % name)
    t0 = time.monotonic(); seen = False
    while time.monotonic() - t0 < 25:
        if not d.value:
            seen = True; break
        if enter_pressed():
            break
        time.sleep(0.002)
    if not seen:
        record(name, False, "no press detected (skipped or open circuit)")
        d.deinit(); continue
    # bounce: count transitions for 60 ms from first edge
    edges = 0; last = False; t_start = time.monotonic_ns(); t_last = t_start
    while (time.monotonic_ns() - t_start) < 60000000:
        v = d.value
        if v != last:
            edges += 1; last = v; t_last = time.monotonic_ns()
    bounce_ms = (t_last - t_start) / 1e6
    while not d.value:
        time.sleep(0.005)
    ok = edges <= 6 and bounce_ms < 25
    record(name, ok, "%d edges, settled in %.1f ms" % (edges, bounce_ms))
    d.deinit()

# ----------------------------------------------------------------- PART C
banner("PART C - quadrature: direction, then fast-spin count integrity")
for name, a, b, sw in ENCODERS:
    print("\n  --- %s ---" % name)
    try:
        enc = rotaryio.IncrementalEncoder(a, b)
    except Exception as e:
        record(name + " quadrature", False, "could not init: %s" % e); continue

    for want, label in ((+1, "CLOCKWISE"), (-1, "COUNTER-CLOCKWISE")):
        enc.position = 0
        print("      >>> rotate %s SLOWLY, one full turn (~%d counts)  [ENTER skips]"
              % (label, COUNTS_PER_REV))
        t0 = time.monotonic(); last = 0; idle = time.monotonic()
        while time.monotonic() - t0 < 40:
            p = enc.position
            if p != last:
                last = p; idle = time.monotonic()
            if abs(p) >= COUNTS_PER_REV or (p != 0 and time.monotonic() - idle > 2.5):
                break
            if enter_pressed():
                break
            time.sleep(0.005)
        p = enc.position
        if p == 0:
            record("%s %s" % (name, label), False, "no counts - check A/B joints")
        elif (p > 0) != (want > 0):
            record("%s %s" % (name, label), False,
                   "counted %d - direction INVERTED (swap A/B in firmware)" % p)
        else:
            slow = abs(p)
            record("%s %s" % (name, label), abs(slow - COUNTS_PER_REV) <= 2,
                   "%d counts, expected ~%d for one turn" % (p, COUNTS_PER_REV))

    # fast spin - the RC-filter question in PCB_DESIGN 2.1
    enc.position = 0
    print("      >>> now FLICK %s HARD, one full turn, as fast as you can  [ENTER skips]"
          % name)
    t0 = time.monotonic(); last = 0; idle = time.monotonic(); moved = False
    while time.monotonic() - t0 < 40:
        p = enc.position
        if p != last:
            last = p; idle = time.monotonic(); moved = True
        if moved and time.monotonic() - idle > 2.0:
            break
        if enter_pressed():
            break
        time.sleep(0.002)
    fast = abs(enc.position)
    if not moved:
        record(name + " fast-spin", False, "no counts")
    else:
        lost = COUNTS_PER_REV - fast
        record(name + " fast-spin", lost <= 2,
               "%d counts vs %d expected (%d missed)" % (fast, COUNTS_PER_REV, lost)
               + ("  -> RC filter: try 10nF -> 4.7nF" if lost > 2 else ""))
    enc.deinit()

# ----------------------------------------------------------------- SUMMARY
banner("SUMMARY")
bad = [r for r in results if not r[1]]
for n, ok, note in results:
    print("  %-28s %s  %s" % (n, "PASS" if ok else "FAIL", note))
print("-" * 62)
if bad:
    print("  %d FAILURE(S) - DO NOT FIT THE DISPLAY YET" % len(bad))
    for n, _, note in bad:
        print("    - %s  %s" % (n, note))
else:
    print("  ALL CLEAR - Stage 3 complete. Safe to proceed to Stage 4 (display).")
print("=" * 62)

# ----------------------------------------------------------------- LIVE
banner("LIVE MONITOR - poke anything; ctrl-c to stop")
keys = keypad.Keys([p for _, p in SWITCHES], value_when_pressed=False, pull=True)
encs = [(n, rotaryio.IncrementalEncoder(a, b)) for n, a, b, _ in ENCODERS]
names = [n for n, _ in SWITCHES]
last = {n: 0 for n, _ in encs}
while True:
    ev = keys.events.get()
    if ev:
        print("  %-10s %s" % (names[ev.key_number], "DOWN" if ev.pressed else "up"))
    for n, e in encs:
        if e.position != last[n]:
            print("  %-10s %+d  (pos %d)" % (n, e.position - last[n], e.position))
            last[n] = e.position
    time.sleep(0.005)
