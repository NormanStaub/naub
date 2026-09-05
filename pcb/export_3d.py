#!/usr/bin/env python3
"""Export the populated naub V2 board as 3D solids, for enclosure CAD.

Produces, under pcb/export/3d/:

    naub_board.step   the board and every component, as B-rep solids
    naub_board.stl    the same thing as a mesh, for viewers and 3D printing
    naub_board.glb    the same thing again, for a quick look in a browser
    preview_*.png     top, bottom and angled renders, to eyeball the result
    NOTES.md          the origin, the part heights, and what is not in here

This is a companion to export_fab.py, not a replacement: that script produces
what JLCPCB needs to build the board, this one produces what you need to draw
a case around it. Neither touches the other's output.

Every component that has a 3D model is placed, including the four parts KiCad
has no model for — the encoders, the key switches, the RP2350-Tiny and the
display — whose models are built by make_3d_models.py. Run that first; this
script will tell you if you have not.

Copper, silkscreen and soldermask are left out by default. They are flat faces
that add a great deal of geometry and nothing an enclosure cares about. Pass
--copper if you want them for a presentation render.

Run with no arguments:  python pcb/export_3d.py
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

PCB_DIR = Path(__file__).resolve().parent
BOARD = PCB_DIR / "naub_kicad.kicad_pcb"
MODEL_DIR = PCB_DIR / "naub.3dshapes"
EXPORT_DIR = PCB_DIR / "export" / "3d"

STEP_PATH = EXPORT_DIR / "naub_board.step"
STL_PATH = EXPORT_DIR / "naub_board.stl"
GLB_PATH = EXPORT_DIR / "naub_board.glb"
NOTES_PATH = EXPORT_DIR / "NOTES.md"

PREVIEWS = (
    ("preview_top.png", ("--side", "top")),
    ("preview_bottom.png", ("--side", "bottom")),
    ("preview_angled.png", ("--side", "top", "--perspective",
                            "--rotate", "-40,0,20")),
)

# Built by make_3d_models.py. Keep in step with the (model ...) entries in the
# board — a missing file exports as a silent hole in the assembly, which is
# exactly the sort of thing you discover after printing a case.
REQUIRED_MODELS = (
    "SW_Kailh_Choc_V1.step",
    "RotaryEncoder_Bourns_PEC11L-4020F-S0020.step",
    "Waveshare_RP2350-Tiny.step",
    "EA_DOGM128-6_with_LED55X46.step",
)

# What sits how high above the board, for the notes. Measured off the models.
PART_HEIGHTS = (
    ("SW1-SW4", "Bourns PEC11L-4020F-S0020 encoder", "front", 24.60,
     "shaft tip; bushing top at 11.4, body top at 4.6"),
    ("SW5-SW9", "Kailh Choc V1 key switch", "front", 8.00,
     "stem tip, unpressed; housing top at 5.35, 15 x 15 flange"),
    ("U2", "EA DOGM128-6 + EA LED55X46 backlight", "front", 5.74,
     "backlight 0 to 3.6, display 3.6 to 5.74"),
    ("U1", "Waveshare RP2350-Tiny", "back", 1.95,
     "module PCB 0.8 plus components"),
)


class ExportError(RuntimeError):
    """Something the user has to fix before the export means anything."""


def find_kicad_cli() -> str:
    """Locate kicad-cli, preferring an explicit override."""
    override = os.environ.get("KICAD_CLI")
    if override:
        if not Path(override).exists():
            raise ExportError(f"KICAD_CLI is set to {override!r}, which does not exist")
        return override

    on_path = shutil.which("kicad-cli")
    if on_path:
        return on_path

    candidates = sorted(
        Path(r"C:\Program Files\KiCad").glob("*/bin/kicad-cli.exe"),
        key=lambda p: p.parts,
        reverse=True,
    )
    if candidates:
        return str(candidates[0])

    raise ExportError(
        "kicad-cli not found. Install KiCad, or point KICAD_CLI at the binary."
    )


def run_cli(cli: str, *args: str) -> str:
    proc = subprocess.run(
        [cli, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise ExportError(
            f"kicad-cli {' '.join(args[:3])} failed ({proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout


def check_models() -> None:
    missing = [m for m in REQUIRED_MODELS if not (MODEL_DIR / m).exists()]
    if missing:
        raise ExportError(
            "these 3D models have not been built yet:\n"
            + "\n".join(f"  {MODEL_DIR.name}/{m}" for m in missing)
            + "\n\nBuild them first:  python pcb/make_3d_models.py"
        )


def check_board_not_open() -> None:
    lock = BOARD.with_name("~" + BOARD.name + ".lck")
    if lock.exists():
        print(
            f"  ! {BOARD.name} is open in KiCad. Exporting the last SAVED state;\n"
            f"    save the board first if you have pending edits.",
            file=sys.stderr,
        )


def board_outline() -> tuple[float, float, float, float]:
    """(min_x, min_y, max_x, max_y) of Edge.Cuts, in board page coordinates."""
    text = BOARD.read_text(encoding="utf-8")
    points: list[tuple[float, float]] = []
    for match in re.finditer(r"\(gr_(?:rect|line|arc|circle|poly)\b(.*?)\n\t\)", text, re.S):
        body = match.group(1)
        if '"Edge.Cuts"' not in body:
            continue
        for pt in re.finditer(r"\((?:start|end|center|mid|xy) ([-\d.]+) ([-\d.]+)\)", body):
            points.append((float(pt.group(1)), float(pt.group(2))))

    if not points:
        raise ExportError("no Edge.Cuts geometry found — cannot place the origin")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def origin_arg() -> tuple[str, float, float]:
    """Put the model's origin on the board's lower-left corner.

    Left alone, kicad-cli emits the board wherever it happens to sit on the
    KiCad page — around (125, 44) here — which lands the part a long way from
    the origin in whatever CAD tool opens it. Anchoring to a board corner
    means the exported model runs 0..56 by 0..100, which is what you want to
    build a case against.

    kicad-cli mirrors y on the way out (KiCad's page y grows downward, a CAD
    model's grows upward), so the corner to name here is the one with the
    largest y — the bottom-left corner as drawn in KiCad.
    """
    min_x, _min_y, _max_x, max_y = board_outline()
    return (f"{min_x}x{max_y}mm", min_x, max_y)


def export_common(include_copper: bool) -> list[str]:
    args = ["--force", "--subst-models", "--user-origin", origin_arg()[0]]
    if include_copper:
        args += [
            "--include-tracks", "--include-pads", "--include-zones",
            "--include-silkscreen", "--include-soldermask",
        ]
    return args


def stl_bounds(path: Path) -> tuple[tuple[float, float], ...] | None:
    """Bounding box of the exported STL, as a check on what we just wrote.

    Reading the mesh back is the only verification here that does not need a
    CAD kernel — and it catches the failure that matters, a component whose
    model did not load, by showing the assembly is shorter than it should be.

    kicad-cli writes ASCII STL, but the format allows either, so handle both.
    """
    data = path.read_bytes()
    if len(data) < 84:
        return None

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3

    def note(x: float, y: float, z: float) -> None:
        for axis, value in enumerate((x, y, z)):
            lo[axis] = min(lo[axis], value)
            hi[axis] = max(hi[axis], value)

    if data[:5] == b"solid" and b"facet normal" in data[:512]:
        for match in re.finditer(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", data):
            note(*(float(g) for g in match.groups()))
    else:
        (count,) = struct.unpack("<I", data[80:84])
        if len(data) < 84 + count * 50:
            return None
        for i in range(count):
            base = 84 + i * 50 + 12  # skip the facet normal
            for v in range(3):
                note(*struct.unpack_from("<3f", data, base + v * 12))

    if lo[0] == float("inf"):
        return None
    return tuple((lo[i], hi[i]) for i in range(3))


def mounting_planes(cli: str, origin_str: str) -> tuple[float, float] | None:
    """(back, front) z of the surfaces components actually sit on.

    Not the same as the board body, and not worth guessing at. KiCad's board
    solid is the dielectric; it seats component models on the copper, which is
    a hair outside that — so heights quoted against the body come out slightly
    wrong. Measuring instead means the numbers in the notes agree with the
    model a reader is holding.

    Done by exporting one part from each side on its own and subtracting the
    height we already know it to be. The board body comes along — kicad-cli
    refuses to export a component with `--no-board-body` — but it does not
    matter: each probe part is the extreme in the direction being measured,
    so the board never sets the bound.
    """
    probes = {
        "front": next((r for r in PART_HEIGHTS if r[2] == "front"), None),
        "back": next((r for r in PART_HEIGHTS if r[2] == "back"), None),
    }
    if not all(probes.values()):
        return None

    planes: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for side, row in probes.items():
            ref = row[0].split("-")[0]  # "SW1-SW4" -> "SW1"
            height = row[3]
            out = Path(tmp) / f"{ref}.stl"
            run_cli(cli, "pcb", "export", "stl", "--output", str(out), "--force",
                    "--subst-models", "--user-origin", origin_str,
                    "--component-filter", ref, str(BOARD))
            bounds = stl_bounds(out)
            if bounds is None:
                return None
            z_lo, z_hi = bounds[2]
            planes[side] = z_hi - height if side == "front" else z_lo + height

    return (planes["back"], planes["front"])


def check_placement(bounds: tuple[tuple[float, float], ...] | None) -> None:
    """Complain if the assembly is wider than the board it sits on.

    Nothing on this board overhangs an edge, so the finished model should be
    exactly as wide and as long as the outline. Anything more means a
    component model landed in the wrong place — which is easy to do, because
    KiCad negates a model's y as it places it, so a model drawn in the
    footprint's own y ends up reflected across the board. That failure looks
    perfectly reasonable in a parts list and obvious here.
    """
    if bounds is None:
        print("  ! could not read the mesh back; placement not checked",
              file=sys.stderr)
        return

    min_x, min_y, max_x, max_y = board_outline()
    width, length = max_x - min_x, max_y - min_y
    (x0, x1), (y0, y1), _ = bounds

    problems = []
    if x1 - x0 > width + 0.5 or x0 < -0.5:
        problems.append(f"    x spans {x0:.2f}..{x1:.2f}, board is 0..{width:.2f}")
    if y1 - y0 > length + 0.5 or y0 < -0.5:
        problems.append(f"    y spans {y0:.2f}..{y1:.2f}, board is 0..{length:.2f}")

    if problems:
        print(
            "  ! the assembly is bigger than the board outline:\n"
            + "\n".join(problems)
            + "\n    A component model is very likely misplaced.",
            file=sys.stderr,
        )


def write_notes(
    bounds: tuple[tuple[float, float], ...] | None,
    origin: tuple[float, float],
    include_copper: bool,
    sizes: dict[str, int],
    board_z: tuple[float, float] | None,
) -> None:
    min_x, max_y = origin
    back_z, front_z = board_z if board_z else (0.0, 1.6)
    lines: list[str] = []
    add = lines.append

    add("# naub V2 — 3D model of the populated board")
    add("")
    add("Generated by `pcb/export_3d.py`. Do not edit by hand; re-run instead.")
    add("")
    add("| File | Size | Use |")
    add("| --- | --- | --- |")
    add(f"| `{STEP_PATH.name}` | {sizes.get('step', 0)/1e6:.1f} MB | "
        "solids — import this into CAD |")
    add(f"| `{STL_PATH.name}` | {sizes.get('stl', 0)/1e6:.1f} MB | "
        "mesh — viewers, printing, clearance checks |")
    add(f"| `{GLB_PATH.name}` | {sizes.get('glb', 0)/1e6:.1f} MB | "
        "quick look in a browser |")
    for name, _ in PREVIEWS:
        add(f"| `{name}` | — | render, to check the result by eye |")
    add("")

    add("## Origin and orientation")
    add("")
    add(f"The origin sits on the board's lower-left corner as drawn in KiCad "
        f"(page coordinates {min_x:.2f}, {max_y:.2f} mm).")
    add("")
    add("- **+x** runs along the short edge, **+y** along the long edge")
    add(f"- **z = 0 is the back face of the bare PCB.** With copper, the board "
        f"runs z = {back_z:.2f} to {front_z:.2f} mm; parts sit on those "
        f"copper faces, so a front-side part starts at z = {front_z:.2f}")
    add("- **+z** is out of the front — encoders, key switches and the "
        "display are all above the board")
    add("- the RP2350-Tiny is the only part on the back, below z = 0")
    add("")
    if bounds:
        (x0, x1), (y0, y1), (z0, z1) = bounds
        add("Measured from the exported mesh:")
        add("")
        add("| Axis | From | To | Extent |")
        add("| --- | --- | --- | --- |")
        add(f"| x | {x0:.2f} | {x1:.2f} | {x1-x0:.2f} mm |")
        add(f"| y | {y0:.2f} | {y1:.2f} | {y1-y0:.2f} mm |")
        add(f"| z | {z0:.2f} | {z1:.2f} | {z1-z0:.2f} mm |")
        add("")

    add("## What is how tall")
    add("")
    add("Heights are above the board face the part is mounted on, so add "
        f"{front_z:.2f} mm to place a front-side part in model coordinates.")
    add("")
    add("| Ref | Part | Side | Height | Reaches z | Notes |")
    add("| --- | --- | --- | --- | --- | --- |")
    for ref, part, side, height, note in PART_HEIGHTS:
        reach = front_z + height if side == "front" else back_z - height
        add(f"| {ref} | {part} | {side} | {height:.2f} mm | "
            f"{reach:.2f} mm | {note} |")
    add("")

    add("## What is not in here")
    add("")
    add("- **Keycaps and knobs.** Neither is a footprint on the board, so "
        "neither can come out of a board export. Choc keycap and knob models "
        "live in the CAD parts bin; place them against the switch stems and "
        "encoder shafts.")
    add("- **Test points and mounting holes.** `TP_*` are bare pads and the "
        "four M2 holes are holes; both are in the board body, neither has a "
        "part to show.")
    if not include_copper:
        add("- **Copper, silkscreen and soldermask.** Flat faces that add a "
            "lot of geometry and nothing an enclosure needs. Re-run with "
            "`--copper` if you want them for a render.")
    add("")

    add("## Worth knowing before you draw a case")
    add("")
    add("- **SW7, SW8 and SW9 overhang the board's bottom edge by 0.22 mm.** "
        "The switches are 15 mm square and sit 7.28 mm from the edge, so the "
        "switch bodies — not the PCB — set the outline along that edge. "
        "Harmless electrically (the switches are held by their pins and "
        "locating bosses) but the case has to clear 15 mm there, not the "
        "board.")
    add("- **The tallest thing is an encoder shaft at 24.60 mm**, with the "
        "bushing top at 11.4 mm. That is where a panel and knob have to land.")
    add("- **The back is not flat.** J_LCD's header pins stand 8.6 mm proud "
        "of the back face, well past the RP2350-Tiny's 1.95 mm.")
    add("")

    add("## Accuracy")
    add("")
    add("- The **display stack includes its backlight**, because the board "
        "drives one (U2 pins 1/2/3 and 18, through R_BL1..3 and Q1). The "
        "display alone would sit 3.6 mm lower than this model shows. Total "
        "5.74 mm matches the DOGM128 datasheet's 5.8 mm figure for a module "
        "with a backlight fitted.")
    add("- The **encoder shaft is 20 mm**, per PEC11L-**4020**F-S0020. The "
        "manufacturer model in the parts bin is the 15 mm -4215F; "
        "`make_3d_models.py` rebuilds the shaft to 20 mm with the flat "
        "starting 10 mm from the tip, as the datasheet table gives for L=20.")
    add("- The **encoder's two retention ears were moved out to their slots.** "
        "The manufacturer model draws them flush with the body sides at "
        "x = ±5.70, but the datasheet's pattern — which this board is drilled "
        "to — puts the slots 12.8 mm apart at ±6.40, with 13.2 mm across the "
        "ears. As drawn they crossed the board through solid FR4 0.7 mm "
        "inboard of their own slots. All seven encoder leads are now checked "
        "against the holes drilled for them when the model is built.")
    add("- The **display, encoders, key switches and RP2350-Tiny use "
        "manufacturer STEP models**, not KiCad library approximations. Their "
        "placement is checked against pad positions when they are built, and "
        "the finished assembly is checked against the board outline here.")
    add("- Passives, the SOT-23 and the pin headers use KiCad's own library "
        "models and are nominal package outlines, not manufacturer geometry.")
    add("- The four M2 mounting holes and the board outline come straight "
        "from the board, so they are exact.")
    add("")

    NOTES_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--copper", action="store_true",
        help="also export tracks, pads, zones, silkscreen and soldermask",
    )
    opts = parser.parse_args()

    if not BOARD.exists():
        print(f"error: board not found at {BOARD}", file=sys.stderr)
        return 1

    try:
        cli = find_kicad_cli()
        print(f"kicad-cli: {cli}")
        check_models()
        check_board_not_open()

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        common = export_common(opts.copper)
        origin_str, min_x, max_y = origin_arg()
        print(f"origin: board corner at page ({min_x}, {max_y}) mm")

        print("exporting STEP ...")
        run_cli(cli, "pcb", "export", "step", "--output", str(STEP_PATH),
                *common, str(BOARD))

        print("exporting STL ...")
        run_cli(cli, "pcb", "export", "stl", "--output", str(STL_PATH),
                *common, str(BOARD))

        print("exporting GLB ...")
        run_cli(cli, "pcb", "export", "glb", "--output", str(GLB_PATH),
                *common, str(BOARD))

        # Cheap (a fraction of a second each) and the fastest way to catch a
        # part that is present but wrong — a mesh bounding box will not tell
        # you a switch is upside down, and a picture will.
        print("rendering previews ...")
        for name, args in PREVIEWS:
            run_cli(cli, "pcb", "render", "--output", str(EXPORT_DIR / name),
                    "--width", "1100", "--height", "1500", "--quality", "basic",
                    "--background", "opaque", *args, str(BOARD))

        print("measuring ...")
        bounds = stl_bounds(STL_PATH)
        check_placement(bounds)

        board_z = mounting_planes(cli, origin_str)
        sizes = {
            "step": STEP_PATH.stat().st_size,
            "stl": STL_PATH.stat().st_size,
            "glb": GLB_PATH.stat().st_size,
        }
        write_notes(bounds, (min_x, max_y), opts.copper, sizes, board_z)

    except ExportError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    print()
    for path in (STEP_PATH, STL_PATH, GLB_PATH, NOTES_PATH):
        print(f"  {path.relative_to(PCB_DIR.parent)}  "
              f"({path.stat().st_size/1e6:.1f} MB)")
    if bounds:
        (x0, x1), (y0, y1), (z0, z1) = bounds
        print()
        print(f"  extent  x {x0:7.2f} .. {x1:7.2f}   ({x1-x0:6.2f} mm)")
        print(f"          y {y0:7.2f} .. {y1:7.2f}   ({y1-y0:6.2f} mm)")
        print(f"          z {z0:7.2f} .. {z1:7.2f}   ({z1-z0:6.2f} mm)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
