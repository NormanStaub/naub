# naub V2 — board #1 bring-up log

**Date:** 2026-08-31 · **Board:** first V2 PCB, first RP2350-Tiny
**Procedure followed:** `BRINGUP.md` Stages 2–5
**Outcome:** all stages passed. Display and backlight working.

Module UID `01245632E3C8B66F` — record it; it is both the USB serial number and
the chip ID, so it identifies this specific board in any later log.

---

## What was verified, by stage

### Stage 2 — module (U1), before soldering

| Check | Result |
|---|---|
| Enumerates | VID `0x2e8a` / PID `0x0009`, serial `01245632E3C8B66F` |
| Silicon | RP2350A, revision **A4**, package QFN60, 4 MB flash, ARM + RISC-V |
| Chip ID vs USB serial | `0x01245632e3c8b66f` — match (not a relabelled part) |
| Takes firmware | BOOTSEL entered (PID → `0x000f`, bootrom v1.1), UF2 accepted, rebooted clean |
| All 27 PCB-used GPIO | pass pad test (pull-up reads 1, pull-down reads 0) |
| Peripherals on the real pin map | SPI1 GP10/GP11 @8 MHz, PWM GP13, PIO quadrature GP0/GP1 and GP17/GP18 |

The module shipped with Waveshare's factory jig firmware `RP2350_Tiny_Test`
(pico-sdk 2.2.0, built 2026-05-13). It prints `GPIO28 adc Error!!!!!!` on the
serial port with nothing attached — **that is the jig test missing its fixture,
not a fault.** The stock image is saved at `bench/` as a UF2 if it is ever wanted
back; it was replaced with CircuitPython 10.3.0 (`waveshare_rp2350_tiny`).

### Stage 2b — module soldered to the PCB

- No rail shorts and **no pin-to-pin bridges** across all 27 pins.
- `GP13` reads low against the internal pull-up. **This is correct** — see
  lesson 5 below. It drives cleanly to both rails, so it is not a short.

### Stage 3 — encoders and Chocs

- All 17 inputs idle HIGH; full bridge scan across every pair clean.
- All 4 encoders decode clean quadrature both directions. ENC1 and ENC2
  measured explicitly at **monotonic ratio 1.00, zero reverse counts, zero
  jumps** under PIO decode.
- All 9 switches register.

### Stage 4 — display (U2), the irreversible step

- Display bus GP8–GP12: no stuck pins, no bridges. Checked **before** anything
  drove the panel.
- ST7565R initialised with the EA DOGM128-6 "Single Supply 3.0/3.3 V" sequence
  (bias 1/9, booster 4×, `0x2F` power control, V0 ratio `0x27`, contrast `0x16`).
- Display working.

### Stage 5 — backlight

- Backlight solders to **6 pads of the DOGM128 footprint**: anodes A1–A3 on
  pads **1, 2, 3**; cathodes C1–C3 on pads **18, 19, 20**.
- Working.

---

## Lessons learned

### 1. CircuitPython's board definition omits GP17–GP23 — firmware must not use `board.*` for them

`board.*` on `waveshare_rp2350_tiny` (CP 10.3.0) exposes **GP0–GP16 and GP26–GP29
only**. `board.GP17` … `board.GP23` **do not exist**. Those are the module's back
solder-through pads, and on this board they carry **ENC4_A/B/SW, BTN3/4/5 and
ENC3_SW** — 7 of 17 inputs.

The silicon is fine; all 7 were verified working. Use `microcontroller.pin`:

```python
from microcontroller import pin as mp
ENC4_A, ENC4_B, ENC4_SW = mp.GPIO17, mp.GPIO18, mp.GPIO19
BTN3, BTN4, BTN5        = mp.GPIO20, mp.GPIO21, mp.GPIO22
ENC3_SW                 = mp.GPIO23
```

`board.GP0 is microcontroller.pin.GPIO0` evaluates **True** — `board` is nothing
but an alias table, so these are the same `Pin` objects and every API
(`digitalio`, `rotaryio`, `keypad`, `busio`, `pwmio`) accepts them identically.
Only CircuitPython is affected; in the C SDK or MicroPython these are ordinary
GPIO. **Action for V2 firmware:** the port of `firmware/code.py` must use
`microcontroller.pin` for those 7, alongside the already-noted ENC3_SW GP16→GP23
remap.

### 2. The CIRCUITPY drive is unwritable from both sides on this host

macOS enumerates the mass-storage interface and binds
`IOUSBMassStorageDriverNub`, but **never produces a block device** — so the drive
never mounts. Meanwhile CircuitPython refuses `storage.remount()` with
`Cannot remount path when visible via USB`, because from its side the host owns
the filesystem. Net result: **neither side can write it**, and drag-and-drop
firmware deployment does not work.

Workaround, and what all the bench tooling uses: **deploy over the REPL**
(`bench/deploy_repl.py`, or paste-mode directly). Unresolved at the hardware
level; re-flashing the CircuitPython UF2 via BOOTSEL is the likely fix if the
drive is wanted back.

### 3. A detentless encoder can legitimately rest with both contacts closed

PEC11L here is **detentless 20-PPR** (`PCB_DESIGN` §2). Both A and B closed is
one of the four legal quadrature states, and with the caps wired straight A→GND
and no series resistor, a closed contact is a **genuine dead short to ground**.
So an untouched encoder can read as two pins stuck low and be perfectly healthy.

ENC2 presented exactly this way and was briefly misread as a fault. **Establish
the resting baseline before treating a low pin as a defect** — nudge the shaft
and re-read.

### 4. A bridge scan must never skip the pins that read low

The obvious implementation excludes low-reading pins from the scan, both as
driver and as observer. That means **a bridge between two low pins is invisible**
— and low pins are exactly where a bridge is most likely. The first scan here
reported "no bridges" while never having tested the one pair that mattered
(ENC2_A ↔ ENC2_B).

`bench/stage3_test.py` now prompts to nudge the encoders until all inputs read
HIGH, re-scans with nothing excluded, and reports **`INCOMPLETE` naming the
untested pins** rather than a clean pass. A false "no bridges" immediately before
an irreversible step is the worst possible failure mode.

### 5. GP13 reading low with an internal pull-up is the backlight gate pulldown, not a short

The 10 kΩ gate→GND pulldown beats the RP2350's ~50–80 kΩ internal pull-up,
holding the pin at roughly 0.5 V. **Expected**, and positive confirmation that
the resistor is populated on the right net.

Distinguish a designed pulldown from a solder short by **driving the pin as an
output**: it drove cleanly to both rails, which a short to ground cannot do.

### 6. Do not poll GPIO from Python to judge quadrature

A CircuitPython poll loop over 5 pins runs at roughly **100 Hz**. Hand-rotation
quadrature phase is ~12 ms, so the phase offset **aliases away** and A/B appear
to move simultaneously — which looks exactly like a short between them. This
produced a false alarm on ENC2.

The giveaway was the median edge gap landing at exactly the loop period. Judge
quadrature only with the **PIO decoder** (`rotaryio.IncrementalEncoder`), which
catches every edge in hardware.

### 7. Re-pasting a REPL program fails with `GPn in use`

Ctrl-C stops a running loop but leaves its `rotaryio`/`digitalio` objects alive
and still holding the pins, so the next paste dies at construction. Every
reporter therefore opens by deinitialising anything a previous run left behind.
Symptom if missed: a UI that looks live but shows **0 fps**.

### 8. Assembly order — the backlight must be seated before the display is soldered

The LED55X46-A rests flat on the PCB **between the display's two pin rows**, so
once U2 is down the backlight cannot be inserted (`PCB_ACTION_PLAN` §1.2). It was
in place here, and only its 6 joints were outstanding — but soldering U2 with the
backlight absent would have meant removing the one part that cannot come off.
**Dry-fit both before U2 goes down.**

### 9. Process — establish the baseline before calling something a fault

Two of the findings above (ENC2's resting state, the aliased quadrature) were
raised as suspected defects before the normal behaviour was known. On a board
where the next step is irreversible, a false alarm is not free: it costs
rework decisions. **Measure what "healthy" looks like first**, then compare.

---

## Bench tooling

Everything used lives in [`bench/`](bench/) and is re-runnable on the next board.
All of it works over the REPL, so none of it needs the CIRCUITPY drive.

| Script | Use |
|---|---|
| `selftest.py`, `selftest2.py`, `prove.py` | Stage 2 — module acceptance before soldering |
| `live_inputs.py` | Live full-screen view of all 17 inputs, with coverage tracking |
| `stage3_test.py`, `run_stage3.sh` | Stage 3 — bridge scan, bounce timing, quadrature, fast-spin |
| `stage4_display.py`, `run_stage4.py` | Stage 4 — bus short check, ST7565 init, test patterns, contrast, backlight |
| `deploy_repl.py`, `reporter.py` | Write a local `.py` to the board without the drive |

**Fast-spin check is still outstanding.** `PCB_DESIGN` §2.1 flags a ~10 rev/s
ceiling on the A/B debounce RC; if counts drop *only* at speed the fix is a
10 nF → 4.7 nF cap swap, not a respin. `stage3_test.py` part C measures it.
