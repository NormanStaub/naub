# naub V2 — Board Bring-Up Procedure

**For:** the first fabricated batch of the V2 PCB
**Written:** 2026-07-24, before the fab order, alongside the debug hooks in `pcb/naub_v2.py`
**Companion docs:** [PCB_DESIGN.md](PCB_DESIGN.md) (why the circuit is what it is) · [PCB_ACTION_PLAN.md](PCB_ACTION_PLAN.md) (pre-fab punch list)

---

## The constraint everything else follows from

**U1 goes on before U2, and U1 cannot come off afterward.**

The module's front-side fillets sit underneath the display (`PCB_ACTION_PLAN` §1.2), and the display solders directly into the board with no connector. So the assembly order is forced, and it is forced in the *unhelpful* direction: the irreversible part goes on top of the expensive part.

The whole procedure below is built around one consequence of that — **prove the module completely while the display is still in its bag.** Every stage before Stage 4 exists to make Stage 4 a decision you make once, with evidence.

## What you have to work with

Added to the netlist 2026-07-24 specifically for this (see `pcb/naub_v2.py`, "Bring-up / debug hooks"):

| Hook | What it is | Use |
|---|---|---|
| `JP_3V3` | 0R link, `+3V3` → `+3V3_LCD` | Strand the entire display |
| `JP_BL` | 0R link, `VSYS_5V` → `VSYS_BL` | Strand the entire backlight |
| `TP_VSYS` / `TP_3V3` / `TP_3V3L` | Rail pads (source, source, load) | Rails; bench-supply injection |
| `TP_GND1/2/3` | Grounds, one per cluster | Probe returns |
| `TP_VOUT` / `TP_V0` | Charge-pump output, bias top | The display fault tree |
| `TP_BLPWM` / `TP_BLK` | Q1 gate drive, Q1 drain | Backlight path |
| `J_LCD` | Unpopulated 1×6 + GND | Logic analyzer on the display bus |
| `J_ADC` | Unpopulated 1×6 | Spare IO / somewhere to land a bodge wire |
| `J1` | 1×3 SWD | Debugger |

All on the back — the service side.

**Bench supply injection points:** 5 V across `TP_VSYS` + `TP_GND1`, 3.3 V across `TP_3V3` + `TP_GND2`. These matter more here than on a typical board because power normally reaches this PCB *through* U1 — VSYS arrives over the adapter FPC and enters at the module's VSYS pad. Without these pads there is no way to energize the board at all before a module is soldered on.

---

## Stage 0 — Bare board, no parts

Do this on **every** board in the batch, before any solder. It takes two minutes and it is the only cheap moment to catch a fab defect.

- [ ] `+3V3` ↔ `GND` — **open**
- [ ] `VSYS_5V` ↔ `GND` — **open**
- [ ] `+3V3` ↔ `VSYS_5V` — **open**
- [ ] `TP_GND1` ↔ `TP_GND2` ↔ `TP_GND3` — **all continuous** (proves the pours actually stitched; a split ground plane on a 2-layer board is a miserable thing to discover later)
- [ ] `JP_3V3` pads open across the gap; same for `JP_BL` (sanity-checks the link footprints aren't bridged by a fab short)
- [ ] Eyeball the display outline against the real glass — the overhang item (`PCB_ACTION_PLAN` §1.1) is a mechanical fit you want to confirm on the bare board, not with a soldered display

> A board that fails Stage 0 is scrap. Set it aside; do not populate it hoping to find the short later.

## Stage 1 — Back-side passives only

All 30 R/C plus Q1. No module, no display, no switches.

- [ ] Solder the passives
- [ ] Re-run the three short checks from Stage 0. **A new short here is a solder bridge you just made**, and you have a much smaller suspect list now than you will at any later point
- [ ] **Open `JP_3V3`** (leave it off for now — 0R links go on last)
- [ ] **Open `JP_BL`**
- [ ] Bench supply → `TP_VSYS`/`TP_GND1`, limit **50 mA**, 5 V. With both links open this should draw essentially nothing. If the supply hits its limit, you have a short on the module side and no module at risk
- [ ] Repeat at 3.3 V into `TP_3V3`/`TP_GND2`

## Stage 2 — Module (U1)

- [ ] **Before soldering:** power the module on its own USB-C adapter, off the board. Confirm it enumerates and takes firmware. A DOA module is far easier to find now than soldered down under a display
- [ ] Solder U1 — castellated edge ring first, then the 13 back solder-through pads
- [ ] `JP_3V3` still **open**
- [ ] Power via USB through the adapter FPC
- [ ] `TP_VSYS` ≈ **5 V**
- [ ] `TP_3V3` ≈ **3.3 V** — this is the LDO with no display load at all. If it is low or absent here, the fault is the module or its solder joints, full stop
- [ ] `TP_3V3L` ≈ **0 V** (confirms `JP_3V3` really is open, i.e. the seam works)
- [ ] SWD through `J1` — debugger connects, halts, reads the chip ID
- [ ] Load firmware; blink the onboard WS2812 on GP16
- [ ] Confirm BOOTSEL and RESET on the adapter both work **through the FPC** — these exist nowhere else on this design (the carrier brings out no RUN pin), so a bad FPC seat costs you both

## Stage 3 — Encoders and keys

Cheap, reworkable, and the last chance to test anything before the irreversible step.

- [ ] Solder the 4 encoders and 5 Chocs
- [ ] Firmware GPIO dump: **all 17 inputs**, one at a time
  - 4 × encoder A/B — rotate slowly, confirm clean quadrature both directions
  - 4 × encoder switch, 5 × Choc — confirm active-low, no chatter
- [ ] **Fast-spin check** — flick each encoder hard and confirm counts don't drop. Missed counts *only at speed*, with slow rotation perfect, is the RC filter; misbehaviour at slow speed is a firmware bug. See `PCB_DESIGN` §2.1 for the fallbacks (first one is a 10 nF → 4.7 nF cap swap, no board change)

> **Do not proceed until every input is clean.** After Stage 4 the module is captive.

## Stage 4 — Display (U2) — the irreversible step

- [ ] Fit `JP_3V3` (0R). Leave `JP_BL` **open** — backlight stays dark for now
- [ ] Measure current on the 3.3 V rail before and after fitting the link. The delta is the display subsystem alone
- [ ] Solder U2. Check the glass overhang and fillet clearance as you go
- [ ] Power up. `TP_3V3L` ≈ **3.3 V**
- [ ] Initialise the ST7565 in firmware, then read **`TP_VOUT`**:
  - **~9–13 V** → charge pump is running. The 9-cap network is correct
  - **≈ VDD (3.3 V)** → pump is not running. Suspect the cap network, especially the non-obvious `CAP3P → CAP1N` pairing (`PCB_DESIGN` §4.2). Every way of getting these wrong looks identical from the front: blank screen
- [ ] `TP_V0` — bias present. Pump good but V0 wrong is a contrast/register problem, not a hardware one
- [ ] Draw something. If the screen is blank with `TP_VOUT` healthy, clip a logic analyzer on **`J_LCD`** and check whether the MCU is transmitting at all — that splits "MCU not talking" from "display not listening" in seconds

## Stage 5 — Backlight

- [ ] Fit `JP_BL` (0R)
- [ ] Drive GP13 PWM. `TP_BLPWM` should swing 0 ↔ 3.3 V
- [ ] `TP_BLK` (Q1 drain) should swing with it. **Gate swinging but drain stuck = dead FET**, not a dead LED
- [ ] Total backlight current ≈ **31 mA** (3 paths × ~10 mA through 3 × 300 Ω). Measure it by lifting one end of `JP_BL` and putting the meter across
- [ ] Check brightness. If you want it different, change `R_BL1/2/3` — the link keeps you from having to guess with the display powered

---

## Before you order

- [ ] **Buy spares.** A second bare board costs almost nothing and having one to sacrifice changes how you debug. Same for a spare module and a spare display — those are the two parts you can't recover from
- [ ] **Diff the carrier footprint against Waveshare's published RP2350-Tiny pinout.** The footprint brings out 36 pads and `RUN` is not among them. If the real module exposes RUN and the footprint simply omitted it, that is a footprint bug worth catching now — and a board-level reset button is worth having on a first spin
- [ ] Confirm `J_ADC` could be placed hard against the module's GP26–29 edge contacts. If it can't be, drop it rather than dragging six traces across a crowded 2-layer board — see the note in `naub_v2.py`
- [ ] Work the rest of [PCB_ACTION_PLAN.md](PCB_ACTION_PLAN.md) §1–2
