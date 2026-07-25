# PCB Design — V2 (electrical reference)

**Project:** naub — USB application/system audio mixer
**Controller:** ~~Raspberry Pi Pico 2 (RP2350), non-H, flush castellated mount~~ → **Waveshare RP2350-Tiny** (2026-07-15; doc body reworked 2026-07-16)
**Companion docs:** [PCB_NOTES.md](PCB_NOTES.md) (mechanical/decisions) · [BOM_V2.md](BOM_V2.md) · [WIRING.md](WIRING.md) (V1 pin map, superseded below)
**Netlist:** [pcb/naub_v2.py](pcb/naub_v2.py) — this doc's connections as a runnable SKiDL script → `naub_v2.net` (KiCad). Validated: **56 parts, 50 nets** (re-counted from the generated netlist 2026-07-24), matches §1. *The functional circuit is 42 parts / 44 nets; the other 14 parts / 6 nets are the bring-up hooks added 2026-07-24 — see §7 and [BRINGUP.md](BRINGUP.md).* All footprints are real (KiCad stock libs + `pcb/naub.pretty`, incl. the new `naub:Waveshare_RP2350-Tiny_Hybrid`); import-ready.

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
      **42 parts, 44 nets**, 0 errors. (This line read "48 nets" until 2026-07-24; that
      figure predated dropping the J2 expansion header, which removed its four GP26-29
      ADC nets. Part count is unchanged because J2 (−1) was traded for SW9/BTN5 (+1).)
      **Now 56 / 50** — the bring-up hooks (§7) added 14 parts and 6 nets later the
      same day. The 42 / 44 figure above is still the correct count of the *functional*
      circuit; nothing in it changed.
- [x] **Bring-up hooks (2026-07-24):** 10 test pads, 2 rail-cut links, 2 unpopulated
      debug rows added to the netlist before the first fab order — §7, procedure in
      [BRINGUP.md](BRINGUP.md).
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
      Also: V2 has **5** aux buttons (BTN1–5 on GP6/7/20/21/22), not V1's set.
      The Tiny has an official CircuitPython build (`waveshare_rp2350_tiny`).
- [ ] ⏳ **Encoder fast-spin check (at V2 bring-up):** spin each knob hard and confirm
      counts don't drop. The A/B debounce RC has a ~10 rev/s ceiling and **no external
      pull-up footprints exist by design** — if counts are missed *only at speed*, the
      fix is a 10 nF → 4.7 nF cap swap or PIO/state-machine decoding, **not** a respin.
      Full reasoning and fallback order: **§2.1**.

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
| GP22 | back-V  | BTN5     | Aux button 5 | internal pull-up; ex-"spare digital" |
| GP23 | back-V  | ENC3_SW  | Encoder 3 switch | **was GP16 on the Pico map** |

**Used: 23 GPIO.** Encoders 12 · display 5 · backlight PWM 1 · aux buttons 5.

**Spare: 4 GPIO** — **GP26/GP27/GP28/GP29 (ADC-capable, edge)**. Per PCB_NOTES, reserve
GP26–28 for a future fader/pot; GP29 is a bonus ADC the Pico kept internal. GP27/28/29
pass through **47 Ω on-module series resistors** (irrelevant for buttons/pots).
Max-buttons ceiling is **9** (5 populated + GP26–29).

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
  range), but this is a **detentless 20-PPR** encoder (PEC11L) you may spin fast — the
  edges get rounded.

### 2.1 DECIDED 2026-07-24: ship as-is, no external pull-ups, no DNP footprints

> **This supersedes the earlier recommendation in this section to lay out 8× 10 kΩ DNP
> pull-up footprints.** Those footprints were never added to `pcb/naub_v2.py` or the
> board, and the decision is now to **not** add them. §6's passives table is updated to
> match. Do not go looking for pull-up pads on the board — there are none, by choice.

**Why skipping is safe.** Quantify the actual limit: reliable edge detection wants the
pulse half-period above roughly 3τ. At the worst-case τ ≈ 800 µs that's a half-period
> ~2.4 ms, i.e. a pulse period > ~5 ms → ~200 pulses/s → at 20 PPR, about **10 rev/s
(~600 RPM)**. A hard hand-flick on a knob is realistically 3–5 rev/s, which leaves ~5–6 τ
of margin per half-period. The rounding is real but the headroom is comfortable, and
this is not worth 8 extra parts and 16 pads on a crowded 2-layer board.

**Symptom to watch for at prototype bring-up:** counts that are *missed* or *jittery
specifically when spinning fast*, while slow rotation tracks perfectly. Slow-speed
misbehaviour is a firmware/decode bug, not this.

**If it does show up, in order of preference — the first two need NO board change:**

1. **Swap the 8 A/B caps 10 nF → 4.7 nF** (τ ≈ 250 µs, doubling the speed ceiling to
   ~20 rev/s). Same 0603 footprint, a straight parts swap. This is the intended fix and
   the reason no board change was reserved.
2. **Fix it in firmware.** Decode quadrature with a proper state machine that tolerates a
   missed transition, or move decoding to the RP2350's **PIO** — free, and more robust
   than polling regardless of the RC.
3. **Last resort — hand-tack 10 kΩ pull-ups** (τ ≈ 100 µs, the classic Bourns/Alps
   figure). With no footprints provided this means soldering 8× 0603 from each A/B node
   to a 3V3 point by hand. Ugly and fiddly ×8, but possible — it is deliberately *not*
   a respin-level problem.

Options 1 and 2 together cover essentially any realistic case, which is what makes the
missing footprints a non-issue rather than a gamble.

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
| ~~*(opt) A/B pull-ups*~~ | ~~10 kΩ~~ | **0** | — | **NOT on the board** — decided against 2026-07-24, no footprints exist. Fast-spin fallback is a 10 nF → 4.7 nF cap swap instead (§2.1) |
| Charge-pump network | 1 µF X7R 16 V | **9** | at DOG pads | §4.2 — pairings confirmed off EA app schematic (CAP3P↔CAP1N) |
| Display VDD decoupling | 100 nF | 1 | at DOG VDD | |
| Rail bulk | 10 µF | 1 | near 3V3OUT | |
| Rail decoupling | 100 nF | 1 | near 3V3OUT | |
| Backlight series R | 300 Ω (5 V) / 140 Ω (3.3 V) | **3** (one per LED path) | VSYS → A1/A2/A3 | per §3; 0603 fine (~32 mW each) |
| Backlight PWM FET | 2N7002 (SOT-23) | 1 | LED− low-side | only if dimming committed |
| FET gate R / pulldown | 100 Ω / 10 kΩ | 1 / 1 | GP13 gate / gate→GND | only with the FET |
| Aux buttons | — | 5 | GP6/7/20/21/22 | no diodes, no discrete pull-ups |
| **Rail links** (`JP_3V3`, `JP_BL`) | **0 Ω 0603** | **2** | in series with +3V3 and VSYS | §7.1 — **ship stuffed**; a service seam, not a build option |
| *Test pads* `TP_*` | — | 10 | back side | §7.2 — THT, no parts to buy |
| *Debug rows* `J_LCD`, `J_ADC` | — | 2 | back side | §7.3 — **unpopulated**, holes only |

---

## 7. Bring-up hooks (added 2026-07-24, for the first fab batch)

14 parts that carry no signal in normal operation and exist only to make the first
fabricated batch diagnosable and bodgeable. Added while the layout was still unrouted,
when they were free. **Step-by-step procedure that uses them: [BRINGUP.md](BRINGUP.md).**

### 7.1 The two rail cuts

The load lists on both rails happen to be disjoint, so two 0 Ω links split the board
into three independently testable blocks:

```
+3V3     U1.3V3, C_BULK, C_DEC   ──JP_3V3──   U2.VDD/VDD2, C_LCD_VDD      (+3V3_LCD)
VSYS_5V  U1.VSYS                 ──JP_BL───   R_BL1/2/3 → backlight       (VSYS_BL)
```

Nothing else touches either rail — the encoders and Choc keys are switch-to-GND on
internal pull-ups, so they draw from neither.

Both ship **stuffed with 0 Ω**. This is a service seam, not a build option. Decoupling
stays on the *source* side of `JP_3V3` deliberately: `C_BULK`/`C_DEC` belong to the LDO,
so opening the link must not leave the regulator undecoupled — the display keeps its own
`C_LCD_VDD` downstream.

**Why it earns its place on a first spin:** the display cannot come back off (it solders
directly, and U1's front-side fillets are underneath it). If a display solder bridge pulls
the module's LDO down and the rails are hard-wired, a dead module and a dead display look
identical. Open `JP_3V3` and the question answers itself.

0603 chips rather than 2.54 mm shunt headers because a shunt would stand ~11 mm off the
**back** — the side the module and every passive already occupy, and the side facing the
enclosure floor. A chip link costs no height, still lifts in seconds, and can take a 1 Ω
current-sense part instead if you ever want rail current on a scope.

### 7.2 Probe pads (10, all THT, all back side)

THT rather than SMD dots so you can solder a header pin or a bodge wire *into* them —
D2.0 mm pad / **1.0 mm drill**, because a 0.64 mm square header pin measures 0.9 mm across
the diagonal and will not enter the 0.7 mm drill of the smaller part.

| Pad | Net | Reads |
|---|---|---|
| `TP_VOUT` | `LCD_VOUT` | Charge-pump output — **the highest-value pad on the board** |
| `TP_V0` | `LCD_V0` | Top of the bias divider |
| `TP_VSYS` | `VSYS_5V` | 5 V in, as delivered by the FPC |
| `TP_3V3` | `+3V3` | LDO output, source side |
| `TP_3V3L` | `+3V3_LCD` | Display rail, load side |
| `TP_GND1/2/3` | `GND` | One return per cluster — module, charge pump, backlight |
| `TP_BLPWM` | `BL_PWM` | Q1 gate drive from GP13 |
| `TP_BLK` | `BL_CATHODE` | Q1 drain / LED cathodes |

`TP_VOUT` is first for a reason. The ST7565's on-glass pump is 9 hand-soldered caps with
one non-obvious pairing (`CAP3P → CAP1N`, §4.2), and *every* way of getting it wrong
presents identically as a blank screen. VOUT reads ~9–13 V when the pump runs and ≈VDD
when it doesn't, splitting the whole display fault tree in one measurement. `TP_V0` then
splits the survivors: pump good + bias wrong is a contrast problem, not a cap problem.

Three grounds, not one, because a probe return is only honest next to the thing it's
returning — these pads sit >60 mm apart on a 56 × 102 mm board.

**Power injection:** 5 V across `TP_VSYS`+`TP_GND1`, 3.3 V across `TP_3V3`+`TP_GND2`
(place each pair adjacent). Power normally reaches this PCB *through* U1 — VSYS arrives
over the adapter FPC and enters at the module's VSYS pad — so without these pads there is
no way to energize the board at all before a module is soldered on. With them, a
current-limited supply smoke-tests the populated back side *before* the module is
committed, and U1 cannot be removed once U2 is on.

### 7.3 Two unpopulated 1×6 rows

Holes only. No headers shipped, no BOM cost.

- **`J_LCD`** — **GND, SI, SCL, A0, RST, CS**. Clip a logic analyzer on and see whether
  the MCU is transmitting at all; that is the first question when the screen is blank.
  GND is on the row so the analyzer's ground isn't a flying lead.
  **Placed horizontally above `U2`'s pin row at (164.92, 102.00), rot 270.**
  > *Revised 2026-07-24.* This originally read "place next to the module's GP8–GP13
  > contacts, **not** at the display end" — written without checking U1's orientation.
  > U1 sits at **rot=180**, which mirrors the module: GP8–GP13 end up spread along its
  > *bottom* edge in a jumbled order, while all 20 display pins are one row at
  > y = 106.78 with SI/SCL/A0/RST/CS adjacent at x 167.46–177.62. The display end is
  > therefore both the shorter route *and* the better tap — it proves the signal
  > **arrived**, where an MCU-side tap only proves one was sent.

  Pin order follows `U2`'s physical x-order, not signal convention, so the row is a
  zero-crossing comb. **GND leads** for a purely mechanical reason: with GND last, the
  row has to start at `U2.36`'s x and the final pin lands 0.47 mm from the board edge.
  Leading with GND shifts it one pitch left, putting all five signals at **dx = 0.00**
  from their display pads — five identical 4.78 mm stubs.
- **`J_ADC`** — GP26, GP27, GP28, GP29, 3V3, GND. Spare IO and, mostly, *somewhere to land
  a bodge wire* — the thing you most often need on a first batch and the thing a
  fabricated board can't give you after the fact. Keeps the fader/pot option open.
  **Placed vertically at (166.00, 85.58), rot 0** — directly opposite U1's edge column at
  x = 161.12, where GP26/27/28/29/3V3/GND run on the same 2.54 pitch in the same order.
  Six straight 3.6–4.9 mm stubs, no crossings.

  This supersedes the earlier "no expansion header" decision, whose reasoning was
  specifically *"costs six traces on a crowded 2-layer board"* — a routing objection,
  answered by constraining placement rather than by dropping the row.

  > That old note also offered a fallback — *"tack a wire into the module footprint's
  > edge-ring hole"* — which **does not hold**. Those 1.0 mm plated holes are real, but
  > they sit at footprint-local x = −7.62 against the castellation at −8.91: **inboard,
  > under the module body**, and they fill with solder when U1 is mounted. Once U1 is
  > down, GP26–29 have no access at all without `J_ADC`. It earns its place.

### 7.4 Everything is under the display — so tent the front mask

The display body spans **x 125.19–180.99, y 63.69–106.69** — 55.8 × 43 mm, essentially
the whole board. The only glass-free band is **y 107.8–110.9**, between `U2`'s pin row
and the Choc pins: 3.3 mm tall, and blocked at x > 166 by `SW4`. There is nowhere else
to put the debug hardware, and pushing `J_ADC`/`J_LCD` out to the extreme left and right
edges does *not* escape it.

Back-side placement means no electrical conflict, but 12 through-hole debug parts now
have **front-side annular rings under a display that seats nearly flat** — and soldering
a wire into a test point from the back can wick solder through and raise a bump beneath
the glass. This is the same failure as §1.2 of the action plan (U1's front-side fillets),
in the same region.

So the exposure is managed rather than avoided: **`F.Mask` is removed from every THT
debug pad under the display**, covering the front ring. `TP_BLPWM` (y = 59.0) is the one
THT hook left untented — it is clear of the glass.

Applied by `pcb/place_bringup_hooks.py`. Note this makes those footprints differ from
their stock library copies, which DRC reports as `lib_footprint_mismatch` — **expected;
do not "fix" it by reloading from the library, that silently undoes the tenting.**

### 7.5 What is *not* retrofittable

**Board-level RESET.** The carrier footprint brings out 36 pads and `RUN` is not among
them, so no reset button can be added here — RESET and BOOTSEL reach the module only
through the FPC from the panel-mounted USB-C adapter. That's by design and the buttons
are panel-accessible, but a bad FPC seat costs you both.

> ⏳ **Before ordering:** diff the footprint against Waveshare's published pinout. If the
> real module exposes RUN and the footprint merely omitted it, that's a footprint bug
> worth catching now — and a board-level reset is worth having on a first spin.

---

## 8. Still owned by you (GUI / mechanical — not in this doc)
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
- **Bring-up hook placement (§7)** — all on **B.Cu**, the service side; a test pad on the
  front is one you can't reach with the enclosure assembled. Honour the three placement
  constraints that are the whole reason the hooks work: `TP_VSYS`+`TP_GND1` and
  `TP_3V3`+`TP_GND2` adjacent (bench-supply clips), `J_LCD` by GP8–GP13, `J_ADC` hard
  against GP26–29. **Silkscreen every pad with its net name**, and label J1's pinout
  (SWCLK/GND/SWDIO) with a pin-1 marker — you will be reading this board upside down
  under a lamp.

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
