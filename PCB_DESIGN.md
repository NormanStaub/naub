# PCB Design — V2 (electrical reference)

**Project:** naub — USB application/system audio mixer
**Controller:** ~~Raspberry Pi Pico 2 (RP2350), non-H, flush castellated mount~~ → **Waveshare RP2350-Tiny** (2026-07-15; doc body reworked 2026-07-16)
**Companion docs:** [PCB_NOTES.md](PCB_NOTES.md) (mechanical/decisions) · [BOM_V2.md](BOM_V2.md) · [WIRING.md](WIRING.md) (V1 pin map, superseded below)
**Netlist:** [pcb/naub_v2.py](pcb/naub_v2.py) — this doc's connections as a runnable SKiDL script → `naub_v2.net` (KiCad). Validated: 42 parts, 48 nets, matches §1. All footprints are real (KiCad stock libs + `pcb/naub.pretty`, incl. the new `naub:Waveshare_RP2350-Tiny_Hybrid`); import-ready.

> Scope: the electrical bookkeeping that can be settled before layout — pin map,
> part values, current budget, decoupling. Hand this to KiCad as the connection
> truth. **Component placement / routing / outline are done in the GUI** (you).
> Every value here is computed or datasheet-sourced, including the ST7565
> charge-pump pad pairing (§4.2 — resolved off the EA app schematic).

---

## 0. ⚠ Controller change (2026-07-15): Pico 2 → Waveshare RP2350-Tiny — rework status

> The Pico 2 + its onboard USB jack don't fit the final layout. Replaced by the
> **Waveshare RP2350-Tiny**: same RP2350A silicon, USB split onto a separate
> 18×18 mm **panel-mount USB-C adapter** over an 8-pin 0.5 mm FPC (~15 cm, kit).
> The SPI1/PWM pin-function validation below carries over unchanged (same chip).
> **Rework done 2026-07-16** — doc body, footprint and netlist now describe the
> Tiny. Remaining items are flagged ⏳.

- [x] **§1 pin map:** ENC3_SW moved **GP16 → GP23** (GP16 drives the Tiny's onboard
      WS2812 — treat as spent; free status LED). **GP24/GP25 are unavailable** (NC
      mode-select pads). Everything else maps 1:1. GP22 stays the spare digital,
      GP26–28 fader reserve intact, **GP29 is an extra spare ADC**. GP27/28/29 have
      **47 Ω series resistors on-module** — negligible for encoders/buttons/ADC.
- [x] **§3 backlight rail:** VBUS → **VSYS** (USB 5 V arrives over the FPC; VSYS is
      the top-left edge contact). 3× 300 Ω unchanged.
- [x] **§5 power:** 3V3 from the Tiny's ME6217C33M5G LDO (800 mA). Budget unchanged
      (<100 mA total) — comfortable.
- [x] **Module footprint** → `pcb/naub.pretty/Waveshare_RP2350-Tiny_Hybrid.kicad_mod`
      (generated 2026-07-16). Hybrid mount, all 36 contacts hand-solderable:
      **(a)** 23 castellation finger pads (1.4 mm wide, 2.54 mm pitch) extending
      1.2 mm past the module outline for iron fillets;
      **(b)** 23 Ø1.0 mm plated holes under the module's own through-holes
      (Ø0.75 mm, 1.38 mm inboard sides / 1.59 mm bottom) — header pins through both
      boards as an alignment jig, optional top-down solder-through;
      **(c)** 13 **solder-through holes** (drill 1.0 mm) under the back pads
      (1.5 mm squares) — **GND + GP16–19** column (power-edge side), **3V3 + GP23–20**
      column (GP0-edge side), **SWD DIO/GND/CLK** row — soldered from the carrier
      underside into the module pads. Module back is flat. Pads are *named* (GP0…,
      3V3, GND, VSYS, SWCLK, SWDIO), matching the netlist part. Prefer ENIG.
      Geometry cross-verified from the Waveshare **STEP** (all 23 hole centers),
      **DXF** (pad copper sizes), **schematic** (P1/P2/P3 nets) and **product
      photos** (per-contact signal assignment).
- [x] **SWD / BOOTSEL open items (PCB_NOTES):** both resolved by the Tiny — the 3 SWD
      solder-through holes route to J1 (1×3 header); BOOT + RESET buttons live on the
      USB adapter at the panel.
- [x] **Netlist (`pcb/naub_v2.py`):** RP2350-Tiny part on the new footprint, GP23
      remap + VSYS backlight rail + GP29 added to J2. Regenerated `naub_v2.net`:
      **42 parts, 48 nets** (was 47 — GP29 is the extra), 0 errors.
- [ ] ⏳ **Pre-fab check — SWD row position:** the official DXF places the SWD trio
      mid-board, but the wiki render **and** the product photos show it **1.08 mm
      below the FPC edge** (photo-measured, ±0.15 mm) — the footprint uses the
      photo position (front view: DIO left / GND / CLK right). Before ordering,
      **1:1-print the footprint and lay the physical module on it**; if your module
      has the trio mid-board (older rev?), shift those 3 holes.
- [ ] ⏳ **USB adapter mechanical (enclosure CAD):** 18 × 18 mm, 4× M2 holes on a
      14 × 14 mm grid (2 mm from edges), panel-mount behind a USB-C cutout. Route the
      FPC ribbon away from encoder wiring (it carries the USB-FS pair + VSYS).
- [ ] ⏳ **Firmware (at V2 bring-up):** ENC3_SW GP16 → GP23 in the `sws` list of
      `firmware/code.py` (don't touch it now — the V1 device still runs on the Pico).
      The Tiny has an official CircuitPython build (`waveshare_rp2350_tiny`).

Sources: [product page](https://www.waveshare.com/rp2350-tiny.htm) ·
[schematic](https://files.waveshare.com/wiki/RP2350-Tiny/RP2350_Tiny_Schematic.pdf) ·
[DXF](https://files.waveshare.com/wiki/RP2350-Tiny/RP2350_Tiny_DXF.zip) ·
[STEP](https://files.waveshare.com/wiki/RP2350-Tiny/RP2350_Tiny_STEP.zip) ·
[wiki/pinout](https://www.waveshare.com/wiki/RP2350-Tiny) — geometry re-derived
2026-07-16 from STEP hole centers + DXF pad outlines + straight-on product photos
(23 edge TH Ø0.75 @ 2.54 mm; back pads 1.5 mm sq in two 5-pad columns at ±5.4 mm
off-center + 3-pad SWD row near the FPC edge).

---

## 1. GPIO pin map (V2, validated against RP2350 / RP2350-Tiny)

The RP2350-Tiny brings out **28 GPIO**: GP0–GP15 + GP26–GP29 on the 23-contact
**edge ring** (with 3V3 / GND / VSYS), and GP16–GP23 on **back solder-through pads**.
**GP16 is spent** on the module's onboard WS2812; **GP24/GP25 don't exist** off-module
(NC mode-select pads) → **27 usable GPIO**. SWD is two dedicated back pads (+GND);
BOOT/RESET buttons live on the USB-C adapter — none of these consume GPIO.

Module pad key (front view, FPC/USB edge up — matches the footprint):
**L1–L9** = left edge top→bottom (VSYS, GND, 3V3, GP29, GP28, GP27, GP26, GP15, GP14) ·
**R1–R9** = right edge top→bottom (GP0…GP8) · **B1–B5** = bottom edge left→right
(GP13…GP9) · **back-G / back-V** = the GND+GP16-19 / 3V3+GP23-20 solder-through columns.

| GPIO | Module pad | Net | Function | Notes |
|------|-----------|-----|----------|-------|
| GP0  | edge R1 | ENC1_A   | Encoder 1 A | internal pull-up |
| GP1  | edge R2 | ENC1_B   | Encoder 1 B | internal pull-up |
| GP2  | edge R3 | ENC1_SW  | Encoder 1 switch | internal pull-up |
| GP3  | edge R4 | ENC2_A   | Encoder 2 A | internal pull-up |
| GP4  | edge R5 | ENC2_B   | Encoder 2 B | internal pull-up |
| GP5  | edge R6 | ENC2_SW  | Encoder 2 switch | internal pull-up |
| GP6  | edge R7 | BTN1     | Aux button 1 | internal pull-up |
| GP7  | edge R8 | BTN2     | Aux button 2 | internal pull-up |
| GP8  | edge R9 | LCD_A0   | Display data/command | (was "DC" on e-paper) |
| GP9  | edge B5 | LCD_CS   | Display chip select | SPI1 CSn (or GPIO) |
| GP10 | edge B4 | LCD_SCL  | Display SPI clock | **SPI1 SCK** (HW SPI) |
| GP11 | edge B3 | LCD_SI   | Display SPI data in | **SPI1 TX/MOSI** (HW SPI) |
| GP12 | edge B2 | LCD_RST  | Display reset | active low |
| GP13 | edge B1 | BL_PWM   | Backlight PWM enable | the freed ex-BUSY pin → 2N7002 gate |
| GP14 | edge L9 | ENC3_A   | Encoder 3 A | internal pull-up |
| GP15 | edge L8 | ENC3_B   | Encoder 3 B | internal pull-up |
| GP16 | —       | *(none)* | **unusable** | drives the module's onboard WS2812 |
| GP17 | back-G  | ENC4_A   | Encoder 4 A | internal pull-up |
| GP18 | back-G  | ENC4_B   | Encoder 4 B | internal pull-up |
| GP19 | back-G  | ENC4_SW  | Encoder 4 switch | internal pull-up |
| GP20 | back-V  | BTN3     | Aux button 3 | internal pull-up |
| GP21 | back-V  | BTN4     | Aux button 4 | internal pull-up |
| GP23 | back-V  | ENC3_SW  | Encoder 3 switch | **was GP16 on the Pico map** |

**Used: 22 GPIO.** Encoders 12 · display 5 · backlight PWM 1 · aux buttons 4.

**Spare: 5 GPIO** — GP22 (back-V, digital), **GP26/GP27/GP28/GP29 (ADC-capable,
edge)**. Per PCB_NOTES, reserve GP26–28 for a future fader/pot; GP29 is a bonus ADC
the Pico kept internal. GP27/28/29 pass through **47 Ω on-module series resistors**
(irrelevant for buttons/pots). Max-buttons ceiling is now **9** (4 populated +
GP22 + GP26–29).

**Power/ground:**

| Module contact | Where | Use |
|----------------|-------|-----|
| `3V3` | edge L3 + back-V top | Logic + display VDD, from the module's ME6217C33M5G LDO (800 mA) |
| `VSYS` | edge L1 | **USB 5 V**, arrives over the adapter FPC → backlight rail only (§3) |
| `GND` | edge L2 + back-G top + SWD row middle | Common ground (tie all; solid pour both layers) |

### Validation notes (why this is electrically legal)
- **Hardware SPI1** lands correctly: SCK has options {GP10, GP14, GP26}, TX has
  {GP11, GP15, GP27}. GP10/GP11 are valid SPI1 SCK/TX — so the display can run on
  hardware SPI, not bit-bang. A0/CS/RST are plain GPIO (display is write-only, no MISO).
- **No double-assignment:** GP14/GP15 also *could* be SPI1, but here they're plain
  GPIO encoder inputs — fine, each pin has exactly one role.
- **PWM on GP13:** every RP2350 GPIO maps to a PWM slice (GP13 = slice 6B), so the
  backlight dim pin is valid. ENC3_SW on GP23 is a plain GPIO input — no constraint.
- **BOOTSEL / SWD** are off the GPIO budget — the module's SWCLK/SWDIO/GND back pads
  route to **J1 (1×3 header)** via the solder-through holes; BOOT/RESET buttons are
  on the USB-C adapter (both old PCB_NOTES open items resolved).

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
  range), but this is a **detentless 20-PPR** encoder (PEC11L) you may spin fast — the edges
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

> **Datasheet reality check (netlist now reflects this):** the LED55X46 backlights have
> **3 independent LED paths** (anodes A1–A3 = display footprint pads 1–3, cathodes
> C1–C3 = pads 18–20 — the backlight solders **through the DOGM128 footprint**, it is
> not a separately-placed part), and EA's resistor figures are **per path**. So: one
> series resistor per anode, three total.

| Rail | R per path (×3) | Resulting If | P per resistor | Resistor size |
|------|---------|--------------|---------------|---------------|
| **5 V (VSYS)** — recommended | **300 Ω** | ~10 mA/path, ~31 mA total | ~32 mW | **0603** fine |
| 3.3 V (module 3V3) | 140 Ω | ~10 mA/path, ~30 mA total | ~14 mW | 0603 fine |

**Recommendation: drive the backlight from VSYS (USB 5 V) with 3× 300 Ω**, not the
3.3 V rail. Reasons: (1) keeps a ~30 mA load off the module's 3V3 LDO and out of the
logic-rail ripple; (2) VSYS is always present (bus-powered device). On the RP2350-Tiny
this is trivial to route: USB 5 V arrives over the adapter FPC and lands on the
module's **VSYS edge contact (L1)** — tapping it on the carrier is an ordinary board
load, no second power-entry path involved (the old Pico-jack caveat in PCB_NOTES is
obsolete). If you'd rather keep 5 V off the board entirely, the 3.3 V / 140 Ω row is
fine — the LDO has the headroom (§5).

**PWM dimming (optional, committed-or-not in PCB_NOTES):** low-side **2N7002**, LED+ →
rail through R, LED− → MOSFET drain, source → GND, gate ← **GP13 (BL_PWM)** through a
~100 Ω series gate resistor with a 10 kΩ gate→source pulldown. 2N7002 is logic-level
(Vgs(th) ≤ 2.5 V) so 3.3 V drive fully enhances it at 30 mA. Drive **> 1 kHz** to avoid
flicker. For fixed brightness, omit the MOSFET + GP13 and tie LED− straight to GND.

---

## 4. Display power & the ST7565 charge pump

### 4.1 Logic decoupling
- DOG **VDD / VDD2 = 3.3 V**, with **100 nF** at the module pins.
- Rail bulk near the Tiny's 3V3 contacts: **10 µF** + **100 nF**.

### 4.2 Charge-pump / voltage-multiplier caps — ⚠ transcribe, don't trust memory
The ST7565R generates its own LCD bias (no external high voltage), so a small network of
capacitors hangs off the booster and divider pins. **These must sit hard against the DOG
pads** — they carry the multiplied voltage and cannot be routed over any distance (this
is the whole reason the cable/connector plan was dropped).

**Value (settled): 1 µF ceramic, X7R/X5R, 16 V, 0603 or 0805 — one line item for all.**
(16 V because the multiplier node VOUT can reach ~13 V; standardizing avoids a mixed BOM.)

**Count (9× 1 µF) — CONFIRMED against the EA datasheet application schematic.**
Read off the EA DOGM128-6 datasheet (issue 01.2026) p.4 **"Single Supply 3,0/3,3 V"**
application example:

| Group | Caps | Pins (per EA app schematic) | Confirmed? |
|-------|------|------|------|
| Charge-pump stages | 3 × 1 µF | CAP1P(29)↔CAP1N(30), CAP2P(28)↔CAP2N(27), **CAP3P(31)↔CAP1N(30)** | ✓ |
| Booster reservoir | 1 × 1 µF | VOUT(32) → GND | ✓ |
| LCD bias divider | 5 × 1 µF | V0–V4 (21–25) → GND (each) | ✓ |

3 + 1 + 5 = **9** (supersedes the "~7" guess in PCB_NOTES). The separate VDD/VDD2 100 nF
(§4.1) is *not* one of these nine. The datasheet pin table also confirms the interface map in §1
(SI=36→GP11, SCL=37→GP10, A0=38→GP8, RST=39→GP12, CS1B=40→GP9; VDD(35)/VDD2(34)=3V3;
VSS(26,33)=GND). Backlight pins pass **through this same footprint** (pads 1–3/18–20, §3).

> **RESOLVED (was the pre-fab ⚠):** the CAP3 far end is **CAP1N**, not VOUT and not VSS —
> the pump chain is 2× 1 µF stacked CAP3P–CAP1N and CAP1N–CAP1P, plus CAP2P–CAP2N.
> The netlist (`pcb/naub_v2.py`) encodes exactly this.

---

## 5. Current budget

**3.3 V rail (RP2350-Tiny's ME6217C33M5G LDO, 800 mA max):**

| Load | Current |
|------|---------|
| RP2350 core + USB-FS active (firmware running) | ~25–50 mA |
| ST7565R logic + charge pump | < 2 mA |
| Backlight **if on 3.3 V** | ~30 mA |
| **Total (backlight on 3V3)** | **~60–85 mA** |
| **Total (backlight on VSYS)** | **~30–55 mA** |

The module's LDO feeds both the RP2350 and the 3V3 contacts, with 800 mA of headroom —
~85 mA worst case here is nothing. Dissipation is equally trivial: (5 V − 3.3 V) ×
85 mA ≈ 0.14 W. Either backlight choice is safe; VSYS just keeps the logic rail cleaner.
(One LDO nuance vs the Pico's SMPS: dropout, not efficiency, is the limit — irrelevant
at USB voltages.)

**VSYS (USB 5 V over the adapter FPC):** whole device ≈ module (~50 mA) + backlight
(~30 mA) + passives ≈ **< 100 mA**. Comfortably inside USB's 100 mA pre-enumeration
floor, and trivial after the composite HID+CDC device enumerates. No PTC strictly
required; the optional PTC fuse in PCB_NOTES is belt-and-suspenders. Power and data
both enter through the FPC — there is no other power path.

---

## 6. Passives summary (for the KiCad schematic / BOM)

| Ref group | Value | Qty | Where | Notes |
|-----------|-------|-----|-------|-------|
| Encoder A/B debounce | 10 nF C0G/X7R | 8 (2×4) | at each encoder A/B pin | 4.7 nF alt if fast-spin jitter |
| Encoder SW debounce | 100 nF X7R | 4 | at each encoder SW pin | |
| *(opt) A/B pull-ups* | 10 kΩ | 8 | A/B → 3V3 | **DNP** footprints — fast-spin insurance |
| Charge-pump network | 1 µF X7R 16 V | **9** | at DOG pads | §4.2 — pairings confirmed off EA app schematic (CAP3P↔CAP1N) |
| Display VDD decoupling | 100 nF | 1 | at DOG VDD | |
| Rail bulk | 10 µF | 1 | near 3V3OUT | |
| Rail decoupling | 100 nF | 1 | near 3V3OUT | |
| Backlight series R | 300 Ω (5 V) / 140 Ω (3.3 V) | **3** (one per LED path) | VSYS → A1/A2/A3 | per §3; 0603 fine (~32 mW each) |
| Backlight PWM FET | 2N7002 (SOT-23) | 1 | LED− low-side | only if dimming committed |
| FET gate R / pulldown | 100 Ω / 10 kΩ | 1 / 1 | GP13 gate / gate→GND | only with the FET |
| Aux buttons | — | 4 | GP6/7/20/21 | no diodes, no discrete pull-ups |

---

## 7. Still owned by you (GUI / mechanical — not in this doc)
- Board outline, control XY placement, mounting holes, fiducials (the layout you're on now).
- Footprint *physical* verification (PEC11L bushing/pins, Choc, DOG, **RP2350-Tiny
  hybrid**) — footprints now exist (`pcb/naub.pretty` + stock libs); sanity-check the
  first fab print at 1:1 against real parts. For the Tiny, lay the actual module on
  the print — especially the **SWD trio** (see §0 pre-fab check).
- Z-clearance pass (STEP → Fusion). The Tiny sits back-flat on the carrier
  (components top-side only); the USB adapter is a separate panel-mounted board.
- Routing, ground pours, trace widths (currents are tiny — no SI concerns at SPI
  speeds). Keep the FPC ribbon path away from encoder wiring.
- SWD breakout header (J1) placement. (BOOTSEL is solved — button on the adapter.)

---

## Sources
- Waveshare RP2350-Tiny — product/wiki/schematic/DXF/STEP links in §0
- RP2350 / Pico 2 pinout & GPIO function table — <https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html>
- EA DOGM128-6 datasheet (issue 01.2026, ST7565R, 3.3 V application) — <https://www.lcd-module.com/eng/pdf/grafik/dogm128e.pdf>
- EA LED55X46-A amber backlight (Vf ≈ 1.9 V, 47 Ω@3.3 V / 103 Ω@5 V) — <https://www.soselectronic.com/products/electronic-assembly/ea-led55x46-a-1-60682>
- Bourns PEC11L datasheet (REV. 04/26) — <https://www.bourns.com/docs/Product-Datasheets/PEC11L.pdf>
- 2N7002 (logic-level N-MOSFET) — standard SOT-23 part
</content>
</invoke>
