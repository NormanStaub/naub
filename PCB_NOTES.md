# PCB Design Notes — V2

**Project:** naub — USB application/system audio mixer
**Controller:** Raspberry Pi Pico 2 (RP2350), **non-H** (no headers)
**Board:** custom through-hole PCB, V2
**Companion doc:** [BOM_V2.md](BOM_V2.md) · [WIRING.md](WIRING.md) (V1 pin map carries over)

---

## Confirmed decisions

### Microcontroller mounting
- **Non-H Pico 2** (no pre-soldered headers), **soldered flush via castellated
  edge pads** — permanent, lowest profile (~4 mm above host PCB; the ~2.9 mm
  micro-USB jack is the height floor).
- Tucks **under the panel** (panel ≈ 6.5 mm above PCB), unlike a socketed/Dupont
  Pico (~10–11 mm) which would blow the height budget.
- Orient the Pico so its **micro-USB jack faces the back** (toward the 4090).
- **BOOTSEL access required** (permanent mount): panel hole over the button, or
  press-before-close. Design it in.
- Use a hybrid castellation + through-hole footprint so the edge can be hand
  drag-soldered.

### USB interface (off-board)
- **Adafruit 4090 USB-C breakout** (has the CC resistors) → short **micro-USB
  pigtail** → plugged into the **Pico's micro-USB jack**. Carries over from V1.
- **No USB nets on the PCB.** Data never touches the board; power enters through
  the Pico's jack.
- **Single power path** — do NOT also feed VBUS to Pico pin 40.
- 4090 is panel-mounted at the back (handled in enclosure CAD).
- Strain-relieve the micro plug in the jack (glue/clip) so it can't vibrate out.

### Encoders (×4)
- **Alps EC11E1834403**, board-mounted (PC pins to PCB).
- **Nut-secured to the top panel** (M7×0.75 bushing + nut + lock washer). This
  **sets the panel-to-PCB gap ≈ 6.5 mm** (encoder body shoulder height —
  *confirm on datasheet drawing*).
- **Knob:** no shortened cap needed — the long shaft already sits the knob above
  the panel. **Trim the metal D-shaft** to set final knob height.
- Debounce caps per encoder: **2× 10 nF (A/B) + 1× 100 nF (SW)**, placed right at
  the pins. Internal pull-ups (no discrete pull-ups).

### Aux buttons (Kailh Choc V1 linear, Red Pro 35 gf)
- **Populate 4** for now (of the 8 the pin budget allows).
- **Switches soldered directly to the board — no hot-swap sockets** (solder-only
  footprint). Note: permanent, so **audition the 35 gf feel on a loose switch
  before bulk-soldering** (each Choc is only 2 solder pins, but still).
- Board-mounted; **discrete GPIO inputs, internal pull-up, NO diodes** (not a
  matrix).
- Choc top ≈ **8 mm** above PCB → sits **~1.5 mm above the panel-bottom plane**.
- **Button cap must be EXTENDED** — a printed Choc-stem plunger bridging from the
  ~8 mm stem up through and above the panel (~+3–5 mm). Size the panel hole to
  clear the **cap + 3 mm of travel + upper switch body**.

### Pin budget — 8 aux buttons max
Pico 2 exposes **26 usable GPIO** (GP0–GP22, GP26–GP28).

| Load | GPIO |
|------|------|
| STN LCD (SI, SCL, CS, A0, RST = 5; + optional backlight PWM) | 5–6 |
| 4 encoders × (A, B, SW) | 12 |
| **Required subtotal** | **17–18** |
| **Free for aux buttons** | **8–9** (populating **4** for now) |

- BOOTSEL / RUN are separate pins — they don't reduce the 8.
- GP26–28 are ADC-capable: usable as buttons, or reserve for a future analog
  input (fader/pot).

### Power / passives
- Display + logic on **3.3 V** from the Pico's `3V3(OUT)`.
- **ST7565 charge-pump caps (~7× ~1 µF)** at the DOG pins (per EA reference
  schematic) — required for the LCD's on-chip boost; place tight to the module.
- **Backlight:** amber LED55X46-A via a **series resistor** (size from its Vf/If);
  run off **3.3 V or VBUS**. Budget ~10–150 mA.
- **Backlight dimming (optional):** **PWM via a low-side N-MOSFET** (2N7002) — LED+
  → rail through the series resistor, LED− → MOSFET drain, gate = PWM GPIO (~10 kΩ
  gate resistor), source → GND. Drive >~1 kHz to avoid flicker. Candidate pin: the
  **freed BUSY GPIO (GP13)**. Skip the MOSFET/pin entirely for fixed brightness.
- General **decoupling** (10 µF bulk + 100 nF) on the 3.3 V rail, plus a 100 nF at
  the display module.
- **Solid ground pour** both layers.

### Display — EA DOGM128W-6 amber STN LCD (replaces e-paper)
- **Chip-on-glass module soldered directly** into the PCB (2.54 mm pin row +
  backlight pins). **No display connector/cable** in the default build.
- **ST7565 charge-pump caps (~7× ~1 µF) must sit at the module pins** — local to
  the chip, *cannot* be routed over a cable. This is why the JST-PH 1×8 + cable
  plan is dropped. (Serviceable alt: DOG + caps on a carrier board, 1×8 cable to
  the main board — see BOM_V2 §Display.)
- SPI signals: **SI, SCL, CS, A0, RST** (no BUSY). **Backlight:** LED+ via a series
  resistor, LED− to GND; optional **PWM dimming** via a 2N7002 low-side switch on
  LED− (gate = a GPIO; the freed BUSY pin GP13 is the natural choice) — see
  *Power / passives*.
- Amber backlight **EA LED55X46-A** behind the glass; size the resistor from its
  Vf/If (3.3 V or VBUS rail).

### Decided to skip
- USB ESD / CC resistors on the PCB — handled by the 4090 / off-board.
- Key matrix diodes — N/A (discrete buttons).
- Pico socket / female headers — N/A (flush castellated mount).
- Display connector / cable — N/A by default (DOG solders directly; see Display).

---

## Open items — PCB design

- [ ] **Board outline + control placement** to match the enclosure (3 encoders
      across under the display + master to the right). Needs enclosure CAD first.
- [ ] **Confirm the panel-to-PCB gap**: encoder body height + bushing length +
      panel thickness, read off the EC11E dimensional drawing.
- [ ] **Placement of the 4 aux buttons** (count = 4 confirmed; positions TBD).
- [ ] **Reset button** (RUN→GND) — include or not.
- [ ] **BOOTSEL access** method — panel hole vs exposed pad/extension.
- [ ] **Display mounting** — solder DOG directly (default) vs DOG-on-carrier-board
      + 1×8 cable (serviceable). Decides whether any display connector exists.
- [ ] **Charge-pump cap network** — count/values/placement from EA DOG reference.
- [ ] **Backlight** — resistor value (LED55X46-A Vf/If), 3.3 V vs VBUS, PWM-dim pin?
- [ ] **Spare-GPIO breakout header** — include for future expansion? how many?
- [ ] **SPI series resistors** (~22–33 Ω on SCL/SI) — only relevant for the
      carrier-board cable; N/A for solder-direct.
- [ ] **ADC pins (GP26–28)** — reserve for a future fader/pot, or use as buttons.
- [ ] **Mounting strategy** — board held by encoder nuts alone vs extra standoffs;
      add dedicated **M2/M3 mounting holes** + **fiducials** for assembly either way.
- [ ] **SWD debug/program access** — the flush castellated Pico buries its SWD pads.
      Bring **SWCLK / SWDIO / GND** out to test points or a 3-pin header so the board
      can be reflashed/debugged without USB-BOOTSEL.
- [ ] **Layer count / thickness / finish** (assume 2-layer / 1.6 mm / HASL or ENIG
      unless decided otherwise).
- [ ] **Optional:** panel status LED; VBUS PTC fuse.
- [ ] **Z-clearance pass** — Choc (8 mm), encoder bodies, flush Pico (~4 mm),
      JST connector vs the enclosure lid.

## Open items — component sourcing

- [ ] **Switch Pico order from H → non-H** (no headers) for the flush mount.
- [ ] **Grease grade:** Nyogel 767A vs heavier 767B.
- [ ] **Aux button force:** confirm Red Pro 35 gf vs Pink 20 gf on a **loose
      switch** (soldered down = permanent, so decide before bulk order).
- [ ] **Aux buttons:** buy **4 + spares**.
- [ ] **Knob:** redesign/print for the 6 mm flat shaft (+ optional steel weights).
- [ ] **Extended Choc-stem button cap** — design + print (material: rigid vs TPU).
- [ ] **Display:** order **EA DOGM128W-6** (or DOGXL160W-7 for 160×104).
- [ ] **Amber backlight:** order **EA LED55X46-A**.
- [ ] **Fab the bare PCB** — 2-layer / 1.6 mm; pick fab house + finish + qty.
- [ ] **Charge-pump caps** (~7× ~1 µF) + **backlight series resistor** — pick parts.
- [ ] **Backlight PWM MOSFET** (2N7002 + ~10 kΩ gate) — only if dimming is committed.
- [ ] **Display decoupling** (bulk + 100 nF) — pick parts.
- [ ] **JST-PH header + cable** — only if going the carrier-board route (else drop).
- [ ] **Mounting hardware:** M7 nuts/washers (often bundled w/ encoder — confirm),
      standoffs, panel screws.
- [ ] **Confirm EC11E datasheet dims** (body height, bushing length, shaft length)
      for the final stack-up.
