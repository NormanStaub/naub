# Bill of Materials — V2 (in progress)

**Project:** naub — USB application/system audio mixer
**Controller:** Raspberry Pi Pico 2 (RP2350)
**Revision:** V2 (planning)
**Date:** 2026-06-22

> This is a **delta** on [BOM.md](BOM.md) (V1). Only parts that change for V2 are
> listed here; everything not mentioned carries over from V1 unchanged.

---

## Changes from V1

| # | Component | V1 part | V2 part | Why |
|---|-----------|---------|---------|-----|
| 1 | Rotary encoder | Bourns PEC11R-4220F-S0024 (24 detent, capped feel) | **Alps Alpine EC11E1834403** | Higher-end smooth (zero-detent) feel; grease-damped for weight |
| 2 | Damping grease | — | **Nye Nyogel 767A** (silicone haptic damping grease) | Adds rotational friction/weight to the detentless shaft |
| 3 | **Display** | Waveshare 2.9" e-Paper (296×128, SSD1680, SPI) | **EA DOGM128W-6** — 128×64 amber **STN LCD** (FSTN positive, transflective, ST7565R, SPI) | Amber-backlit "classic" look; pixel-addressable; ~30–60 fps live volume bars vs e-paper's 1–2 fps |
| 4 | **Display backlight** | — (e-paper needs none) | **EA LED55X46-A** (amber LED backlight, separate snap/solder board) | True ~605 nm amber via the backlight LED, not a filter; single-color = one backlight line |
| 5 | Display mounting | PCB connector + cable (earlier V2 plan) | **Solder DOG directly to PCB** + ST7565 charge-pump caps at the pins | COG module is solder-in; charge-pump cap nets must be local to the chip (can't route over a cable) |
| 6 | Aux button | (unsourced in V1) | **Kailh Choc V1 (PG1350) linear** — Red Pro 35 gf | Smooth, light, quiet linear press (no snap) — distinct from the heavy encoder click, won't sound cheap in a light shell |

> **Display changed for V2** — the Waveshare e-paper is replaced by an **amber STN
> LCD** (EA DOGM128W-6 + amber backlight). This **supersedes the earlier V2 plan**
> of a JST-PH 1×8 header + double-ended cable: the DOG is a chip-on-glass module
> that **solders directly into the PCB**, and its ST7565 charge-pump capacitors
> must sit right at the module pins — so a plain display cable is no longer viable.
> The **JST-PH header and double-ended cable are dropped** (see Display section).
>
> Resolution note: 128×64 is fine now that app identity comes from **category
> glyphs + text labels**, not brand icons (brand logos don't survive 1-bit at this
> size). For higher res, **EA DOGXL160W-7** (160×104, UC1610) is a drop-in
> connector-wise — same solder-in COG + charge-pump-caps story.

### Encoder — Alps Alpine EC11E1834403

| Spec | Value |
|------|-------|
| Type | Incremental mechanical rotary, 2-phase (A/B) |
| Detents | **0 — not indexed (smooth)** |
| Resolution | 18 PPR (pulses per revolution) |
| Push switch | Integrated SPST, 1.5 mm travel (click-to-mute/select retained) |
| Shaft | 6 mm flat (D) shaft, ~20 mm length |
| Bushing / mount | M7×0.75 threaded, panel mount |
| Terminals | PC pins, through-hole |
| Rotational torque | ~7 mN·m (before grease) |
| Contact rating | 10 mA @ 5 V DC |
| Rotational life | 15,000 cycles |
| Temp range | −30 °C to +85 °C |

**Qty:** 5 (4 used + 1 spare), matching V1.

**Wiring:** drop-in for V1 — same A / C(common) / B + SW pin layout, same
GPIO map and debounce caps (see [WIRING.md](WIRING.md) §3). No pin changes.

**Knobs:** V1 3D-printed PETG knobs carry over, but cut the bore for this
shaft (6 mm, flatted, ~20 mm) — verify flat orientation/depth before printing.

### Damping grease — Nye Nyogel 767A

| Spec | Value |
|------|-------|
| Type | Synthetic hydrocarbon / silicone damping (haptic) grease |
| Use | Pack a thin film into the encoder detent/bushing area to add drag |
| Qty | 1 small tube/syringe (a few grams covers many encoders) |
| Note | More grease = more drag; tune feel on a spare encoder first |

### Display — EA DOGM128W-6 amber STN LCD

| Spec | Value |
|------|-------|
| Panel | 128×64 graphic, FSTN **positive**, **transflective** |
| Controller | ST7565R, **4-wire SPI**, 3.3 V (matches the V1 display rail) |
| Backlight | **EA LED55X46-A amber**, separate board behind the glass; single-color → one backlight line |
| Response | ~150–250 ms optical; comfortable 30–60 fps live bars (vs e-paper 1–2 fps) |
| Mounting | **Soldered directly** to the PCB (2.54 mm pin row + backlight pins) |
| Charge-pump caps | ~7× ceramic (~1 µF) at the module pins, per the EA reference schematic — **must be local** |
| Backlight drive | LED+ via series resistor; LED− to GND; optional PWM dimming on a GPIO |

**Signal change vs the e-paper (for [WIRING.md](WIRING.md) at firmware time):**

| e-paper pin | DOG pin | Note |
|-------------|---------|------|
| DIN (MOSI) | **SI** | same net |
| CLK (SCK) | **SCL** | same net |
| CS | **CS** | same net |
| DC | **A0** (data/command) | same net, renamed |
| RST | **RST** | same net |
| **BUSY** | — (none) | LCD has no busy flag → **GPIO freed** |
| — | **backlight** | new: LED+ (series resistor) + optional PWM-enable GPIO |

**Connector decision — no display connector by default.** Because the DOG solders
in and its charge-pump caps must sit at the pins, the V2 plan **drops the JST-PH
1×8 header and the double-ended cable**. The display is now a soldered part with a
footprint + local cap network + backlight resistor.

**Serviceable alternative (if you want the display removable):** put the DOG **and
its charge-pump caps on a small carrier PCB**, and run a cable from the carrier to
the main board. With the backlight cathode sharing the common GND, the link is
**8 lines** — so a **1×8 connector still fits**: `VCC, GND, SI, SCL, CS, A0, RST,
LED+`. (The caps stay on the carrier — they cannot go over the cable.)

### Aux button — Kailh Choc V1 (PG1350) linear

| Spec | Value |
|------|-------|
| Type | Low-profile mechanical, **linear** (no tactile bump, no click) |
| Force | **Red Pro ~35 gf** (pick; **Pink ~20 gf** = lighter alt, Red ~50 gf = firmer) |
| Travel | 1.5 mm to actuation, 3.0 mm total (short throw) |
| Footprint | ~15 × 15 mm body, ~8 mm tall above PCB |
| Mounting | Through-hole, **soldered directly** to the PCB (no hot-swap sockets) |
| Cap | Custom **extended** printed cap, must match the **Choc stem** (not MX cross) |
| Qty | **4 used** + spares |

**Why linear:** smooth spring-loaded press with a quiet bottom-out — gives the
"deliberate event" feel without a snap that rings out in a lightweight enclosure,
and at 20–35 gf it's far lighter than the ~6 N encoder press.

**Trade-off:** larger footprint / taller than a 6 mm tactile — this is the floor
for a true mechanical-keyboard feel. Budget the board area and Z-height.

### V2 support parts (new on the custom PCB)

New for V2 because V1 was hand-wired; these support the new display and the move
to a **fabricated board**. Values marked *confirm* come from the EA DOG reference
schematic / LED55X46-A datasheet — not invented here.

| # | Part | Spec / value | Qty | Note |
|---|------|--------------|-----|------|
| 7 | **Bare PCB** | Custom **2-layer, 1.6 mm**, HASL or ENIG | 1 (+ fab MOQ spares) | The V2 board itself — the defining change from V1 hand-wiring |
| 8 | **Charge-pump caps** | Ceramic ~1 µF X7R, count *confirm* (~7) | ~7 | ST7565 on-chip boost; **must sit at the DOG pins** |
| 9 | **Backlight series resistor** | value *confirm* from LED55X46-A Vf/If | 1 | Sets backlight current (~10–150 mA); off 3.3 V or VBUS |
| 10 | **Backlight PWM MOSFET** | 2N7002 (N-ch SOT-23) + ~10 kΩ gate resistor | 1 + 1 | **Only if PWM dimming is committed** — low-side switch on LED− |
| 11 | **Display decoupling** | 10 µF bulk + 100 nF | 1 + 1 | On the 3.3 V rail at the module |
| 12 | **Mounting hardware** | M7×0.75 encoder nuts/washers, standoffs, panel screws, heat-set inserts | set | Some bundled with the encoders — confirm before ordering |

> The **bare PCB** and the **charge-pump caps** are the two easiest parts to drop
> at order time (they live in [PCB_NOTES.md](PCB_NOTES.md) prose, not the V1 BOM).
> Pico (non-H), encoder debounce caps (8× 10 nF + 4× 100 nF), the Adafruit 4090 +
> micro-USB pigtail, and the PETG knobs all **carry over from V1 unchanged**.

---

## Design implications (firmware, no hardware change)

- **No detents → no per-click step.** Quadrature decode is unchanged, but volume
  will feel analog/continuous. Revisit volume-per-edge scaling so one full sweep
  maps to a comfortable 0–100 range (18 PPR = 72 edges/rev vs V1's 24 PPR / 96).
- Click-to-mute/select is unaffected (switch retained).

---

## Open decisions / TBD for V2

- [ ] Grease selection confirmed (Nyogel 767A vs. heavier 767B) — buy a spare
      encoder to dial in the feel before committing.
- [ ] Knob redesign for the new shaft (bore + optional steel weight for inertia).
- [ ] Aux button force: confirm Red Pro 35 gf vs Pink 20 gf on a loose switch
      (soldered down = permanent, so decide before bulk order).
- [ ] Extended printed Choc-stem cap for the aux buttons (+ enclosure clearance).
- [ ] **Display:** confirm DOGM128W-6 (128×64) vs DOGXL160W-7 (160×104) — same
      connector/cap story either way.
- [ ] **Charge-pump caps:** exact count/values from the EA DOG reference schematic.
- [ ] **Backlight series resistor:** size from LED55X46-A Vf/If (3.3 V vs VBUS rail).
- [ ] **Backlight dimming:** commit PWM (2N7002 low-side + dedicate a GPIO, e.g. the
      freed BUSY pin) or run fixed-brightness (resistor only, no MOSFET/pin).
- [ ] **Display mounting:** solder-direct (default) vs DOG-on-carrier + 1×8 cable.
- [ ] **Firmware:** ST7565 driver (e.g. u8g2) + category-glyph/text UI replacing icons.
- [ ] _(add further V2 changes here as decided)_

---

## Sources

- Alps Alpine EC11E series — <https://tech.alpsalpine.com/e/products/category/encorders/sub/01/series/ec11e/>
- EC11E1834403 (RS, "18 Pulse … 6 mm Flat Shaft, Not Indexed") — <https://sg.rs-online.com/web/p/mechanical-rotary-encoders/7295615>
- EC11E1834403 (Mouser) — <https://www.mouser.com/ProductDetail/Alps-Alpine/EC11E1834403>
- Alps EC11 series datasheet — <https://www.avnet.com/fsp/opasdata/d120001/medias/docus/196/Alps-Alpine-EC11-Series-EN-Datasheet.pdf>
- Nye Nyogel 767A damping grease — <https://www.nyelubricants.com/nyogel-767a>
- EA DOGM128W-6 (128×64 FSTN+ STN LCD, ST7565R) — <https://www.lcd-module.com/eng/pdf/grafik/dogm128e.pdf>
- EA LED55X46-A amber backlight — <https://shop.lcd-module.com/>
- EA DOGXL160W-7 (160×104, UC1610) higher-res option — <https://www.mouser.com/datasheet/3/711/1/dogxl160-7e.pdf>
- Waveshare 2.9" e-Paper (V1 display, now superseded) — <https://www.waveshare.com/2.9inch-e-paper-module.htm>
- Kailh PG1350 (Choc V1) series — <https://deskthority.net/wiki/Kailh_PG1350_series>
- Kailh Choc Red Pro 35 gf linear — <https://ymdkey.com/products/kailh-low-profile-red-pro-choc-1350-switch-35g-linear>
