# Bill of Materials — V2 (in progress)

**Project:** naub — USB application/system audio mixer
**Controller:** **Waveshare RP2350-Tiny** (RP2350A) + panel-mount USB-C adapter — supersedes the Pico 2 pick (2026-07-15, see §Controller)
**Revision:** V2 (planning)
**Date:** 2026-06-22 (controller updated 2026-07-15)

> This is a **delta** on [BOM.md](BOM.md) (V1). Only parts that change for V2 are
> listed here; everything not mentioned carries over from V1 unchanged.

---

## Changes from V1

| # | Component | V1 part | V2 part | Why |
|---|-----------|---------|---------|-----|
| 0 | **Controller** | Raspberry Pi Pico (V1 hand-wire; Pico 2 was the interim V2 plan) | **Waveshare RP2350-Tiny** + USB-C adapter board over 8-pin FPC (kit) | Pico 2 + its onboard USB jack don't fit the final layout; the Tiny splits USB onto a separate 18×18 mm **panel-mount** adapter. Same RP2350A silicon as the Pico 2 |
| 1 | Rotary encoder | Bourns PEC11R-4220F-S0024 (24 detent, capped feel) | **Bourns PEC11L-4020F-S0020** (low profile, no detents) | Zero-detent smooth feel (grease-damped for weight) + **low-profile body** for a slimmer stack; supersedes the interim Alps EC11E1834403 pick |
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

### Controller — Waveshare RP2350-Tiny + USB-C adapter (supersedes the Pico 2)

Order as the **RP2350-Tiny-Kit** (module + USB adapter board + 8-pin 0.5 mm FPC
cable, ~15 cm). Verified against the Waveshare schematic, DXF, and pinout diagram
(2026-07-15).

| Spec | Value |
|------|-------|
| MCU | RP2350A (same silicon as Pico 2) — dual M33 / Hazard3, 150 MHz, 520 KB SRAM |
| Flash | 4 MB (P25Q32SH) |
| Module size | **18 × 23.5 mm, 2.1 mm thick** — components top-side only, back is flat |
| Edge contacts | 23× castellated **+** Ø0.75 mm through-hole, 2.54 mm pitch: GP0–GP15, GP26, GP27/28/29 (47 Ω series on-module), 3V3/GND/VSYS |
| Back pads | 13×, all carrier-facing: **GND + GP16–19**, **3V3 + GP20–23**, **SWD CLK/GND/DIO** |
| Not available | GP24/GP25 (NC mode-select pads); **GP16 shared with onboard WS2812 LED** — treat as spent (free status LED) |
| 3.3 V rail | ME6217C33M5G LDO, 800 mA — ample for the §5 budget (<100 mA) |
| USB | On the adapter board: USB-C + **RESET/BOOT buttons** + power LED; 18 × 18 mm, 4× M2 holes on a 14 × 14 mm grid (2 mm from edges) → panel-mount |
| Firmware | Official CircuitPython build (`waveshare_rp2350_tiny`) |

**Qty:** 1 kit (+ 1 spare FPC cable recommended — 0.5 mm FPC tabs don't love re-seating).

**Pin budget:** 27 usable GPIO — the full V2 map (22 pins: 12 encoder + 5 LCD +
1 backlight PWM + 4 aux buttons) fits with **one remap: ENC3_SW GP16 → GP23**.
GP22 stays the spare digital, the GP26–28 fader reserve survives, GP29 is a bonus
ADC spare.

**What this replaces:** the Pico 2 (non-H) *and* the Adafruit 4090 + micro-USB
pigtail — the Tiny's adapter board **is** the panel USB now, and BOOTSEL/RESET
access comes with it. Power + USB data enter over the FPC (VSYS); the backlight
taps VSYS on the carrier (PCB_DESIGN §3, was VBUS).

**Design rework this triggers:** see the **Next steps** checklist at the top of
[PCB_DESIGN.md](PCB_DESIGN.md) — pin-map fix, VSYS rail note, hybrid
castellation + solder-through-hole module footprint, netlist swap.

### Encoder — Bourns PEC11L-4020F-S0020 (supersedes the Alps EC11E pick)

| Spec | Value |
|------|-------|
| Type | Incremental mechanical rotary, 2-phase (A/B), 11 mm **low profile** |
| Detents | **0 — no detents (smooth)** (detent variants: codes 1/2/3 in the P/N) |
| Resolution | 20 PPR (P/N options: 9 / 15 / 20) |
| Push switch | Integrated SPST momentary (click-to-mute/select retained) |
| Shaft | 6 mm flatted metal shaft, 20 mm (15 / 25 mm variants exist) |
| Bushing / mount | M7×0.75 threaded, nut + washer bundled |
| Terminals | PC pins, through-hole (A/C/B row + S1/S2 row, tab slots) |
| Contact rating | 10 mA @ 5 V DC |
| Rotational life | 100,000 cycles |
| Temp range | −25 °C to +80 °C |
| Soldering | **Wave spec only — Bourns says hand soldering "not recommended"**; hand-build with a fine tip and short dwell |

**Qty:** 5 (4 used + 1 spare), matching V1.

**Wiring:** drop-in for V1 — same A / C(common) / B + SW pin layout, same
GPIO map and debounce caps (see [WIRING.md](WIRING.md) §3). No pin changes.
Footprint: `naub:RotaryEncoder_Bourns_PEC11L-4xxxF-Sxxxx_Vertical`
(in-repo, drawn from the Bourns PCB layout drawing).

**Mechanical note:** the PEC11L body is shallower than the EC11E the mechanical
notes were written against — **re-derive the panel-to-PCB gap** from the PEC11L
drawing before committing standoff heights (see PCB_NOTES checklist).

**Knobs:** V1 3D-printed PETG knobs carry over, but cut the bore for this
shaft (6 mm, flatted, 20 mm) — verify flat orientation/depth before printing.

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
| 9 | **Backlight series resistors** | **300 Ω 0603 ×3** (one per LED path, VSYS 5 V rail) | 3 | LED55X46 has 3 independent LED paths through the DOG footprint; ~10 mA/path, ~31 mA total (PCB_DESIGN §3) |
| 10 | **Backlight PWM MOSFET** | 2N7002 (N-ch SOT-23) + ~10 kΩ gate resistor | 1 + 1 | **Only if PWM dimming is committed** — low-side switch on LED− |
| 11 | **Display decoupling** | 10 µF bulk + 100 nF | 1 + 1 | On the 3.3 V rail at the module |
| 12 | **Mounting hardware** | M7×0.75 encoder nuts/washers, standoffs, panel screws, heat-set inserts | set | Some bundled with the encoders — confirm before ordering |

> The **bare PCB** and the **charge-pump caps** are the two easiest parts to drop
> at order time (they live in [PCB_NOTES.md](PCB_NOTES.md) prose, not the V1 BOM).
> Encoder debounce caps (8× 10 nF + 4× 100 nF) and the PETG knobs **carry over
> from V1 unchanged**. The Pico and the Adafruit 4090 + micro-USB pigtail do
> **not** carry over — the RP2350-Tiny kit (row 0) replaces both (its adapter
> board is the panel-mount USB now).

---

## Design implications (firmware, no hardware change)

- **No detents → no per-click step.** Quadrature decode is unchanged, but volume
  will feel analog/continuous. Revisit volume-per-edge scaling so one full sweep
  maps to a comfortable 0–100 range (18 PPR = 72 edges/rev vs V1's 24 PPR / 96).
- Click-to-mute/select is unaffected (switch retained).

---

## Planned for the next revision (same PCB, parts only)

Agreed for the **next** build, not this one. Nothing in this section touches the
V2 board — don't act on it while assembling the current revision.

| # | Component | Next-revision part | Why |
|---|-----------|--------------------|-----|
| 1 | Rotary encoder | **Bourns PEC11L-4015F-S0020** | Detentless (detent code `0`), 15 mm shaft, 20 PPR — the detented feel reads as rough and clicky; grease-damped weight replaces the click |

> **Baseline caveat.** This file currently lists `PEC11L-4020F-S0020` (detentless,
> 20 mm shaft), while `worktree-encoder-detent-swap` lists `PEC11L-4215F-S0015`
> (30 detents, 15 mm shaft). Against the former the delta is **shaft length only**;
> against the latter it is **detent code and resolution** as well. Reconcile when
> those branches merge.

### Encoder — Bourns PEC11L-4015F-S0020 (planned)

| Spec | Value |
|------|-------|
| Detents | **0 — none.** Ordering digit: `0` = no detents (15 or 20 pulses), `1` = 20 detents, `2` = 30 detents |
| Resolution | 20 PPR |
| Shaft | 6 mm **flatted**, **15 mm measured from the mounting surface** (top of the M7 bushing, not the body) |
| Push switch | Retained — SPST momentary, 610 ± 306 gf, 0.5 mm travel |
| Body / bushing | 5.0 mm above PCB + 4.5 mm M7×0.75 bushing → mounting surface at **9.5 mm**, shaft tip at **24.5 mm** |
| Torque | Running 10–150 gf-cm; no detent torque term |
| Life | 100,000 cycles rotational / 100,000 switch actuations |

**No PCB work.** The in-repo footprint
`naub:RotaryEncoder_Bourns_PEC11L-4xxxF-Sxxxx_Vertical` is deliberately
variant-agnostic: detent code, shaft length and resolution change no physical
dimension. Same pads, same netlist, same pin map, same 5.0 mm body height.

**Pairs with the Nyogel 767A** (row 2 above): a detentless shaft has nothing but
running torque to give it weight, so the grease stops being optional. Budget time
to dial it in — hand-applied damping is hard to match across four knobs, and the
4.5 mm bushing is a short, loose annulus rather than a designed damper. If
unit-to-unit consistency matters more than authenticity, a panel-side O-ring under
the knob skirt sets the drag by printed geometry instead.

**Firmware knock-on:** with no detents there is no click to align counts to, so
`divisor` becomes a free choice rather than the fixed `2`, and `STEP` wants
retuning against 20 PPR. Also re-check the fast-spin ceiling in PCB_DESIGN §2 —
20 PPR is electrically ~33 % faster than 15 PPR, which pulls the limit back toward
~10 rev/s. Watch for dither at rest: a detentless shaft can park mid-transition,
where `divisor = 4` is the most noise-immune.

**Shaft-style note:** the knurled `PEC11L-4015K-…` variant is *not* a trim-to-fit
substitute — its knurl is a 6 mm band at the very tip, identical on the 15 mm and
20 mm shafts, so cutting to length destroys the grip (and the 20 mm version's extra
length is all neck, buying no extra grip). The flatted `F` keeps a 7 mm flat back
from the tip and tolerates trimming.

---

## Open decisions / TBD for V2

- [x] **Controller:** Waveshare RP2350-Tiny + panel-mount USB-C adapter
      (supersedes Pico 2) — order the **kit** variant (module + adapter + FPC).
- [x] **PCB docs/netlist rework for the RP2350-Tiny** — done 2026-07-16: pin remap
      (ENC3_SW GP16→GP23), VSYS backlight rail, hybrid module footprint
      (`pcb/naub.pretty/Waveshare_RP2350-Tiny_Hybrid.kicad_mod`), naub_v2.py swap +
      regenerated netlist (42 parts / 48 nets). Still open in
      [PCB_DESIGN.md](PCB_DESIGN.md) §0: pre-fab 1:1 check of the SWD pad row,
      adapter mechanicals (enclosure CAD), firmware remap at V2 bring-up.
- [ ] Grease selection confirmed (Nyogel 767A vs. heavier 767B) — buy a spare
      encoder to dial in the feel before committing.
- [ ] Knob redesign for the new shaft (bore + optional steel weight for inertia).
- [ ] Aux button force: confirm Red Pro 35 gf vs Pink 20 gf on a loose switch
      (soldered down = permanent, so decide before bulk order).
- [ ] Extended printed Choc-stem cap for the aux buttons (+ enclosure clearance).
- [ ] **Display:** confirm DOGM128W-6 (128×64) vs DOGXL160W-7 (160×104) — same
      connector/cap story either way.
- [ ] **Charge-pump caps:** exact count/values from the EA DOG reference schematic.
- [x] **Backlight series resistors:** sized — 3× 300 Ω off VSYS/5 V (Vf ≈ 1.9 V amber, ~10 mA/path); see PCB_DESIGN §3.
- [ ] **Backlight dimming:** commit PWM (2N7002 low-side + dedicate a GPIO, e.g. the
      freed BUSY pin) or run fixed-brightness (resistor only, no MOSFET/pin).
- [ ] **Display mounting:** solder-direct (default) vs DOG-on-carrier + 1×8 cable.
- [ ] **Firmware:** ST7565 driver (e.g. u8g2) + category-glyph/text UI replacing icons.
- [ ] _(add further V2 changes here as decided)_

---

## Sources

- Waveshare RP2350-Tiny product page — <https://www.waveshare.com/rp2350-tiny.htm>
- RP2350-Tiny wiki (pinout, resources) — <https://www.waveshare.com/wiki/RP2350-Tiny>
- RP2350-Tiny schematic — <https://files.waveshare.com/wiki/RP2350-Tiny/RP2350_Tiny_Schematic.pdf>
- RP2350-Tiny 2D drawing (DXF, for the carrier footprint) — <https://files.waveshare.com/wiki/RP2350-Tiny/RP2350_Tiny_DXF.zip>
- CircuitPython build for the RP2350-Tiny — <https://circuitpython.org/board/waveshare_rp2350_tiny/>
- Bourns PEC11L series datasheet (REV. 04/26) — <https://www.bourns.com/docs/Product-Datasheets/PEC11L.pdf>
- PEC11L-4020F-S0020 (Mouser) — <https://www.mouser.com/ProductDetail/Bourns/PEC11L-4020F-S0020>
- PEC11L-4015F-S0020 — planned next-revision encoder (DigiKey) — <https://www.digikey.com/en/products/detail/bourns-inc/PEC11L-4015F-S0020/4699163>
- Alps EC11 series datasheet — <https://www.avnet.com/fsp/opasdata/d120001/medias/docus/196/Alps-Alpine-EC11-Series-EN-Datasheet.pdf>
- Nye Nyogel 767A damping grease — <https://www.nyelubricants.com/nyogel-767a>
- EA DOGM128W-6 (128×64 FSTN+ STN LCD, ST7565R) — <https://www.lcd-module.com/eng/pdf/grafik/dogm128e.pdf>
- EA LED55X46-A amber backlight — <https://shop.lcd-module.com/>
- EA DOGXL160W-7 (160×104, UC1610) higher-res option — <https://www.mouser.com/datasheet/3/711/1/dogxl160-7e.pdf>
- Waveshare 2.9" e-Paper (V1 display, now superseded) — <https://www.waveshare.com/2.9inch-e-paper-module.htm>
- Kailh PG1350 (Choc V1) series — <https://deskthority.net/wiki/Kailh_PG1350_series>
- Kailh Choc Red Pro 35 gf linear — <https://ymdkey.com/products/kailh-low-profile-red-pro-choc-1350-switch-35g-linear>
