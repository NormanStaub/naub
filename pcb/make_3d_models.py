#!/usr/bin/env python3
"""Build the 3D models KiCad is missing, into pcb/naub.3dshapes/.

Four of the parts on this board have no usable model in KiCad's own library:

    SW1-SW4   Bourns PEC11L rotary encoder      - not in the library at all
    SW5-SW9   Kailh Choc V1 key switch          - not in the library at all
    U1        Waveshare RP2350-Tiny module      - not in the library at all
    U2        EA DOGM128-6 display              - the footprint points at
                                                  Display.3dshapes/EA_DOGM128-6.step,
                                                  which KiCad 10 does not ship

Manufacturer STEP files for all four exist in the CAD parts bin (see
SOURCE_DIR). None of them is in KiCad's coordinate convention, and one of them
is the wrong variant. This script fixes that and writes the corrected models
where the footprints can find them.

KiCad's convention, which every model here is transformed into:

    origin  = the footprint origin
    z = 0   = the board surface the part is soldered to
    +z      = away from the board
    +x      = the footprint's +x
    +y      = the footprint's MINUS y

That last line is the one that bites. A footprint is drawn in pcbnew, where y
grows downward; a 3D model lives in a right-handed frame where y grows upward.
KiCad reconciles them by negating y as it places the model, so a model that
lines up with a pad at footprint (x, y) has to be drawn at (x, -y).

Every source file here already follows that convention — they are all vendor
models meant to be dropped onto a footprint — so none of them needs turning in
plane. The checks at the bottom of each build function are written against pad
positions with y negated, and are what would catch it if a replacement source
file ever arrived in the other orientation.

Run with no arguments:  python pcb/make_3d_models.py

Needs cadquery, which is not otherwise a dependency of this project:

    pip install "cadquery==2.5.2"

(Pin the version. cadquery 2.7 resolves to a cadquery-ocp build with no VTK
bindings and fails on import.)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import cadquery as cq
except ImportError:  # pragma: no cover - environment problem, not logic
    sys.exit(
        'error: cadquery is not installed.\n'
        '       pip install "cadquery==2.5.2"'
    )

PCB_DIR = Path(__file__).resolve().parent
OUT_DIR = PCB_DIR / "naub.3dshapes"

# The manufacturer STEP files. Outside the repo because they are third-party
# CAD, shared across board revisions; override if the parts bin moves.
SOURCE_DIR = Path(
    os.environ.get(
        "NAUB_COMPONENT_MODELS",
        r"C:\Users\ketch\Documents\Saves\naub\V2\components",
    )
)

SRC_CHOC = "Kailh Choc Low Profile Switch.step"
SRC_ENCODER = "SW-PEC11L-4215F-S0015.stp"
SRC_MODULE = "RP2350_Tiny_STEP.step"
SRC_DISPLAY = "EADOGM128_DPV.step"
SRC_BACKLIGHT = "EA LED55X46-A backlight.step"


class ModelError(RuntimeError):
    """A source model is missing or is not the shape we measured it to be."""


def load(name: str) -> cq.Shape:
    path = SOURCE_DIR / name
    if not path.exists():
        raise ModelError(
            f"{path} not found.\n"
            f"  Set NAUB_COMPONENT_MODELS to the directory holding the "
            f"manufacturer STEP files."
        )
    return cq.importers.importStep(str(path)).val()


def bbox(shape: cq.Shape) -> tuple[float, ...]:
    b = shape.BoundingBox()
    return (b.xmin, b.xmax, b.ymin, b.ymax, b.zmin, b.zmax)


def check(shape: cq.Shape, name: str, **expect: tuple[float, float]) -> None:
    """Fail loudly if a transformed model does not land where it should.

    The transforms below are hand-derived from measurements of each source
    file. If a source file is ever replaced with a different revision, the
    transform silently stops being correct — these assertions are what turn
    that into an error instead of a quietly wrong enclosure.
    """
    b = shape.BoundingBox()
    actual = {
        "x": (b.xmin, b.xmax), "y": (b.ymin, b.ymax), "z": (b.zmin, b.zmax),
    }
    for axis, (lo, hi) in expect.items():
        got_lo, got_hi = actual[axis]
        if abs(got_lo - lo) > 0.15 or abs(got_hi - hi) > 0.15:
            raise ModelError(
                f"{name}: expected {axis} in [{lo:.2f}, {hi:.2f}], "
                f"got [{got_lo:.2f}, {got_hi:.2f}]. "
                f"The source model is not the one this transform was written for."
            )


def section(shape: cq.Shape, z: float, size: float = 400.0) -> list[cq.Shape]:
    """The solid pieces of `shape` in a thin slab just below `z`."""
    slab = (
        cq.Workplane("XY")
        .workplane(offset=z - 0.02)
        .box(size, size, 0.04, centered=(True, True, False))
        .val()
    )
    return shape.intersect(slab).Solids()


# --------------------------------------------------------------------------
# SW5-SW9 — Kailh Choc V1
# --------------------------------------------------------------------------

def build_choc() -> cq.Shape:
    """Kailh Choc V1, turned to match the footprint and dropped onto z = 0.

    Two corrections to the manufacturer model:

    1. It is drawn with pin 1 at (0, -5.87) and pin 2 at (-5.0, -3.86). The
       footprint — kiswitch's, and ours, which copies it — has pin 1 at
       (0, -5.9) and pin 2 at (5, -3.8), which in model coordinates is
       (0, +5.9) and (5, +3.8). Half a turn about z lands both.

    2. Its z origin is the underside of the 15 x 15 flange, which is 2.20 mm
       above the board, not on it. Per the CPG135001D01 drawing the switch is
       5.80 mm from stem tip to flange underside and 8.00 mm from stem tip to
       the seating plane, so the model lifts by exactly 2.20 mm.
    """
    s = load(SRC_CHOC)
    check(s, "Choc source", x=(-7.5, 7.5), y=(-7.5, 7.5), z=(-5.197, 5.80))

    s = s.rotate((0, 0, 0), (0, 0, 1), 180)
    s = s.translate((0, 0, 2.20))

    check(s, "Choc", x=(-7.5, 7.5), y=(-7.5, 7.5), z=(-3.0, 8.0))

    # Pin 2 must now sit at (5.0, +3.8) — the footprint's (5.0, -3.8) pad.
    pins = [p for p in section(s, -1.0) if p.BoundingBox().xlen < 1.5]
    if not any(
        abs(p.Center().x - 5.0) < 0.3 and abs(p.Center().y - 3.8) < 0.3
        for p in pins
    ):
        raise ModelError("Choc: pin 2 did not land on the footprint pad at (5.0, -3.8)")
    return s


# --------------------------------------------------------------------------
# SW1-SW4 — Bourns PEC11L
# --------------------------------------------------------------------------

# Bourns PEC11L "How To Order" (REV. 04/26) decodes the two part numbers as:
#
#     PEC11L - 4 0 20 F - S 0020   the encoder on this board
#     PEC11L - 4 2 15 F - S 0015   the model in the parts bin
#                │ │ │  │   │ └── resolution, pulses per revolution
#                │ │ │  │   └──── S = push switch
#                │ │ │  └──────── F = flatted metal shaft
#                │ │ └─────────── shaft length L, mm      20 vs 15  <-- differs
#                │ └───────────── detent option
#                └─────────────── terminal configuration
#
# Only the shaft length is mechanical. Everything below the mounting face —
# body, terminals, mounting tabs, bushing — is common to the series, so the
# model is correct there and only the shaft has to be rebuilt.
ENC_MOUNTING_FACE = 4.6   # body front face; L is measured from here
ENC_SHAFT_DIA = 6.0       # 6.0 +0/-0.1
ENC_FLAT_ACROSS = 4.5     # across the D flat
ENC_L = 20.0              # -4020F
ENC_F = 10.0              # flat length for L = 20, from the datasheet table
ENC_TIP = ENC_MOUNTING_FACE + ENC_L          # 24.6 above the board
ENC_FLAT_START = ENC_TIP - ENC_F             # 14.6
ENC_MODEL_FLAT_START = 12.7                  # where the -4215F model's flat begins
ENC_MODEL_TIP = 19.6

# The two sheet-metal retention ears. The datasheet's recommended pattern puts
# their slots 12.8 mm apart — x = +/-6.4, which is what the footprint drills —
# and gives 13.2 mm across the ears, i.e. a 0.4 mm thick ear centred on 6.4
# with its outer face at 6.6, standing proud of the 11.8 mm body.
#
# The manufacturer model draws them flush with the body sides at +/-5.70
# instead, missing that outward bend. Left alone the ears cross the board 0.7
# mm inboard of their own slots — through solid FR4 — and the barb that is
# meant to spring out under the board hangs in mid air. So the ear below the
# body is moved out to where the slot is, and a bend is added to carry it
# there. The barb rides along and lands under the board, which is what it is
# for: it peaks 0.9 mm below the seating plane, sized for a 1.6 mm board.
ENC_TAB_X = 6.4              # footprint SH pad, and the datasheet's slot
ENC_MODEL_TAB_X = 5.70       # where the model puts the ear
ENC_TAB_THICKNESS = 0.4
ENC_TAB_WIDTH = 2.0          # along y; the slot is 2.1
ENC_BEND_TOP = 0.75          # ear is a free tongue below this; plate above
ENC_BEND_BOTTOM = 0.35


def bend_out_tabs(s: cq.Shape) -> cq.Shape:
    """Move the two retention ears out to the slots the board drills for them.

    Everything of the ear below the body is lifted out as a lump, shifted
    outward, and put back, so the barb keeps the shape the manufacturer drew.
    A short bend is added between the body's side plate and the relocated ear
    to carry it out — which is the feature the model was missing.
    """
    shift = ENC_TAB_X - ENC_MODEL_TAB_X

    for sign in (1, -1):
        # The ear is the only thing this far out and this narrow in y; the
        # side plate it hangs off is 8.9 mm long in y, so |y| < 1.5 picks out
        # the tongue and leaves the plate alone.
        region = (
            cq.Workplane("XY")
            .workplane(offset=-6.0)
            .center(sign * 6.6, 0)
            .box(4.4, 3.0, 6.0 + ENC_BEND_TOP, centered=(True, True, False))
            .val()
        )
        ear = s.intersect(region)
        if not ear.Solids():
            raise ModelError(
                f"PEC11L: no retention ear found at x = {sign * ENC_MODEL_TAB_X}"
            )
        s = s.cut(region).fuse(ear.translate((sign * shift, 0, 0)))

        bend = (
            cq.Workplane("XY")
            .workplane(offset=ENC_BEND_BOTTOM)
            .center(sign * (ENC_MODEL_TAB_X + ENC_TAB_X) / 2, 0)
            .box(
                (ENC_TAB_X - ENC_MODEL_TAB_X) + ENC_TAB_THICKNESS,
                ENC_TAB_WIDTH,
                ENC_BEND_TOP - ENC_BEND_BOTTOM,
                centered=(True, True, False),
            )
            .val()
        )
        s = s.fuse(bend)

    return s.clean()


def build_encoder() -> cq.Shape:
    """PEC11L-4020F-S0020: the -4215F model with a 20 mm shaft grafted on.

    The source is a 15 mm shaft. Lengthening it is not just a stretch — the
    flat has to start 10.0 mm from the tip rather than 7.0 mm, so the round
    section grows and the flat moves. The tip's chamfer is lifted off the
    source model rather than re-cut, so the shaft end stays the vendor's.

    Nothing turns in plane: the source already has its three encoder terminals
    at y = -7.55, which is the footprint's A/C/B row at y = +7.5. The D flat
    faces -y with them, as the datasheet's side view shows.
    """
    s = load(SRC_ENCODER)
    check(s, "PEC11L source", x=(-6.92, 6.92), y=(-7.60, 7.10), z=(-4.41, 19.60))

    # Everything up to where the source flat begins is common to the series.
    keep = s.cut(
        cq.Workplane("XY")
        .workplane(offset=ENC_MODEL_FLAT_START)
        .box(60, 60, 60, centered=(True, True, False))
        .val()
    )

    # The vendor's chamfered shaft end, moved up by the extra 5 mm of shaft.
    tip_cut = 0.6
    tip = s.intersect(
        cq.Workplane("XY")
        .workplane(offset=ENC_MODEL_TIP - tip_cut)
        .box(60, 60, 10, centered=(True, True, False))
        .val()
    ).translate((0, 0, ENC_TIP - ENC_MODEL_TIP))

    # Round shaft from the source flat's old start up to where the flat now
    # begins, then the flatted section up to the reused tip.
    round_shaft = (
        cq.Workplane("XY")
        .workplane(offset=ENC_MODEL_FLAT_START)
        .circle(ENC_SHAFT_DIA / 2)
        .extrude(ENC_FLAT_START - ENC_MODEL_FLAT_START)
        .val()
    )
    flat_len = (ENC_TIP - tip_cut) - ENC_FLAT_START
    flat_shaft = (
        cq.Workplane("XY")
        .workplane(offset=ENC_FLAT_START)
        .circle(ENC_SHAFT_DIA / 2)
        .extrude(flat_len)
        .val()
    )
    # The flat faces -y, matching the source's own shaft section there
    # (x[-3.00, 3.00] y[-1.50, 3.00]) and so the three encoder terminals.
    off = ENC_SHAFT_DIA / 2 - ENC_FLAT_ACROSS
    flat_shaft = flat_shaft.cut(
        cq.Workplane("XY")
        .workplane(offset=ENC_FLAT_START)
        .center(0, -ENC_SHAFT_DIA / 2 + off / 2)
        .box(ENC_SHAFT_DIA + 2, abs(off), flat_len + 2, centered=(True, True, False))
        .val()
    )

    s = keep.fuse(round_shaft).fuse(flat_shaft).fuse(tip).clean()
    s = bend_out_tabs(s)

    check(s, "PEC11L", y=(-7.60, 7.10), z=(-4.41, ENC_TIP))

    # Three terminals at -y, which is the footprint's A/C/B row at y = +7.5.
    row = [p for p in section(s, -2.0) if p.Center().y < -5.0]
    if len(row) != 3:
        raise ModelError(
            f"PEC11L: expected 3 terminals at -y, found {len(row)}"
        )

    # Everything that goes through the board has to be inside the hole drilled
    # for it. Checked halfway down a 1.6 mm board, where a lead sitting in the
    # wrong place is unambiguous.
    holes = {                      # footprint pad -> (x, y, half-width in x)
        "A": (-2.5, -7.5, 0.55), "C": (0.0, -7.5, 0.55), "B": (2.5, -7.5, 0.55),
        "S1": (-2.5, 7.0, 0.55), "S2": (2.5, 7.0, 0.55),
        "SH-": (-ENC_TAB_X, 0.0, 0.75), "SH+": (ENC_TAB_X, 0.0, 0.75),
    }
    for piece in section(s, -0.8):
        b = piece.BoundingBox()
        cx, cy = (b.xmin + b.xmax) / 2, (b.ymin + b.ymax) / 2
        name, (hx, hy, half) = min(
            holes.items(), key=lambda kv: (kv[1][0] - cx) ** 2 + (kv[1][1] - cy) ** 2
        )
        if abs(cx - hx) > half or abs(cy - hy) > 1.1:
            raise ModelError(
                f"PEC11L: a lead crosses the board at ({cx:.2f}, {cy:.2f}), "
                f"outside the {name} hole at ({hx:.2f}, {hy:.2f})"
            )
    return s


# --------------------------------------------------------------------------
# U1 — Waveshare RP2350-Tiny
# --------------------------------------------------------------------------

def build_module() -> cq.Shape:
    """Waveshare RP2350-Tiny, centred on the footprint origin, FPC edge at -y.

    Waveshare draws the module in the first quadrant with its PCB top face at
    z = 0, so the whole part hangs below the origin. The footprint is centred
    and the module sits on top of the carrier, so it moves by half its size in
    x and y and by its own PCB thickness in z. Nothing turns in plane.

    The castellations confirm the orientation: the source has its row of five
    on the y = 0 edge, which after centring is y = -10.16 — the footprint's
    GP9..GP13 row at y = +10.16. The edge with no castellations, the FPC/USB
    one, ends up at the footprint's -y, where the silkscreen marks it.
    """
    s = load(SRC_MODULE)
    check(s, "RP2350-Tiny source", x=(0, 18.0), y=(0, 23.5), z=(-0.80, 1.15))

    s = s.translate((-9.0, -11.75, 0.80))

    check(s, "RP2350-Tiny", x=(-9.0, 9.0), y=(-11.75, 11.75), z=(0.0, 1.95))
    return s


# --------------------------------------------------------------------------
# U2 — EA DOGM128-6 with its EA LED55X46 backlight
# --------------------------------------------------------------------------

# The board drives the backlight (U2 pins 1/2/3 are BL_A1..A3 and pin 18 is
# BL_CATHODE, through R_BL1..3 and Q1), so the display does not sit on the
# board — it sits on the backlight module. The DOGM128 datasheet puts it
# exactly: "2.0 mm without backlight module, and only 5.8 mm with a b./l.
# module mounted". The parts-bin backlight measures 3.60 mm thick and the
# display 2.14 mm, for 5.74 mm — the datasheet's 5.8 mm.
#
# Leaving the backlight out would model the display 3.6 mm too low, which is
# the one number an enclosure most needs to get right, so it is built in.
BACKLIGHT_THICKNESS = 3.60


def build_display() -> cq.Shape:
    """The display stack: backlight on the board, display on the backlight."""
    disp = load(SRC_DISPLAY)
    check(disp, "DOGM128 source", x=(-0.13, 43.56), y=(-51.63, 3.37), z=(-2.54, 2.14))

    # Pin rows at y = 0, -2.54 ... -48.26, which is the footprint's pads at
    # y = 0, +2.54 ... +48.26. Already right; nothing turns in plane.
    pin_boxes = [p.BoundingBox() for p in section(disp, -2.0)]
    if len(pin_boxes) < 20:
        raise ModelError(f"DOGM128: found only {len(pin_boxes)} pins to extend")

    disp = disp.translate((0, 0, BACKLIGHT_THICKNESS))

    # Lifting the display lifts its pin tails clear of the board, so redraw
    # them from the raised module down through where the carrier will be.
    pins = []
    for b in pin_boxes:
        pins.append(
            cq.Workplane("XY")
            .workplane(offset=-3.0)
            .center((b.xmin + b.xmax) / 2, (b.ymin + b.ymax) / 2)
            .box(b.xlen, b.ylen, 3.0 + BACKLIGHT_THICKNESS,
                 centered=(True, True, False))
            .val()
        )

    # The backlight panel is drawn standing up in the xz plane, 55 x 46 with
    # y as its thickness. Lay it down, turn it to match the display's 46 x 55,
    # and centre it under the module.
    bl = load(SRC_BACKLIGHT)
    check(bl, "backlight source", x=(-27.5, 27.5), y=(0, 3.60), z=(-23.0, 23.0))
    bl = bl.rotate((0, 0, 0), (1, 0, 0), 90).rotate((0, 0, 0), (0, 0, 1), 90)
    bl = bl.translate((21.72, -24.13, 0))

    s = disp.fuse(bl)
    for p in pins:
        s = s.fuse(p)
    s = s.clean()

    # The 46 x 55 backlight is the widest thing in the stack, so it sets the
    # bounds: centred at (21.72, -24.13).
    check(s, "DOGM128 stack", x=(-1.28, 44.72), y=(-51.63, 3.37),
          z=(-3.0, BACKLIGHT_THICKNESS + 2.14))
    return s


# --------------------------------------------------------------------------

BUILDS = (
    ("SW_Kailh_Choc_V1.step", build_choc, "SW5-SW9  Kailh Choc V1"),
    ("RotaryEncoder_Bourns_PEC11L-4020F-S0020.step", build_encoder,
     "SW1-SW4  Bourns PEC11L, 20 mm shaft"),
    ("Waveshare_RP2350-Tiny.step", build_module, "U1       Waveshare RP2350-Tiny"),
    ("EA_DOGM128-6_with_LED55X46.step", build_display,
     "U2       EA DOGM128-6 + backlight"),
)


def main() -> int:
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        rows = []
        for filename, build, label in BUILDS:
            print(f"building {label} ...")
            shape = build()
            out = OUT_DIR / filename
            cq.exporters.export(cq.Workplane(obj=shape), str(out),
                                exportType="STEP")
            b = shape.BoundingBox()
            rows.append((filename, b, out.stat().st_size))
    except ModelError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"{'model':46s} {'size (mm)':>22s}  {'above board':>11s}  {'file'}")
    for filename, b, size in rows:
        print(
            f"  {filename:44s} "
            f"{b.xlen:6.2f} x {b.ylen:6.2f} x {b.zlen:5.2f}  "
            f"{b.zmax:9.2f}    {size/1024:8.0f} kB"
        )
    print()
    print(f"written to {OUT_DIR.relative_to(PCB_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
