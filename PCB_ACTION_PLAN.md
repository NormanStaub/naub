# PCB Action Plan — V2 pre-fab

**Created:** 2026-07-24, from a full review of `pcb/naub_v2.py`, `pcb/naub_kicad.kicad_pcb`,
all four `pcb/naub.pretty` footprints, the project design rules, and a KiCad 10 `kicad-cli pcb drc` run.
**Companion docs:** [PCB_DESIGN.md](PCB_DESIGN.md) (electrical truth) · [PCB_NOTES.md](PCB_NOTES.md) (mechanical/decisions) · [BOM_V2.md](BOM_V2.md)

> This is the punch list to clear before ordering the bare board. Items are ordered by
> consequence: §1 costs a respin or a ruined assembly, §2 is worth fixing before the
> gerbers go out, §3 is polish. Every number here was measured off the actual files,
> not estimated — coordinates are board mm.
>
> **No electrical defects were found.** The netlist is correct and every finding below
> is mechanical, procedural, or cosmetic.

---

## 0. Snapshot — what the review confirmed is already correct

Recorded so future-you doesn't re-audit it:

- **Netlist ↔ board match is exact.** All 44 nets imported; every U1 and U2 pad carries
  the intended net. `GP16` (onboard WS2812) and `GP26–29` (ADC reserve) are open on
  purpose, confirmed pad-by-pad.
  > **Stale as of 2026-07-24 (bring-up hooks):** the netlist is now **56 parts / 50 nets**
  > and the board has not been re-imported yet — see item **1.4**. `GP26–29` are no longer
  > open; they go to `J_ADC`. Everything else in this bullet still holds.
- **ST7565 charge-pump network** matches the EA app schematic, including the
  `CAP3P ↔ CAP1N` pairing. All 9× 1 µF sit 4–6 mm from their DOG pads — close enough.
- **SPI1 assignment is hardware-legal** (SCL=GP10, SI=GP11); PWM on GP13 is valid.
- **Backlight math** checks out: 3× 300 Ω from VSYS ≈ 10 mA/path, ~31 mA total, ~32 mW
  per resistor.
- **All passives + J1 are on B.Cu**, so nothing on the front fouls the flush display.
- **Encoder frame lugs (SH) are grounded** — ESD path + mechanical retention.
- **DRC has no copper shorts and no clearance errors between components.** The 66
  "unconnected" items are just the unrouted ratsnest.
- **RP2350-E9 does not apply to this design** — evaluated and dismissed 2026-07-24,
  recorded here so it doesn't get re-raised. E9 affects pads configured as inputs with
  the **internal pull-down** enabled: input-mode leakage of up to ~120 µA at
  IOVDD = 3.3 V overwhelms the weak internal pull-down and latches the pad at
  ~2.1–2.2 V. The documented workaround (external **pull-down** ≤ 8.2 kΩ) is for that
  case. Every input here is **active-low with an internal pull-up** — the leakage pushes
  the pin toward IOVDD, i.e. the same direction the pull-up already holds it, and a
  closed switch is a hard short to GND that 120 µA cannot fight. No external resistors
  are required on any of the 17 inputs.
  Sources: [Raspberry Pi forum thread](https://forums.raspberrypi.com/viewtopic.php?t=375631) ·
  [Hackaday: E9 redefined as input-mode leakage](https://hackaday.com/2024/09/20/raspberry-pi-rp2350-e9-erratum-redefined-as-input-mode-leakage-current/)

---

## 1. Critical — address before committing to fab

> **Retracted 2026-07-24:** an earlier version of this plan listed "RP2350 erratum E9 —
> add 8.2 kΩ pull-ups on all 17 inputs" as the top critical item. **That was a mistake.**
> E9 is a pull-*down* erratum and does not affect this design's active-low/pull-up
> inputs — see §0 for the full reasoning and sources. No electrical change is needed.
> The remaining critical items are all mechanical, and **§1.1 is now the item most
> likely to force a respin.**

### 1.1 Display glass overhangs the left board edge by ~0.3 mm
**Status:** [ ] not started

Board outline is x = 125.48 → 181.48. The DOGM128 pin rows are centered on that, but
the glass body is **asymmetric relative to its pins**: U2's F.Fab body spans
x = 125.19 → 180.99, and its courtyard reaches 124.94.

Result: glass overhangs the left edge by **0.29 mm** (courtyard by 0.54 mm) while
**0.49 mm** goes spare on the right.

Action: widen the outline ~0.6 mm on the left, *or* shift the outline ~0.4 mm left
relative to the component set. **Mirror whichever fix into the enclosure CAD**
(`CAD/V2/*.dxf`) — this moves the panel-to-board registration.

### 1.2 U1's front-side solder fillets sit under the flush backlight
**Status:** [ ] not started — this is an assembly-order constraint, not a file fix

U1's 13 back-pad solder-through holes (GP17–23, 3V3, GND, SWD trio) must be soldered
from the board's **front**, and they land at x ≈ 146–161, y ≈ 70–91 — dead center under
the LED55X46 backlight, which rests flat on the PCB between the display's two pin rows.
Any fillet proud of the surface will rock or lift the display. The 23 edge-ring holes
carry the same risk if solder wicks through.

Bake into the build procedure:
1. Solder U1 **first**, from the front, before anything else is in the way.
2. Wick and dress **every** front-side joint in that region flush to the board.
3. **Dry-fit the backlight and display** before soldering the display's 26 pins —
   that step is effectively irreversible by hand.
4. If you want margin: a thin adhesive shim frame lifting the display ~0.5 mm. The
   DOG pin length allows it.

### 1.3 Empty-schematic footgun
**Status:** [ ] not started

`pcb/naub_kicad.kicad_sch` is an empty placeholder — the workflow is SKiDL netlist
import, not schematic-driven. Two hazards:

- Pressing **F8 / "Update PCB from Schematic"** will offer to delete every footprint
  on the board.
- The 4 mounting holes and the `naub_v2_layout` template footprint are **not** in the
  netlist, so on any netlist re-import, **leave "delete footprints not in netlist"
  unchecked**.

Action: **done** — a loud warning block is now at the top of `naub_v2.py`.

**Also import with "match by reference", not by unique ID.** Component tstamps used to be
randomly regenerated on every SKiDL run (now fixed — see Done §D1), but the tstamps
baked into the *existing* board predate that fix, so ID-based matching would make KiCad
treat all 42 footprints as new and duplicate them.

---

### 1.4 Re-import the netlist — six pads still have no net
**Status:** [ ] **blocked on closing KiCad**, then two steps

The bring-up hooks were added to `pcb/naub_v2.py` on 2026-07-24 (PCB_DESIGN §7,
procedure in [BRINGUP.md](BRINGUP.md)). The 14 footprints **are** on the board and have
been repositioned (below), but the import was only partial.

> #### ⚠ Six pads carry no net. One of them is functional.
>
> Diffing all 183 board pads against the 182 netlist nodes:
>
> | Pad | Should be | Consequence |
> |---|---|---|
> | `U1.GP26/27/28/29` | `SPARE_GP26..29` | `J_ADC` is connected to nothing |
> | **`R_BL3.1`** | **`VSYS_BL`** | **one backlight path is orphaned from `JP_BL` — it would never light** |
> | `TP_3V3L.1` | `+3V3_LCD` | the load-side rail pad is dangling |
>
> Everything else matched, including both rail splits on `C_LCD_VDD`, `U2.34/35`,
> `R_BL1.1` and `R_BL2.1` — so the import mostly took, it just didn't finish. All six
> are currently hiding among DRC's 79 unconnected items. **A re-import fixes all of
> them**; `R_BL3` is the one that would otherwise reach fab.

#### Step 1 — placement (scripted, validated, not yet applied)

`pcb/place_bringup_hooks.py` repositions 13 parts and tents 21 front-side pads. It has
been validated against a copy of the board but **not applied — KiCad holds the file
open.** Close KiCad, then:

```
cd pcb && python place_bringup_hooks.py naub_kicad.kicad_pcb
```

Safe to re-run (absolute positions, idempotent mask edit). Why it's needed: the first
placement followed §7's constraints literally, but those were written without
accounting for **U1 being placed at rot=180**, which mirrors the module — GP26–29 end
up on its *right* (x≈161–162) and GP8–GP13 along its *bottom* (y≈91). Both headers
therefore sat on the far side of the board from the pins they tap. Straight-line
ratsnest for the 14 debug parts: **374 mm → 125 mm**. It also fixes two real
collisions the original placement had: `J_LCD` overlapping `SW4.S1` by 0.26 mm, and
`TP_BLPWM` sitting on the M2 mounting hole at (163.23, 61.00).

After applying: min pad-to-pad gap 0.95 mm, **zero hard-geometry DRC violations**.

#### Step 2 — re-import in KiCad

Then re-import `naub_v2.net` to pick up the six missing nets and the revised `J_LCD`
pin order.

Netlist went **42 parts / 44 nets → 56 / 50**:

| Added | What |
|---|---|
| `JP_3V3`, `JP_BL` | 0 Ω rail links — **ship stuffed**, they are a service seam |
| `TP_VOUT` `TP_V0` `TP_VSYS` `TP_3V3` `TP_3V3L` `TP_GND1/2/3` `TP_BLPWM` `TP_BLK` | 10 THT probe pads |
| `J_LCD`, `J_ADC` | two unpopulated 1×6 rows |

Two existing nets were **split**, which is the only part of this that touches the
functional circuit:

- `+3V3` → keeps `U1.3V3`/`C_BULK`/`C_DEC`; new `+3V3_LCD` takes `C_LCD_VDD`/`U2.34`/`U2.35`
- `VSYS_5V` → keeps `U1.VSYS`; new `VSYS_BL` takes `R_BL1/2/3`

Plus 4 new `SPARE_GP26..29` from `J_ADC`. **Verified by normalized netlist diff: the other
39 nets are node-for-node identical.** No functional connection changed.

Watch out on import:

- **Match by reference, not unique ID** (same reason as §1.3).
- Leave **"delete footprints not in netlist" unchecked** (mounting holes + the layout
  template live only on the board).
- `J_LCD`'s pin order changed to **GND, SI, SCL, A0, RST, CS** so the five signal pins
  land at dx = 0.00 from `U2.36–40` — a zero-crossing comb of identical 4.78 mm stubs.
  Until you re-import, the board still has the old order and the ratsnest will look
  tangled. That is expected.
- The two rail splits mean KiCad re-assigns `+3V3` and `VSYS_5V` on existing copper —
  **re-check the ratsnest around U2 and the backlight resistors afterwards.**

#### Step 3 — refill the ground zones

Not optional here, and not merely hygiene (cf. §2.2). Until you refill, DRC reports
**~94 clearance / hole-clearance / solder-mask-bridge violations against the stale
`b.gnd_zone` fill**. They are all artifacts of the new pads landing in an unfilled
zone; the underlying geometry is clean. Refill and they vanish.

Expect **+11 `lib_footprint_mismatch`** to remain, and leave it: those are the tented
pads, which now legitimately differ from the stock library footprints. Excluding it
from DRC (§2.4) is fine; "fixing" it by reloading from the library would silently undo
the tenting.

---

## 2. Should fix before ordering

### 2.1 Mounting-hole screw-head clearance is zero
DRC flags courtyard overlaps on all four holes. Measured:
- Top pair (y = 61.004): 2.6 mm below the display glass edge → an M2 pan head
  (Ø 3.8–4 mm) clears the glass by only ~0.7 mm.
- Bottom pair (y = 127.396): sits exactly between the Choc bodies → **0 mm** margin
  against the switch housing.

Action: use **countersunk or low-head M2** screws, or nudge the bottom pair toward
board center. Also annotate the holes as **H1–H4** instead of leaving them `REF**`.

### 2.2 Refill the ground zones
Both GND pours currently violate the 0.5 mm copper-to-edge rule — the fills predate the
current rule/outline. Press **B** to refill before generating gerbers.

While in there: the **F.Cu zone is copy-paste-named `b.gnd_zone`**. Rename it to
`f.gnd_zone`.

### 2.3 Give the power nets their own netclass
Everything is currently in the 0.2 mm **Default** class. Electrically fine at <100 mA,
but assign `+3V3`, `VSYS_5V`, and `GND` a **0.5 mm** netclass — free robustness and
much easier hand-rework.

### 2.4 DRC hygiene, so real errors don't drown
- **36 × `pth_inside_courtyard`**: all U1 holes inside U2's courtyard. This is the
  intended front/back stacking — add them as **DRC exclusions** now.
- **10 × `silk_overlap` + 3 × `silk_edge_clearance`**: needs a silkscreen cleanup pass.
  Named offenders: the U2 outline segments and `C_ENC1_A`'s reference field.
- **1 × `lib_footprint_mismatch`** on `naub_v2_layout` — the board copy differs from
  `naub.pretty`. Re-sync or add to the ignore list.

### 2.5 Verify the SWD row position against a physical module — highest respin risk
Already flagged as ⏳ in `PCB_DESIGN.md` §0, repeated here because it is **the single
most likely cause of a fab respin**: the official Waveshare DXF puts the SWD trio
mid-board, but the wiki render and product photos put it 1.08 mm below the FPC edge
(±0.15 mm), and the footprint follows the photos.

Action: **1:1 print the footprint, lay the actual module on it**, before ordering.

### 2.6 J1 height on the back
A vertical 2.54 mm header stands ~9 mm off the back — likely the tallest rear component
(the module itself is ~4 mm). If the enclosure back is tight, leave J1 unpopulated and
pogo-pin the holes, or fit a right-angle / low-profile part.

---

## 3. Minor / bookkeeping

### 3.1 A/B pull-up footprints — CLOSED, decided against
**Decision 2026-07-24: ship as-is. No external pull-ups, no DNP footprints.** Nothing to
do here; this entry exists so the question isn't reopened from scratch.

`PCB_DESIGN.md` §2 originally recommended 8× 10 kΩ DNP pull-up footprints as fast-spin
insurance, and §6 listed them as if they existed. They were never added to `naub_v2.py`
or the board, and we are **not** adding them. Both doc locations are now corrected — see
**`PCB_DESIGN.md` §2.1**, which is the authoritative record and holds the full contingency
plan.

Short version, so this file stands alone:

- **Not an E9 issue** (see §0) — just the plain RC low-pass of 10 nF against the internal
  50–80 kΩ pull-up, τ ≈ 500–800 µs.
- **The speed ceiling is ~10 rev/s (~600 RPM)**; a hard hand-flick is 3–5 rev/s, so
  there's ~5–6 τ of margin per half-period. Not worth 8 parts and 16 pads on a crowded
  2-layer board.
- **Symptom if wrong:** missed or jittery counts *only when spinning fast*, with slow
  rotation perfect. (Slow-speed misbehaviour = firmware bug, not this.)
- **Fallbacks, neither needing a board change:** swap the 8 A/B caps 10 nF → 4.7 nF
  (τ ≈ 250 µs, ceiling → ~20 rev/s), or decode quadrature via a fault-tolerant state
  machine / RP2350 **PIO**. Hand-tacking 10 kΩ resistors is a viable last resort.

This is why the absent footprints are a non-issue rather than a gamble: the primary fix
is a parts swap on footprints that already exist.

### 3.2 Everything else

- ~~**Doc drift to reconcile**~~ — **done 2026-07-24**, see Done §D2.
- **Delete the stray half-project.** Untracked empty `pcb/naub.kicad_pcb`,
  `pcb/naub.kicad_pro`, `pcb/naub.kicad_sch` and `pcb/naub/` sit next to the real
  `naub_kicad.*` files — someone will eventually open the wrong board.
- **Stray SKiDL output at the repo root.** `naub_v2.net`, `naub_v2.log`, `naub_v2.erc`,
  `naub_v2_sklib.py` sit in the project root as well as in `pcb/` — dropped there by a
  run of the script from the wrong working directory. The root copies are **stale**
  (they predate the bring-up hooks) and a stale netlist is exactly the kind of file that
  gets imported by accident. Safe to delete; they regenerate with
  `cd pcb && python naub_v2.py`. `pcb/.gitignore` already covers the `pcb/` copies but
  its patterns don't reach the root — worth a root-level ignore rule too.
- **No fiducials**, though `PCB_NOTES.md` lists them. Skippable for hand assembly;
  cheap to add if a pick-and-place run is ever wanted.
- **Knob diameter ceiling:** encoder pitch is 19.5 mm (SW1→SW2) and 19.0 mm (SW2→SW3),
  so knobs must stay under **~18 mm Ø**.

---

## Done — 2026-07-24

Netlist-side work completed during the review. **No connections changed:** the generated
netlist was verified electrically identical (all 42 components' ref/value/footprint, all
44 nets, every node) before and after every edit below.

### D1. `naub_v2.py` netlist is now reproducible
SKiDL derives each component's KiCad tstamp (unique ID) from its `tag`. No tags were
being passed, so SKiDL invented a **random** one per part per run — every regeneration
produced 42 different tstamps, making the netlist non-reproducible and any diff pure
noise. That directly defeated the script's own stated purpose ("review the nets here —
diff-able, version-controlled").

Fix: pass `tag=ref` in all 8 part factories (every part already had a unique explicit
ref). Verified by generating twice and diffing: **byte-identical across runs.** The 42
`Missing tag` warnings in `naub_v2.log` are gone; one residual warning for an unnamed Net
object is harmless and doesn't affect component tstamps.

⚠ Follow-on: see §1.3 — import with **match by reference**, since the board's existing
tstamps predate this fix.

### D2. Stale counts and dead J2 references corrected
- `naub_v2.py`: the docstring's "Expansion header J2 gains GP29" and the aux-button
  comment's "J2 keeps its 4 ADC pins GP26-29" both described a header that no longer
  exists. Rewritten to state J2 is gone and to explain the resulting net count.
- `PCB_DESIGN.md`: **"42 parts, 48 nets" → "42 parts, 44 nets"** in two places. The 48
  predated dropping J2, which removed its four GP26–29 nets. Part count legitimately
  stayed 42 because J2 (−1) was traded for SW9/BTN5 (+1). Both numbers re-counted from
  the generated netlist, not assumed.
- `PCB_DESIGN.md` §1: added the missing **GP22 → BTN5** table row; corrected
  "Used: 22 GPIO / aux buttons 4" → **23 / 5** and "Spare: 5 GPIO" → **4** (GP26–29
  only). §6 passives table: aux buttons **4 → 5**, pins `GP6/7/20/21` → `GP6/7/20/21/22`.

### D3. The board is routed
`pcb/route_board.py` (with `pcb/board_model.py`) routes the whole board headlessly and
was applied to `naub_kicad.kicad_pcb`. **85 of 85 connections, 0 unrouted, 0 unconnected
items in DRC.** 779 segments (580 B.Cu / 199 F.Cu), 24 vias, 1120.8 mm of copper.

Why not Freerouting: `kicad-cli` 10.0.3 has no Specctra DSN export, so the usual
round-trip needs a GUI operator, and the installed JRE is 8 where Freerouting 2.x wants
21. Routing directly also let the strategy be board-specific — GND is never routed (it
is in the pours, 58 of 232 pads free), B.Cu is preferred because 46 of 61 footprints are
there, and vias are priced at a 5 mm detour.

**DRC: 120 violations, vs the 115 baseline before routing — a delta of exactly 5, and
zero hard geometry** (no clearance, hole, shorting, dangling, or co-located-drill hits).

The 5 are `starved_thermal`: `U1.GND` ×3, `SW1.SH`, `C_DEC.2` reach the pour through one
thermal spoke where the rule wants two. They are **connected** — this is spoke count, not
continuity. The cause is pad-field density around U1 rather than trace crowding, which was
established by sweeping the GND keep-out: 0.45 mm gives 5, 0.60 gives 6, 0.75 gives 5. If
they bother you, the options are to set those pads' zone connection to solid (better for
ground, worse for hand-soldering — and this board is hand-soldered, see BRINGUP.md), or
drop `min_resolved_spokes` to 1. Leaving them is defensible on a first spin.

⚠ **The router optimises path cost and nothing else.** It does not know that the
charge-pump caps around U2 want small loop area (`PCB_DESIGN` §4.2) or which nets are
noisy. Read the result before trusting it; §2.3 netclasses would be the lever if the
power nets want wider copper.

Four of my own bugs were caught by DRC rather than by inspection, which is worth
recording because they are the failure modes of this kind of script: oval pads rasterised
as rectangles (5 track ends in corners that do not exist), grid quantisation leaving
0.18–0.198 mm against a 0.20 mm rule, stitching vias landing on top of vias `commit()`
had already placed, and — the quiet one — pad access cells keyed by `(ref, num)` when
**33 pad names on this board are reused inside their own footprint**, `U1.3V3` alone
being three physically separate pads. That last one silently dropped a pad from routing.

This also closes **§1.4** (netlist re-imported, all 6 pads have nets) and **§2.2** (zones
refilled; `kicad-cli pcb drc --refill-zones --save-board` does it without the GUI).

---

## Suggested order of work

1. **§2.5 1:1 module print** — a physical check that gates ordering, and it may itself
   force a footprint edit, so do it first.
2. **§1.1 outline fix + §2.1 mounting holes** — both move geometry; settle them together
   and push the result into the enclosure CAD.
3. ~~§3.1 pull-up sites~~ — **closed, decided against.** No action.
4. **§1.4 re-import the netlist** and place the 14 bring-up parts. Do this *before*
   routing, not after — that is the entire reason they were added while the board was
   still unrouted, and their placement constraints (PCB_DESIGN §7) shape the routing
   rather than the other way round.
5. ~~**§2.2**~~ — **done, see D3.** **§2.3 / §2.4** — netclasses and DRC cleanup remain.
6. ~~**Route the board.**~~ — **done, see D3.** Re-routing after any placement change is
   one command (`python route_board.py naub_kicad.kicad_pcb out.kicad_pcb`), so items 1–2
   above are still cheap to act on: they invalidate the routing, not the effort.
7. **§3.2** stray-file cleanup (the doc reconciliation part is already done — see D2).
8. **§1.2** goes into the build/assembly procedure — now written up as
   [BRINGUP.md](BRINGUP.md). **§1.3** is a standing hazard note, nothing to execute.
