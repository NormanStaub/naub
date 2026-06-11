# V1 Build Outline — USB Audio Mixer

**Goal of V1:** a hand-wired, working prototype that validates the firmware, the
pin map, and the *feel* of the knobs — before committing anything to a custom
PCB or final enclosure. V1 is meant to be cheap, reworkable, and a little ugly.

> Companion docs: **`BOM.md`** (parts) · **`WIRING.md`** (pin-by-pin wiring) ·
> mounting notes in §6 below.

---

## 1. What this device is

A USB **composite device** built on a Raspberry Pi Pico 2 (RP2350):

- **System volume** → sent as USB **HID Consumer Control** → works on Windows,
  macOS, and Linux with **zero drivers**.
- **Per-app volume** → sent over **USB CDC serial** to a small host helper app
  that talks to the OS audio API (Windows WASAPI / Linux PipeWire / macOS via a
  virtual driver). *Host helper is a later phase — not needed to bring up V1.*

Four rotary encoders (turn = volume, press = mute/select), an e-paper display
showing the current level, and two aux buttons.

---

## 2. V1 scope — in vs. out

| In scope for V1 | Deferred |
|---|---|
| Hand-wired Pico 2 + 4 encoders + display + 2 buttons | Custom PCB (→ V2) |
| USB-C via Adafruit 4090 + pigtail | Raw RP2350 integration (→ V3) |
| Firmware: HID volume + encoder/display bring-up | Polished host helper for all 3 OSes |
| Prove knob feel & pin map | Final enclosure / aesthetics |

---

## 3. Parts (see `BOM.md` for full detail)

- Raspberry Pi Pico 2 (use the **H / header** version for prototyping)
- 4× Bourns PEC11R-4220F-S0024 encoders (+ spare)
- Waveshare 2.9" e-Paper Module **(V3)**
- Adafruit 4090 USB-C breakout + micro-USB pigtail
- 2× aux buttons — ⚠️ **not yet sourced**, pick a part if you want them in V1
- Debounce caps (10 nF / 100 nF) — ⚠️ **optional**, see §7

---

## 4. Tools & consumables you'll need

- Soldering iron + solder + flux
- Hookup wire (22–26 AWG stranded; a couple of colors helps a lot)
- Wire strippers, flush cutters, tweezers
- Multimeter (continuity + voltage — essential for catching wiring errors)
- Breadboard **or** perfboard:
  - **Breadboard** = fastest to iterate, good for first firmware bring-up
  - **Perfboard** = more durable, better for testing real knob feel in a fixture
- Heat-set insert tool (or a spare soldering iron tip) for the printed knobs
- 3D printer access for knobs + a test panel

---

## 5. Assembly sequence (recommended order)

Build incrementally and test after **each** stage — don't wire everything then
power on blind.

1. **Pico alone.** Plug in via its own USB, flash a blink/HID test, confirm the
   host sees a USB device. *(Establishes a known-good baseline.)*
2. **Display.** Wire the 8 display lines per `WIRING.md` §2. Run the Waveshare/
   GxEPD2 example. Confirm it draws and partial-refreshes.
3. **One encoder.** Wire Encoder 1 (GP0/GP1/GP2) per §3. Confirm rotation
   counts up/down cleanly and the push switch registers.
4. **Remaining encoders** (GP3–5, GP14–16, GP17–19), one at a time.
5. **Buttons** (GP6, GP7).
6. **USB-C input.** Wire 4090 → pigtail → Pico jack per §5. Verify it enumerates
   through the *external* port. **Power from one source only** (pigtail OR the
   Pico's own jack — never both).
7. **Knobs + test panel.** Mount encoders to a printed test panel, fit knobs,
   and evaluate the feel under realistic conditions.

> If anything misbehaves, fix it before adding the next part. A bug found at
> stage 3 is trivial; the same bug found at stage 6 is a hunt.

---

## 6. Mounting (V1 test panel)

The PEC11R is a **threaded-bushing panel mount** (ships with hex nut + lock
washer):

- Panel hole **~7.0–7.1 mm** for the **M7 × 0.75** bushing (the 6 mm figure is
  the *shaft*, not the hole).
- Encoder body sits **behind** the panel; washer + nut tighten from the **front**.
- Keep the printed panel wall **thin (~2–4 mm)** at the hole, or counter-bore a
  recess — the bushing is only ~7 mm long, so a thick wall won't let the nut bite.
- Check the datasheet drawing for an **anti-rotation lug**; if present, drill a
  matching small hole beside the main one.
- Knob slides onto the **6 mm D-shaft**; the **grub screw** clamps the flat
  (don't rely on a round press-fit — it slips).

For V1, nut-through-printed-panel is plenty rigid. (The rock-solid double-anchor
feel comes later with the PCB-as-faceplate in V2.)

---

## 7. Debounce caps — optional for V1

Not critical. Bounce can be handled in **firmware** instead. Build without caps
first; **add only if you see jitter** (miscounted steps, double-press):

- 10 nF: encoder A/B line → GND
- 100 nF: switch/button line → GND

They solder in after the fact with no rework, so they're not a blocker.

---

## 8. Open items / decisions before/while building

- [ ] **Aux button part** — choose one or drop buttons from V1
- [ ] **Caps** — decide whether to pre-place or defer (recommend defer)
- [ ] **Display firmware class** — confirm `GxEPD2_290_T94` vs
      `GxEPD2_290_GDEY029T94` against the V3 sticker before flashing
- [ ] **Breadboard vs perfboard** for the V1 substrate
- [ ] Verify **pin-1 orientation** on the Pico before wiring (silkscreen)

---

## 9. Next phase (after V1 works)

1. **Firmware proper:** HID consumer-control + CDC serial, PIO hardware
   quadrature decode (1 state machine per encoder), e-paper *update-on-settle /
   clear-on-idle* to minimize refreshes and panel wear.
2. **Host helper:** one text protocol, three OS adapters (Windows / Linux clean;
   macOS needs a virtual audio driver).
3. **V2 hardware:** custom carrier PCB (Pico module footprint, integrated USB-C +
   CC resistors, encoder/cap networks, display FPC, PCB-as-faceplate).

---

## References

- Pico 2 / RP2350 — <https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html>
- Waveshare 2.9" e-Paper Module — <https://www.waveshare.com/wiki/2.9inch_e-Paper_Module_Manual>
- Adafruit 4090 USB-C breakout — <https://www.adafruit.com/product/4090>
- Bourns PEC11R datasheet — <https://www.bourns.com/docs/Product-Datasheets/PEC11R.pdf>
- Reference project (per-app over serial) — deej: <https://github.com/omriharel/deej>
