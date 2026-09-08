# What embedding the RP2350 directly would actually take

**Project:** naub — USB application/system audio mixer
**Question:** drop the Waveshare RP2350-Tiny module + USB-C adapter, and put the
RP2350A silicon and its support circuit straight onto the naub V2 board.
**Date:** 2026-09-07 · prices and stock checked the same day
**Companion docs:** [PCB_DESIGN.md](PCB_DESIGN.md) · [PCB_NOTES.md](PCB_NOTES.md) ·
[PCB_ACTION_PLAN.md](PCB_ACTION_PLAN.md) · [BOM_V2.md](BOM_V2.md) ·
[pcb/export_fab.py](pcb/export_fab.py) (generates the JLC order sheet)

> Study only. Nothing here is committed to the board, the netlist, or the BOM.

---

## 0. Verdict

**Worth doing, and now is the cheapest moment it will ever be.**

The board is **not routed and not ordered** — `naub_kicad.kicad_pcb` still has 66
unconnected ratsnest lines and PCB_ACTION_PLAN §1.4 is blocked on a re-import that
hasn't happened. Every hour of layout that would be thrown away by this change has
not been spent yet. After the first fab order, the same change becomes a respin.

The numbers, in one line each:

| | Module (today) | Native RP2350A |
|---|---|---|
| Parts cost per board | **~$10–13** (Tiny-Kit, retail + shipping) | **~$2.60** (LCSC, qty 1 pricing) |
| One-off fees | $0 (all 9 parts are JLC **Basic**) | **~$10.50** extended-part feeders, once per order |
| First-spin assembly tier | Economic ($0 setup) | Economic works on paper; **budget Standard ($25)** for spin 1 |
| Bare PCB | 2-layer, unchanged | **2-layer, unchanged** — the official Minimal boards are 2-layer |
| Hand-soldered joints on U1 | **36**, 13 of them blind and under the display | **0** |
| Usable GPIO | 27 (GP16 spent, GP24/25 absent) | **30**, all free |
| Engineering | done | **~1–2 days**, dominated by copying a reference block verbatim |

The cost case is real but small at this scale — you save ~$8–10 a board and pay
~$10 of it back in feeder fees on the first order, so at qty 5 it's roughly a wash
and only pulls ahead from ~qty 10. **Do this for the complexity, not the money.**

What you are actually buying out is §1.2 of the action plan: thirteen blind
solder-through joints under a display that seats flat and cannot come back off.

---

## 1. What the module costs you today

Not the purchase price — the design debt. Every one of these is a documented open
item or a documented hazard in the current plan, and every one is caused by the
module rather than by naub:

| # | Cost | Where it's written down |
|---|------|-------------------------|
| 1 | **13 back-pad joints soldered blind from the front, under the flush backlight.** Any fillet proud of the surface rocks or lifts the display. U1 must be soldered first, every joint dressed flush, and it cannot be removed once U2 is on. | PCB_ACTION_PLAN §1.2 |
| 2 | **A footprint whose SWD row position was measured off product photos (±0.15 mm)** because the official DXF disagrees with the wiki render. Mitigation is "1:1-print it and lay the module on it before ordering." | PCB_DESIGN §0 ⏳ |
| 3 | **No board-level RESET, and not retrofittable** — `RUN` is not among the module's 36 contacts. RESET and BOOTSEL reach the chip only through the FPC. | PCB_DESIGN §7.5 |
| 4 | **A 15 cm 8-pin 0.5 mm FPC carrying the USB-FS pair and VSYS**, with "buy a spare, the tabs don't love re-seating" and "route it away from encoder wiring" as standing instructions. A bad seat costs you RESET *and* BOOTSEL *and* power *and* data. | BOM_V2 §Controller, PCB_DESIGN §0 ⏳ |
| 5 | **GP16 is spent** on the module's onboard WS2812 and **GP24/25 don't exist**, which is the sole reason ENC3_SW carries a `GP16 → GP23` remap that firmware still has to apply at bring-up. | PCB_DESIGN §1 |
| 6 | **The board cannot be powered at all before the module is on** — power enters *through* U1. This is why `TP_VSYS`/`TP_GND1` injection pads exist. | PCB_DESIGN §7.2 |
| 7 | **Single-SKU supply.** One Waveshare part number, one adapter, one FPC. | — |

Items 1, 3, 4, 5 and 6 all disappear outright with native silicon. Item 2 is
replaced by a vendor-supplied footprint. Item 7 becomes "any LCSC stock".

---

## 2. The support circuit — what actually has to go on the board

Taken from the official *Hardware design with RP2350* guide (rev 2026-07-03,
`RPI-RP2350A-MINIMAL_R4`), not from memory. Raspberry Pi ships this as a
downloadable KiCad reference design.

### 2.1 The parts

| Block | Parts | Notes |
|---|---|---|
| **MCU** | RP2350A, QFN-60-EP 7×7, 0.4 mm pitch | 30 GPIO, all available |
| **Core regulator** (on-chip buck) | **L1** Abracon `AOTA-B201610S3R3-101-T` 3.3 µH 0806 · **C6/C7/C9** 4.7 µF 0402 · **R3** 33 Ω | See §4.1 — this is the one block you may not improvise |
| **3.3 V input LDO** | NCP1117ST33T3G (or equivalent) + 2× 10 µF | Same job the module's ME6217C33M5G does today |
| **Crystal** | **X1** Abracon `ABM8-272-T3` 12 MHz · 2× 15 pF load · **R2** 1 kΩ series | RPi "strongly suggest" this exact part; ESR-matched to R2 |
| **QSPI flash** | W25Q32 (4 MB) — see §5 for why not 16 MB and not none | Short direct traces, no length matching needed |
| **Boot straps** | R6 1 kΩ to BOOTSEL button · R1 10 kΩ pull-up (DNF with W25Q) · R10 0 Ω | R6 is *not* optional |
| **USB** | 2× **27 Ω** series on DP/DM, close to the chip · USB-C + 2× 5.1 kΩ CC · optional USBLC6-2SC6 ESD | No pull-ups/downs needed — built into the RP2350 I/O |
| **Decoupling** | ~11× 100 nF, one per power pin | RPi's own board shares one cap across pins 53/54 for space; naub has more room on that side |
| **Buttons** | BOOTSEL + RESET (RUN) tactile | Currently on the Waveshare adapter at the panel |

Roughly **35 additional placements**, essentially all 0402/0603 passives that
JLC places for fractions of a cent. The board goes from 32 placements to ~67.

### 2.2 Prices, checked 2026-09-07

| Part | LCSC | Stock | @ qty 1 |
|---|---|---|---|
| RP2350A | C42411118 | 9,585 | **$1.299** |
| RP2354A (2 MB internal flash) | C41378174 | 16,303 | **$1.269** |
| ABM8-272-T3 12 MHz crystal | C20625731 | 9,590 | **$0.147** |
| AOTA-B201610S3R3-101-T inductor | C42411119 | in stock | **$0.145** |

The crystal and the inductor sit at C42411118/**C42411119** — adjacent to the MCU.
Raspberry Pi and JLCPCB stocked the whole recommended set together on purpose;
this is the supported path, not a workaround. Everything else in §2.1 is a JLC
Basic part or a commodity (LDO, USB-C, flash) at $0.10–$0.35.

**Silicon + passives ≈ $2.50–2.80 per board**, against **~$10–13** for one
RP2350-Tiny-Kit at retail plus shipping.

---

## 3. Manufacturing — does JLC actually place it?

Yes, with one caveat worth $25.

| Constraint | Economic PCBA | Standard PCBA | naub |
|---|---|---|---|
| Min IC pin pitch | **0.40 mm** | 0.35 mm | RP2350A is **0.40 mm** — exactly at the Economic floor |
| QFN / leadless | supported | supported | ✓ |
| X-ray on hidden joints | "certain parts, such as BGA" | comprehensive | QFN joints are invisible after reflow |
| Sides | single-sided only | single & double | ✓ single (bottom) — unchanged |
| Qty | 2–50 | 2–80,000 | ✓ |
| Setup | $0 | $25 (one-sided) | — |
| Extended-part feeder | $1.50/type (was $3, cut 2025-12-19) | per type | ~7 new extended types ≈ **$10.50**, once per order |

**Recommendation: pay for Standard PCBA on the first spin.** A 0.4 mm-pitch
QFN-60 with an exposed pad, sitting exactly on the Economic tier's stated limit,
with solder joints you cannot inspect by eye, on a board where the MCU is the
part you least want to debug — $25 for X-ray coverage is the cheapest insurance
in this whole document. Drop to Economic once a spin has come back good.

Everything else about the order is unchanged: **2 layers, 56 × 100 mm, 1.6 mm,
bottom-side assembly**. The board still fits inside 100 × 100 mm, so it stays in
the cheapest fab tier. No 4-layer upgrade is required — the official Minimal
boards are deliberately 2-layer, single-sided, "without using unnecessarily
exotic PCB technologies."

---

## 4. The risks, and what buys each one down

### 4.1 The regulator layout is mandated, not suggested

This is the one genuinely new engineering risk, and it deserves to be read in
Raspberry Pi's own words: the RP2040's core LDO became a **switching** regulator
on RP2350, so the chip now needs an external inductor, and the magnetic field of
a 0806 inductor mounted *the wrong way round* measurably upsets the regulator's
own output capacitor and control loop. Abracon makes the recommended part with a
polarity dot, reeled all-aligned, specifically because of this. RPi:

> *"if you choose not to use our example, then you do so at your own risk."*

**Mitigation: copy the reference layout verbatim.** Import the RP2350A Minimal
KiCad design, lift the regulator + crystal block as a unit, and do not
re-place a single component in it to make routing nicer. Treat it the way §4.2 of
PCB_DESIGN already treats the ST7565 charge-pump pairing — a transcription job,
not a design job. Budget the layout time for the *other* 90% of the board.

### 4.2 There is no longer a known-good module

Today, if the board is dead, the module is probably fine. Natively, you own the
crystal, the regulator and the flash.

**Mitigation: the bootrom is a better smoke test than the module ever was.** With
no valid flash image the RP2350 enumerates as a USB mass-storage device. So one
USB plug answers "is the chip alive, is the crystal oscillating, is the regulator
regulating, is USB routed correctly" — before the display is committed, which is
exactly the irreversible step. Compare with today, where the board **cannot be
powered at all** until U1 is soldered (PCB_DESIGN §7.2). This is a net
*improvement* in bring-up, not a regression.

The existing bring-up hooks keep their value: `JP_3V3` still splits the LDO from
the display, and `TP_3V3`/`TP_VSYS` still take a bench supply. `TP_VOUT` remains
the highest-value pad on the board for the same reason as before.

### 4.3 USB impedance on a 1.6 mm board

RPi's 90 Ω differential geometry (0.8 mm traces, 0.15 mm gap) assumes a **1 mm**
board. naub is 1.6 mm, and going thinner is not free — a 100 mm board carrying
four encoders that get twisted by hand wants the stiffness.

**Accept it.** RPi's own guidance for a thicker board is "ignore it, and hope for
the best… USB FS can be quite forgiving." More to the point, the thing being
replaced is **15 cm of 0.5 mm FPC** carrying the same pair. Any on-board routing
is strictly better than what's there now. Keep the pair short, keep solid ground
directly under it for its whole length, and don't cross a split.

### 4.4 Where does USB-C physically enter?

The one question this study can't close, because it belongs to the enclosure —
and `CAD/V2` is still open ("Board outline + control placement… needs enclosure
CAD first").

- **(a) USB-C on the main board, at a board edge.** Fewest parts, best signal,
  nothing to hand-wire, JLC places the connector. Requires a board edge to reach
  the back panel.
- **(b) Panel-mount USB-C breakout, 4 short wires** (VBUS, D+, D−, GND) to the
  main board. Keeps today's panel freedom. The **Adafruit 4090** — the V1 part
  that V2 retired — does exactly this and already carries the CC resistors.
  Still far better than the FPC, since the wires are short and you choose the
  routing.

Either way: **put BOOTSEL and RESET on the main board.** They cost two tactiles
and close PCB_DESIGN §7.5 permanently.

### 4.5 The 0.4 mm QFN is at the Economic tier's floor

Covered in §3. $25 and a spin.

---

## 5. Chip choice: RP2350A + flash, or RP2354A?

The RP2354A is the same QFN-60 with **2 MB of stacked flash**, in stock, and
**$0.03 cheaper than the RP2350A**. It deletes the flash chip, its decoupling cap
and six QSPI traces. That is genuinely tempting.

Space is not the objection. The V2 device payload is **159 KB** of Python across
17 modules — CircuitPython itself takes ~1.1 MB, leaving ~800 KB of CIRCUITPY on a
2 MB part, which is 5× what naub needs.

The objection is the firmware path:

| | RP2350A + W25Q32 (4 MB) | RP2354A (2 MB internal) |
|---|---|---|
| Parts | +1 (flash) | — |
| Flash size | **4 MB — same as Pico 2 and same as the Tiny** | 2 MB |
| CircuitPython | stock **Pico 2** `.uf2` very likely runs as-is (12 MHz crystal + 4 MB flash is exactly the Pico 2 config) — **verify on the first board** | needs a custom board definition and a from-source build |
| Cost | +$0.35 | −$0.03 |

**Recommendation: RP2350A + W25Q32 for the first spin.** Matching the Pico 2's
crystal and flash keeps firmware a *download* rather than a build, which means
the first spin has exactly one new unknown (the hardware) instead of two. Revisit
the RP2354A at a later revision, when you're building CircuitPython anyway.

> Caveat to verify, not assume: the stock Pico 2 build reserves GP23/24/25/29 for
> the Pico's own SMPS-mode, VBUS-sense, LED and VSYS-ADC functions, so those pin
> names may be missing or mapped oddly. §6 sidesteps this entirely.

---

## 6. The pin map gets *better*

30 GPIO, none spent, none missing. naub needs 23.

**Map the whole design onto GP0–GP22 and leave GP26–GP29 as the ADC/fader
reserve.** That is 23 pins for 23 needs, exactly, and it:

- **deletes the `ENC3_SW GP16 → GP23` remap** — the wart the Tiny forced, still
  listed as an open firmware task in PCB_DESIGN §0 ⏳;
- **avoids GP23/24/25/29 entirely**, which is precisely the set the stock Pico 2
  CircuitPython build treats as special (§5 caveat — closed by construction);
- keeps GP16 as an ordinary GPIO (no WS2812 stealing it), so the V1 pin map's
  `ENC3 A/B/SW = GP14/GP15/GP16` **comes back verbatim**;
- leaves GP26–28 for the fader and GP29 as a spare ADC, as PCB_NOTES intends.

Net firmware effect: closer to V1 than the current V2 plan is. One less remap to
remember at bring-up.

---

## 7. What it costs in work

The board is unrouted, so this is additive, not rework.

| Task | Effort | Notes |
|---|---|---|
| Import RPi's RP2350A Minimal KiCad reference | 1 h | Footprint + regulator + crystal block come with it |
| Lift regulator/crystal block verbatim into the layout | 2–3 h | §4.1 — transcription, not design |
| Add MCU/flash/USB/boot to `pcb/naub_v2.py` | 2–3 h | 56 parts → ~90; the SKiDL script is the netlist truth |
| Re-derive pin map onto GP0–GP22 | 1 h | §6; simplifies rather than complicates |
| Layout + route the new block | 4–6 h | Bottom side, alongside the existing 32 placements |
| USB-C entry decision + enclosure knock-on | — | §4.4, gated on `CAD/V2` |
| Doc rework | 2 h | PCB_DESIGN §0/§1/§5, PCB_NOTES, BOM_V2, BRINGUP.md, `export_fab.py` |

**≈1–2 focused days.** The single largest line item is a block you are explicitly
forbidden from improvising, which makes it predictable work rather than
open-ended work.

Deleted work, for balance: the §0 ⏳ 1:1 footprint print-and-check, the
hybrid-footprint verification against DXF/STEP/photos, the FPC routing
constraint, and the §1.2 assembly-order procedure.

---

## 8. Recommendation

**Go native, on RP2350A + W25Q32, on the existing 2-layer 1.6 mm board, assembled
Standard on the first spin.** Decide USB-C entry (§4.4) with the enclosure, since
that is the only open dependency.

The financial case is thin at hobby quantities and honest about it: ~$10 of
feeder fees against ~$8–10 per board saved, so it pays back around the tenth
board. The case that actually carries is that it deletes thirteen blind solder
joints under a display that cannot be removed, gives the board a reset button,
gives back four GPIO and a pin remap, and replaces a fragile 15 cm FPC with
copper — in exchange for one block of layout you copy rather than invent.

### If it's a no

The fallback is not "keep everything." Two of the module's costs are fixable
cheaply and independently, and both are worth doing regardless:

1. **§0 ⏳ 1:1 footprint check** is still mandatory before any fab order.
2. **§1.2 assembly-order procedure** is still the thing most likely to ruin a
   built board.

### Open items if it's a yes

- [ ] Download and diff the **RP2350A Minimal R4** KiCad reference; confirm the
      regulator block imports cleanly into the project's design rules.
- [ ] Confirm **W25Q32 part number + LCSC stock** and that JLC has it as a
      placeable part (16 MB W25Q128 is the reference's choice; 4 MB is the Pico 2
      parity choice).
- [ ] **USB-C entry: (a) board edge or (b) panel breakout** — needs `CAD/V2`.
- [ ] Verify the **stock Pico 2 CircuitPython `.uf2`** enumerates and exposes
      `board.GP0`–`GP22` on the first board (§5).
- [ ] Re-derive the pin map onto **GP0–GP22** and update `pcb/naub_v2.py`.
- [ ] Confirm **Economic vs Standard** at order time against JLC's then-current
      pitch limits.
- [ ] Decide whether the existing **bring-up hooks** (§7 of PCB_DESIGN) change —
      `TP_VSYS` injection is less critical once the board can self-power, but
      `TP_VOUT` and `JP_3V3` are untouched.

---

## Sources

- Hardware design with RP2350 (rev 2026-07-03, `RPI-RP2350A-MINIMAL_R4`) — <https://datasheets.raspberrypi.com/rp2350/hardware-design-with-rp2350.pdf>
- RP2350 now available at JLCPCB (RPi announcement; names the stocked crystal + inductor) — <https://www.raspberrypi.com/news/rp2350-now-available-at-jlcpcb/>
- RP2350A on LCSC (C42411118) — <https://www.lcsc.com/product-detail/C42411118.html>
- RP2354A on LCSC (C41378174, 2 MB stacked flash) — <https://www.lcsc.com/product-detail/C41378174.html>
- ABM8-272-T3 12 MHz crystal on LCSC (C20625731) — <https://www.lcsc.com/product-detail/C20625731.html>
- AOTA-B201610S3R3-101-T inductor on LCSC (C42411119) — <https://www.lcsc.com/product-detail/C42411119.html>
- JLCPCB PCB assembly capabilities (Economic vs Standard pitch/QFN/X-ray limits) — <https://jlcpcb.com/capabilities/pcb-assembly-capabilities>
- JLCPCB assembly price breakdown — <https://jlcpcb.com/help/article/pcb-assembly-price>
- CircuitPython Pico 2 build — <https://circuitpython.org/board/raspberry_pi_pico2/>
</content>
</invoke>
