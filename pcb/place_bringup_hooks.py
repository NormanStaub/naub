#!/usr/bin/env python3
"""Place the bring-up hooks (PCB_DESIGN.md §7) on naub_kicad.kicad_pcb.

    python place_bringup_hooks.py naub_kicad.kicad_pcb

CLOSE KICAD FIRST. KiCad keeps the board in memory and rewrites the whole file on
save, so editing it underneath a running instance loses either your work or this
script's. The .lck file next to the board tells you whether it is open.

WHAT THIS IS
    A one-shot repositioning, applied 2026-07-24, kept in the repo because it is the
    record of WHERE the debug hardware goes and WHY. Safe to re-run: positions are
    absolute and the mask edit only matches pads that are not already tented, so
    running twice changes nothing.

WHY THE PARTS MOVED
    They were first placed with the constraints from PCB_DESIGN §7 read literally --
    J_ADC on the left edge, J_LCD on the right, ten pads in a tidy 2x5 field. That
    was written without accounting for U1 being placed at rot=180, which mirrors the
    module: GP26-29 end up on the module's RIGHT (x~161-162) and GP8-GP13 along its
    BOTTOM (y~91). So both headers sat on the opposite side of the board from the pins
    they tap, and the display/backlight pads sat 18-45 mm from their nets.

    Straight-line ratsnest for the 14 debug parts: 374 mm before, 125 mm after.

VERIFIED AFTER APPLYING (see the checks in the commit that added this file)
    - min pad-to-pad gap among the moved parts: 0.95 mm (was a -0.26 mm overlap of
      J_LCD on SW4.S1, and TP_BLPWM sitting on the M2 mounting hole at 163.23,61.00)
    - zero hard-geometry DRC violations (clearance / hole_clearance / hole_to_hole /
      solder_mask_bridge) once the ground zones are refilled
    - the ~94 remaining zone-clearance hits are the STALE ZONE FILL, not geometry:
      refill both zones (PCB_ACTION_PLAN §2.2) and they go away
    - +11 lib_footprint_mismatch is expected and intentional: the tented pads below
      legitimately differ from the stock library footprints
"""
import re
import sys

# ref -> (x, y, rot).  1x06 header: rot 0 = pins run UP (-y); rot 270 = pins run RIGHT (+x)
MOVES = {
    # J_ADC vertical. fy=85.58 lines pin1..pin6 up EXACTLY opposite U1's left-edge
    # column at x=161.12 -- GP26/GP27/GP28/GP29/3V3/GND on the same 2.54 pitch, in the
    # same order. Six straight 3.6-4.9 mm stubs, no crossings. (Was x=128, the far side
    # of the board: 33-35 mm x4, ~46% of all the added routing on its own.)
    'J_ADC':    (166.00,  85.58,   0),

    # J_LCD horizontal comb directly above U2's pin row. x=164.92 is one pitch left of
    # U2.36, which puts the five signal pins at dx=0.00 from their display pads --
    # five identical 4.78 mm vertical stubs, zero crossings. This is why GND leads the
    # row in naub_v2.py: with GND last the row must start at U2.36's x and the far pin
    # lands 0.47 mm from the board edge.
    'J_LCD':    (164.92, 102.00, 270),
    'JP_3V3':   (164.50,  98.00, 180),   # displaced by J_LCD; still between C_DEC and C_LCD_VDD
    'TP_3V3L':  (168.00,  98.50, 180),

    # Display / charge-pump diagnostics, gathered to the pins they diagnose.
    # TP_V0 was 43.5 mm from U2.21; TP_VOUT was 18.6 mm from U2.32.
    'TP_V0':    (128.50, 103.00, 180),
    'TP_GND3':  (142.00, 101.50, 180),   # by U2.26, the GND in the middle of the bias row
    'TP_VOUT':  (157.00, 102.00, 180),

    # Backlight. The cathode side of this circuit is all top-left (Q1 at 133,57 and
    # U2.18-20 at y=63.6); TP_BLK used to sit 45.2 mm away on the right.
    'TP_BLK':   (131.00,  66.50, 180),
    'TP_BLPWM': (159.50,  59.00, 180),   # NOT (162,61.5): that lands on the M2 hole at (163.23,61.00)

    # Rails: two bench-supply injection pairs, 4.0 mm apart, beside U1's VSYS/3V3 edge
    # pads. TP_VSYS was 26.4 mm from U1.VSYS despite JP_BL being 3.3 mm from it.
    'TP_VSYS':  (170.50,  73.00, 180),
    'TP_GND1':  (174.50,  73.00, 180),
    'TP_3V3':   (170.50,  78.00, 180),
    'TP_GND2':  (174.50,  78.00, 180),
}

# Tent the FRONT solder mask on every THT debug pad that ends up under the display.
#
# The display body spans x 125.19-180.99, y 63.69-106.69 -- essentially the whole
# board -- and the only glass-free band (y 107.8-110.9, between U2's pin row and the
# Choc pins) is 3.3 mm tall and blocked at x>166 by SW4. There is nowhere else to put
# these, so the exposure is managed instead of avoided: with F.Mask removed, the
# front-side annular ring is covered and cannot sit proud under the glass or collect
# solder wicking through from a wire soldered in from the back. Same failure mode as
# PCB_ACTION_PLAN §1.2 (U1's front-side fillets), same region of the board.
#
# TP_BLPWM is the one THT hook left untented -- at y=59.0 it is clear of the display.
TENT_FRONT = {'J_ADC', 'J_LCD', 'TP_VSYS', 'TP_GND1', 'TP_3V3', 'TP_GND2', 'TP_3V3L',
              'TP_V0', 'TP_GND3', 'TP_VOUT', 'TP_BLK'}


def main(path):
    s = open(path, encoding='utf8').read()
    chunks = re.split(r'(\n\t\(footprint )', s)
    moved = tented = 0
    for i, c in enumerate(chunks):
        m = re.search(r'\(property "Reference" "([^"]+)"', c)
        if not m:
            continue
        ref = m.group(1)
        if ref in MOVES:
            x, y, r = MOVES[ref]
            c = re.sub(r'(\n\t\t\(at )[-\d.]+ [-\d.]+(?: [-\d.]+)?(\))',
                       lambda z: f"{z.group(1)}{x} {y}" + (f" {r}" if r else "") + z.group(2),
                       c, count=1)
            moved += 1
        if ref in TENT_FRONT:
            c, n = re.subn(r'\(layers "\*\.Cu" "\*\.Mask"\)', '(layers "*.Cu" "B.Mask")', c)
            tented += n
        chunks[i] = c
    open(path, 'w', encoding='utf8', newline='').write(''.join(chunks))
    print(f"moved {moved}/{len(MOVES)} footprints; tented {tented} front-side pads")
    if moved != len(MOVES):
        print("WARNING: not every reference was found — did the board get re-annotated?")
    print("\nNext, in KiCad:")
    print("  1. Re-import naub_v2.net (match by REFERENCE; leave 'delete footprints")
    print("     not in netlist' UNCHECKED). This also fixes the 6 pads that still")
    print("     have no net: U1.GP26-29, R_BL3.1, TP_3V3L.1.")
    print("  2. Refill both ground zones (PCB_ACTION_PLAN §2.2) — until you do, DRC")
    print("     reports ~94 bogus zone-clearance hits against the stale fill.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
