# PCB Design — V2 (electrical reference)

**Project:** naub — USB application/system audio mixer
**Controller:** Raspberry Pi Pico 2 (RP2350), non-H, flush castellated mount
**Companion docs:** [PCB_NOTES.md](PCB_NOTES.md) (mechanical/decisions) · [BOM_V2.md](BOM_V2.md) · [WIRING.md](WIRING.md) (V1 pin map, superseded below)
**Netlist:** [pcb/naub_v2.py](pcb/naub_v2.py) — this doc's connections as a runnable SKiDL script → `naub_v2.net` (KiCad). Validated: 41 parts, 45 nets, matches §1.

> Scope: the electrical bookkeeping that can be settled before layout — pin map,
> part values, current budget, decoupling. Hand this to KiCad as the connection
> truth. **Component placement / routing / outline are done in the GUI** (you).
> Every value here is computed or datasheet-sourced; the **one** item to transcribe
> rather than trust is the ST7565 charge-pump pad pairing (§4.2, flagged).

---

## 1. GPIO pin map (V2, validated against RP2350)

Pico 2 exposes **26 GPIO**: GP0–GP22 (23) + GP26–GP28 (3). GP23/24/25 are internal
(SMPS, VBUS sense, LED) and not on the castellated edge. SWCLK/SWDIO/RUN/BOOTSEL are
dedicated pins — they do **not** consume GPIO.

| GPIO | Phys pin | Net | Function | Notes |
|------|---------|-----|----------|-------|
| GP0  | 1  | ENC1_A   | Encoder 1 A | internal pull-up |
| GP1  | 2  | ENC1_B   | Encoder 1 B | internal pull-up |
| GP2  | 4  | ENC1_SW  | Encoder 1 switch | internal pull-up |
| GP3  | 5  | ENC2_A   | Encoder 2 A | internal pull-up |
| GP4  | 6  | ENC2_B   | Encoder 2 B | internal pull-up |
| GP5  | 7  | ENC2_SW  | Encoder 2 switch | internal pull-up |
| GP6  | 9  | BTN1     | Aux button 1 | internal pull-up |
| GP7  | 10 | BTN2     | Aux button 2 | internal pull-up |
| GP8  | 11 | LCD_A0   | Display data/command | (was "DC" on e-paper) |
| GP9  | 12 | LCD_CS   | Display chip select | SPI1 CSn (or GPIO) |
| GP10 | 14 | LCD_SCL  | Display SPI clock | **SPI1 SCK** (HW SPI) |
| GP11 | 15 | LCD_SI   | Display SPI data in | **SPI1 TX/MOSI** (HW SPI) |
| GP12 | 16 | LCD_RST  | Display reset | active low |
| GP13 | 17 | BL_PWM   | Backlight PWM enable | the freed ex-BUSY pin → 2N7002 gate |
| GP14 | 19 | ENC3_A   | Encoder 3 A | internal pull-up |
| GP15 | 20 | ENC3_B   | Encoder 3 B | internal pull-up |
| GP16 | 21 | ENC3_SW  | Encoder 3 switch | internal pull-up |
| GP17 | 22 | ENC4_A   | Encoder 4 A | internal pull-up |
| GP18 | 24 | ENC4_B   | Encoder 4 B | internal pull-up |
| GP19 | 25 | ENC4_SW  | Encoder 4 switch | internal pull-up |
| GP20 | 26 | BTN3     | Aux button 3 | internal pull-up |
| GP21 | 27 | BTN4     | Aux button 4 | internal pull-up |

**Used: 22 GPIO.** Encoders 12 · display 5 · backlight PWM 1 · aux buttons 4.

**Spare: 4 GPIO** — GP22 (digital), **GP26/GP27/GP28 (ADC-capable)**. Per PCB_NOTES,
reserve GP26–28 for a future fader/pot; that leaves GP22 as the one free digital pin.
If you'd rather max out buttons later, GP22 + GP26–28 give **8 aux buttons** total
(the pin-budget ceiling).

**Power/ground:**

| Pico pin | Phys | Use |
|----------|------|-----|
| `3V3(OUT)` | 36 | Logic + display VDD (3.3 V rail) |
| `VBUS` | 40 | **Do not connect** — power enters via the Pico's own USB jack (single path, per PCB_NOTES). Use VBUS only if you tap 5 V for the backlight; see §3. |
| `GND` | 3,8,13,18,23,28,33,38 | Common ground (tie several; solid pour both layers) |

### Validation notes (why this is electrically legal)
- **Hardware SPI1** lands correctly: SCK has options {GP10, GP14, GP26}, TX has
  {GP11, GP15, GP27}. GP10/GP11 are valid SPI1 SCK/TX — so the display can run on
  hardware SPI, not bit-bang. A0/CS/RST are plain GPIO (display is write-only, no MISO).
- **No double-assignment:** GP14/GP15 also *could* be SPI1, but here they're plain
  GPIO encoder inputs — fine, each pin has exactly one role.
- **PWM on GP13:** every RP2350 GPIO maps to a PWM slice (GP13 = slice 6B), so the
  backlight dim pin is valid.
- **BOOTSEL / SWD** are off the GPIO budget — break **SWCLK/SWDIO/GND** out to 3 test
  points or a 1×3 header (the flush Pico buries its SWD pads — PCB_NOTES open item).

---

## 2. Encoder debounce RC (carries over from V1 — with a caveat)

Per encoder: **2× 10 nF** (A, B → GND) + **1× 100 nF** (SW → GND), at the pins. Internal
pull-ups only (no discrete pull-ups). The cap + internal pull-up forms an RC low-pass.

RP2350 internal pull-up ≈ **50–80 kΩ**. Using 50 kΩ:

| Line | C | τ = RC | −3 dB ≈ 1/(2πRC) |
|------|-----|--------|------------------|
| A / B | 10 nF | ~500 µs | ~320 Hz |
| SW | 100 nF | ~5 ms | ~32 Hz |

- **SW (100 nF):** ideal — 5 ms swamps switch bounce, press events are slow. Keep.
- **A/B (10 nF): adequate but on the slow side.** With only the internal pull-up,
  τ ≈ 500 µs (up to ~800 µs at 80 kΩ). That's fine for hand-spin pulse periods (ms
  range), but this is a **detentless 18-PPR** encoder you may spin fast — the edges
  get rounded. **It will work; flag for the prototype:** if you see missed/!jittery
  counts at speed, either drop A/B to **4.7 nF** (τ ≈ 250 µs) or add **10 kΩ external
  pull-ups** on A/B (τ ≈ 100 µs, the classic Bourns/Alps figure). I'd lay out the
  footprints to allow the 10 kΩ option (DNP by default) so it's a no-respin fix.

---

## 3. Backlight — EA LED55X46-A (amber), series resistor + current

Datasheet figures (EA): **Vf ≈ 1.9 V**, drive current **~30 mA** (max rating ~40 mA),
EA's own recommended series resistors are **47 Ω @ 3.3 V** and **103 Ω @ 5 V** — both
land at ~30 mA, confirming Vf ≈ 1.9 V.

`R = (Vrail − Vf) / If`  ·  `P_R = (Vrail − Vf) × If`

| Rail | R (E12) | Resulting If | P in resistor | Resistor size |
|------|---------|--------------|---------------|---------------|
| **5 V (VBUS)** — recommended | **100–110 Ω** (use 100 Ω) | ~31 mA | ~96 mW | **0805, ≥⅛ W** (0.1 W is marginal — use ¼ W / 1206 for headroom) |
| 3.3 V (3V3OUT) | **47 Ω** | ~30 mA | ~42 mW | 0603, ⅛ W fine |

**Recommendation: drive the backlight from VBUS (5 V) with ~100 Ω**, not the 3.3 V rail.
Reasons: (1) keeps a ~30 mA load off the Pico's 3V3 regulator and out of the logic-rail
ripple; (2) VBUS is always present (bus-powered device). This *does* mean running one
wire from the Pico's VBUS pin to the board **for the backlight only** — that's a board
load, not the forbidden second *power-entry* path PCB_NOTES warns about (you're still
powering the Pico through its jack, not back-feeding it). If you'd rather keep VBUS off
the board entirely, the 3.3 V / 47 Ω row is fine — the regulator has the headroom (§5).

**PWM dimming (optional, committed-or-not in PCB_NOTES):** low-side **2N7002**, LED+ →
rail through R, LED− → MOSFET drain, source → GND, gate ← **GP13 (BL_PWM)** through a
~100 Ω series gate resistor with a 10 kΩ gate→source pulldown. 2N7002 is logic-level
(Vgs(th) ≤ 2.5 V) so 3.3 V drive fully enhances it at 30 mA. Drive **> 1 kHz** to avoid
flicker. For fixed brightness, omit the MOSFET + GP13 and tie LED− straight to GND.

---

## 4. Display power & the ST7565 charge pump

### 4.1 Logic decoupling
- DOG **VDD / VDD2 = 3.3 V**, with **100 nF** at the module pins.
- Rail bulk near the Pico 3V3OUT: **10 µF** + **100 nF**.

### 4.2 Charge-pump / voltage-multiplier caps — ⚠ transcribe, don't trust memory
The ST7565R generates its own LCD bias (no external high voltage), so a small network of
capacitors hangs off the booster and divider pins. **These must sit hard against the DOG
pads** — they carry the multiplied voltage and cannot be routed over any distance (this
is the whole reason the cable/connector plan was dropped).

**Value (settled): 1 µF ceramic, X7R/X5R, 16 V, 0603 or 0805 — one line item for all.**
(16 V because the multiplier node VOUT can reach ~13 V; standardizing avoids a mixed BOM.)

**Count (9× 1 µF) — corroborated by the Digi-Key symbol; exact CAP3 pairing still ⚠.**
Web sources agree DOGM128 3.3 V single-supply operation needs **9 external 1 µF caps**, and
the Digi-Key/SnapEDA schematic symbol for the part confirms every destination node exists:

| Group | Caps | Pins (per Digi-Key symbol) | Confirmed? |
|-------|------|------|------|
| Charge-pump stages | 3 × 1 µF | CAP1P↔CAP1N, CAP2P↔CAP2N, CAP3↔(VOUT or VSS) | pins present; **CAP3 far end ⚠** |
| Booster reservoir | 1 × 1 µF | VOUT → VSS | ✓ |
| LCD bias divider | 5 × 1 µF | V0, V1, V2, V3, V4 → VSS (each) | ✓ |

3 + 1 + 5 = **9** (supersedes the "~7" guess in PCB_NOTES). The separate VDD/VDD2 100 nF
(§4.1) is *not* one of these nine. The symbol also confirms the interface pins map to §1
(SI→GP11, SCL→GP10, A0→GP8, RST→GP12, CS1B→GP9; VDD/VDD2=3V3; VSS=GND). Backlight is NOT on
this part — it's the separate LED55X46-A board (§3).

> **⚠ STILL OPEN — confirm before fab (do not forget):** the symbol confirms the **count (9)**
> and the **6 unambiguous caps** (VOUT reservoir + V0–V4 bias). What's *not* yet read off the
> EA glass-specific schematic: the **CAP3 far-end node** (VOUT vs VSS) and the exact P/N
> partner per pump stage. **Action:** open the EA DOGM128-6 datasheet "Application 3.3 V
> single supply" page (issue 01.2026, in BOM_V2 sources) in a real PDF viewer and confirm the
> three pump-cap pairings. A wrong/missing pump cap = no or weak contrast. Everything else in
> this doc is computed/verified.

---

## 5. Current budget

**3.3 V rail (Pico 3V3OUT):**

| Load | Current |
|------|---------|
| RP2350 core + USB-FS active (firmware running) | ~25–50 mA |
| ST7565R logic + charge pump | < 2 mA |
| Backlight **if on 3.3 V** | ~30 mA |
| **Total (backlight on 3V3)** | **~60–85 mA** |
| **Total (backlight on VBUS)** | **~30–55 mA** |

The Pico's onboard switching regulator feeds both the RP2350 and the 3V3OUT pin and has
ample margin for this (raspberry-pi guidance is to keep *external* 3V3OUT draw modest —
~80 mA worst case here is comfortable). Either backlight choice is safe; VBUS just keeps
the logic rail cleaner.

**VBUS (5 V from USB):** whole device ≈ Pico (~50 mA) + backlight (~30 mA) + passives ≈
**< 100 mA**. Comfortably inside USB's 100 mA pre-enumeration floor, and trivial after the
composite HID+CDC device enumerates. No PTC strictly required; the optional VBUS PTC fuse
in PCB_NOTES is belt-and-suspenders. **No second power-entry path** — the only thing you
may tap from VBUS is the backlight rail (§3).

---

## 6. Passives summary (for the KiCad schematic / BOM)

| Ref group | Value | Qty | Where | Notes |
|-----------|-------|-----|-------|-------|
| Encoder A/B debounce | 10 nF C0G/X7R | 8 (2×4) | at each encoder A/B pin | 4.7 nF alt if fast-spin jitter |
| Encoder SW debounce | 100 nF X7R | 4 | at each encoder SW pin | |
| *(opt) A/B pull-ups* | 10 kΩ | 8 | A/B → 3V3 | **DNP** footprints — fast-spin insurance |
| Charge-pump network | 1 µF X7R 16 V | **9** | at DOG pads | §4.2 — count confirmed via Digi-Key symbol; ⚠ CAP3 pairing to verify off EA schematic |
| Display VDD decoupling | 100 nF | 1 | at DOG VDD | |
| Rail bulk | 10 µF | 1 | near 3V3OUT | |
| Rail decoupling | 100 nF | 1 | near 3V3OUT | |
| Backlight series R | 100 Ω (5 V) / 47 Ω (3.3 V) | 1 | backlight + leg | size per §3; ≥¼ W at 5 V |
| Backlight PWM FET | 2N7002 (SOT-23) | 1 | LED− low-side | only if dimming committed |
| FET gate R / pulldown | 100 Ω / 10 kΩ | 1 / 1 | GP13 gate / gate→GND | only with the FET |
| Aux buttons | — | 4 | GP6/7/20/21 | no diodes, no discrete pull-ups |

---

## 7. Still owned by you (GUI / mechanical — not in this doc)
- Board outline, control XY placement, mounting holes, fiducials (the layout you're on now).
- Footprint *physical* verification (EC11E bushing/pins, Choc, DOG, flush Pico).
- Z-clearance pass (STEP → Fusion).
- Routing, ground pours, trace widths (currents are tiny — no SI concerns at SPI speeds).
- BOOTSEL access method + SWD breakout placement.

---

## Sources
- RP2350 / Pico 2 pinout & GPIO function table — <https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html>
- EA DOGM128-6 datasheet (issue 01.2026, ST7565R, 3.3 V application) — <https://www.lcd-module.com/eng/pdf/grafik/dogm128e.pdf>
- EA LED55X46-A amber backlight (Vf ≈ 1.9 V, 47 Ω@3.3 V / 103 Ω@5 V) — <https://www.soselectronic.com/products/electronic-assembly/ea-led55x46-a-1-60682>
- Alps EC11E1834403 datasheet — see BOM_V2 sources
- 2N7002 (logic-level N-MOSFET) — standard SOT-23 part
</content>
</invoke>
