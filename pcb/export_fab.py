#!/usr/bin/env python3
"""Build the complete JLCPCB order package for the naub V2 board.

Produces, under pcb/export/:

    naub_gerbers_jlcpcb.zip   gerbers + Excellon drill, ready to upload
    naub_bom_jlcpcb.csv       BOM for the parts JLC assembles
    naub_cpl_jlcpcb.csv       pick-and-place positions for those same parts
    ORDER.md                  the settings + checks for placing the order
    gerbers/                  the loose plot files, for viewer inspection

Which parts JLC assembles is decided entirely by pcb/fab/assembly.csv. The
encoders, key switches, display and RP2350-Tiny module are marked no there —
they are hand-soldered.

Run with no arguments:  python pcb/export_fab.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PCB_DIR = Path(__file__).resolve().parent
BOARD = PCB_DIR / "naub_kicad.kicad_pcb"
ASSEMBLY_MAP = PCB_DIR / "fab" / "assembly.csv"
EXPORT_DIR = PCB_DIR / "export"
GERBER_DIR = EXPORT_DIR / "gerbers"
ZIP_PATH = EXPORT_DIR / "naub_gerbers_jlcpcb.zip"
BOM_PATH = EXPORT_DIR / "naub_bom_jlcpcb.csv"
CPL_PATH = EXPORT_DIR / "naub_cpl_jlcpcb.csv"
ORDER_PATH = EXPORT_DIR / "ORDER.md"

# A 2-layer board: copper, mask, silk, paste and the outline. Nothing else is
# fabrication data, so nothing else goes in the zip.
GERBER_LAYERS = (
    "F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts"
)

# The drill map is a human-readable summary, not fab data. JLC's parser has
# been known to mistake it for a copper layer, so it is plotted for our own
# reference but kept out of the upload.
ZIP_EXCLUDE_SUFFIX = "-drl_map.gbr"


class ExportError(RuntimeError):
    """A problem the user has to fix before ordering."""


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

    # Default Windows install: C:\Program Files\KiCad\<version>\bin\kicad-cli.exe.
    # Sort so the newest installed version wins.
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
    """Run kicad-cli, raising with its own output if it fails."""
    proc = subprocess.run(
        [cli, *args], capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        raise ExportError(
            f"kicad-cli {' '.join(args[:3])} failed ({proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    return proc.stdout


def check_board_not_open() -> None:
    """Warn if KiCad holds the board — the export would use the last save."""
    lock = BOARD.with_name("~" + BOARD.name + ".lck")
    if lock.exists():
        print(
            f"  ! {BOARD.name} is open in KiCad. Exporting the last SAVED state;\n"
            f"    save the board first if you have pending edits.",
            file=sys.stderr,
        )


def plot_gerbers(cli: str) -> None:
    """Plot the fab layers and the Excellon drill file.

    Both use absolute (page) coordinates so the drill, the copper and the
    placement file all share one origin.
    """
    if GERBER_DIR.exists():
        shutil.rmtree(GERBER_DIR)
    GERBER_DIR.mkdir(parents=True)

    run_cli(
        cli, "pcb", "export", "gerbers",
        "--output", str(GERBER_DIR) + os.sep,
        "--layers", GERBER_LAYERS,
        "--no-protel-ext",
        "--subtract-soldermask",
        str(BOARD),
    )
    # Separate PTH and NPTH files rather than one merged drill file. A merged
    # file records plating only in `; #@!` comments, and a fab that ignores
    # those plates every hole — which would plate the M2 mounting holes, the
    # Kailh Choc alignment posts and the encoder bushing holes. Two files make
    # the distinction structural instead of advisory.
    run_cli(
        cli, "pcb", "export", "drill",
        "--output", str(GERBER_DIR) + os.sep,
        "--format", "excellon",
        "--drill-origin", "absolute",
        "--excellon-units", "mm",
        "--excellon-separate-th",
        "--generate-map", "--map-format", "gerberx2",
        str(BOARD),
    )


def read_positions(cli: str) -> list[dict[str, str]]:
    """Return every placeable footprint, as KiCad reports it."""
    with tempfile.TemporaryDirectory() as tmp:
        pos_csv = Path(tmp) / "pos.csv"
        run_cli(
            cli, "pcb", "export", "pos",
            "--output", str(pos_csv),
            "--format", "csv",
            "--units", "mm",
            "--side", "both",
            str(BOARD),
        )
        with pos_csv.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))


def read_assembly_map() -> dict[str, dict[str, str]]:
    """Parse fab/assembly.csv, ignoring the leading comment block."""
    text = ASSEMBLY_MAP.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    rows = list(csv.DictReader(io.StringIO(body)))

    mapping: dict[str, dict[str, str]] = {}
    for row in rows:
        ref = (row.get("Designator") or "").strip()
        if not ref:
            continue
        if ref in mapping:
            raise ExportError(f"{ASSEMBLY_MAP.name}: duplicate designator {ref}")
        mapping[ref] = {k: (v or "").strip() for k, v in row.items()}
    return mapping


def reconcile(
    positions: list[dict[str, str]], mapping: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    """Cross-check board against map; return the rows JLC will assemble.

    Three ways this fails, all of them things that would otherwise become a
    wrong or incomplete order:
      - a footprint on the board with no row in the map
      - a row in the map that no longer matches any footprint
      - a value that changed in CAD but not in the map
    """
    board_refs = {row["Ref"] for row in positions}
    map_refs = set(mapping)

    problems: list[str] = []
    for ref in sorted(board_refs - map_refs):
        problems.append(f"  on the board but missing from the map: {ref}")
    for ref in sorted(map_refs - board_refs):
        problems.append(f"  in the map but no longer on the board: {ref}")

    for row in positions:
        entry = mapping.get(row["Ref"])
        if entry is None:
            continue
        expected = entry["BoardValue"]
        actual = row["Val"]
        if expected != actual:
            problems.append(
                f"  {row['Ref']}: board says {actual!r}, map says {expected!r}"
            )

    if problems:
        raise ExportError(
            f"{ASSEMBLY_MAP.name} is out of sync with {BOARD.name}:\n"
            + "\n".join(problems)
            + "\n\nUpdate the map, then re-run. Nothing was exported."
        )

    assembled = []
    for row in positions:
        entry = mapping[row["Ref"]]
        if entry["Assemble"].lower() != "yes":
            continue
        if not entry["LCSC"]:
            raise ExportError(
                f"{row['Ref']} is marked for assembly but has no LCSC part number"
            )
        assembled.append({**row, **entry})
    return assembled


def write_bom(assembled: list[dict[str, str]]) -> int:
    """Write the JLC BOM, one line per distinct part."""
    groups: dict[tuple[str, str, str], list[str]] = {}
    for row in assembled:
        key = (row["Comment"], row["Package"], row["LCSC"])
        groups.setdefault(key, []).append(row["Ref"])

    with BOM_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Comment", "Designator", "Footprint", "LCSC Part #"])
        for (comment, package, lcsc), refs in sorted(groups.items()):
            writer.writerow([comment, ",".join(sorted(refs)), package, lcsc])
    return len(groups)


def write_cpl(assembled: list[dict[str, str]]) -> None:
    """Write the JLC placement file, in the gerbers' coordinate frame."""
    with CPL_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for row in sorted(assembled, key=lambda r: r["Ref"]):
            writer.writerow([
                row["Ref"],
                f"{float(row['PosX']):.4f}",
                f"{float(row['PosY']):.4f}",
                row["Side"],
                f"{float(row['Rot']):.2f}",
            ])


def build_zip() -> list[str]:
    """Zip the fab files. Returns the archive's member names."""
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    members = sorted(
        p for p in GERBER_DIR.iterdir()
        if p.is_file() and not p.name.endswith(ZIP_EXCLUDE_SUFFIX)
    )
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in members:
            zf.write(path, arcname=path.name)
    return [p.name for p in members]


def board_outline_mm() -> tuple[float, float]:
    """Measure the board from the plotted Edge.Cuts gerber.

    Coordinates are plotted at 6 decimal places in millimetres, e.g.
    ``X125480000Y-47199999D02*`` is (125.48, -47.20) mm.
    """
    edge = next(GERBER_DIR.glob("*Edge_Cuts.gbr"))
    point = re.compile(r"^X(-?\d+)Y(-?\d+)")

    xs: list[float] = []
    ys: list[float] = []
    for line in edge.read_text(encoding="utf-8").splitlines():
        match = point.match(line)
        if match:
            xs.append(int(match.group(1)) / 1e6)
            ys.append(int(match.group(2)) / 1e6)

    if not xs:
        raise ExportError(f"could not read any outline points from {edge.name}")
    return (max(xs) - min(xs), max(ys) - min(ys))


def write_order_doc(
    assembled: list[dict[str, str]],
    mapping: dict[str, dict[str, str]],
    positions: list[dict[str, str]],
    zip_members: list[str],
    distinct_parts: int,
) -> None:
    """Write the order sheet: settings, part list, and what to verify."""
    width, height = board_outline_mm()
    board_hash = hashlib.sha256(BOARD.read_bytes()).hexdigest()[:12]

    sides = {row["Side"] for row in assembled}
    smt_side = sides.pop() if len(sides) == 1 else "both"

    hand = [
        (ref, entry) for ref, entry in sorted(mapping.items())
        if entry["Assemble"].lower() != "yes"
        and entry["Note"].startswith("Hand-soldered")
    ]
    not_parts = [
        ref for ref, entry in sorted(mapping.items())
        if entry["Assemble"].lower() != "yes"
        and not entry["Note"].startswith("Hand-soldered")
    ]

    lines: list[str] = []
    add = lines.append

    add("# naub V2 — JLCPCB order sheet")
    add("")
    add("Generated by `pcb/export_fab.py`. Do not edit by hand; re-run instead.")
    add("")
    add(f"- Board source: `pcb/{BOARD.name}` (sha256 `{board_hash}`)")
    add(f"- Board size: **{width:.1f} × {height:.1f} mm**, **2 layers**")
    add(f"- Parts assembled by JLC: **{len(assembled)}** placements, "
        f"{distinct_parts} distinct parts, all on the **{smt_side}** side")
    add("")

    add("## Files to upload")
    add("")
    add("| Upload step | File |")
    add("| --- | --- |")
    add(f"| Gerbers | `export/{ZIP_PATH.name}` |")
    add(f"| BOM | `export/{BOM_PATH.name}` |")
    add(f"| CPL / placement | `export/{CPL_PATH.name}` |")
    add("")

    add("## PCB settings")
    add("")
    add("Anything not listed can stay at the JLC default.")
    add("")
    add("| Setting | Value |")
    add("| --- | --- |")
    add("| Layers | 2 |")
    add(f"| Dimensions | {width:.1f} × {height:.1f} mm |")
    add("| PCB thickness | 1.6 mm |")
    add("| Surface finish | HASL (lead-free is fine) |")
    add("| Outer copper weight | 1 oz |")
    add("| Via covering | Tented |")
    add("")

    add("## Assembly settings")
    add("")
    add("| Setting | Value |")
    add("| --- | --- |")
    add("| PCBA type | Economic (no fine-pitch or BGA on this board) |")
    add(f"| Assembly side | {smt_side.capitalize()} side |")
    add("| Tooling holes | Added by JLC |")
    add("")

    add("## Parts JLC assembles")
    add("")
    add("| Comment | LCSC | Qty | Designators |")
    add("| --- | --- | --- | --- |")
    groups: dict[tuple[str, str], list[str]] = {}
    for row in assembled:
        groups.setdefault((row["Comment"], row["LCSC"]), []).append(row["Ref"])
    for (comment, lcsc), refs in sorted(groups.items()):
        add(f"| {comment} | {lcsc} | {len(refs)} | {', '.join(sorted(refs))} |")
    add("")
    add(f"All {distinct_parts} are JLCPCB **Basic** parts, so there is no "
        "per-part setup fee.")
    add("")

    add("## Parts you hand-solder")
    add("")
    add("These are deliberately absent from the BOM and CPL. JLC will ship the")
    add("board with these footprints bare.")
    add("")
    add("| Designator | Part |")
    add("| --- | --- |")
    for ref, entry in hand:
        part = entry["Note"].replace("Hand-soldered — ", "")
        add(f"| {ref} | {part} |")
    add("")
    add(f"Also excluded, because they are not components: {', '.join(not_parts)}.")
    add("")

    add("## Check before paying")
    add("")
    add("1. **Gerber preview** — confirm the outline is a single "
        f"{width:.0f} × {height:.0f} mm rounded rectangle and that both copper "
        "layers look right.")
    add("2. **Placement preview** — JLC renders each part on the board. Walk the "
        "list and confirm every part sits on its pads.")
    add("3. **Q1 rotation** — 2N7002 in SOT-23 is the one part here whose "
        "orientation matters. KiCad and JLC do not always agree on the zero "
        "angle for 3-pin packages, so check Q1 specifically in the placement "
        "preview and rotate it there if pin 1 is wrong. The passives are "
        "symmetric, so their rotation does not matter.")
    add("4. **Bare footprints** — confirm the encoders, key switches, display "
        "and the RP2350-Tiny module are all shown unpopulated.")
    add("")

    add("## Notes")
    add("")
    add("- No fiducials on this board. JLC does not require them for 0603/0805 "
        "and SOT-23 work; they place from the board edge and their own tooling "
        "holes. Add them only if a future revision gets fine-pitch parts.")
    add("- The drill map (`*-drl_map.gbr`) is plotted into `export/gerbers/` for "
        "inspection but kept out of the upload zip, since JLC's parser can "
        "mistake it for a copper layer.")
    add("- Plated and non-plated holes ship as two separate files "
        "(`*-PTH.drl`, `*-NPTH.drl`). The 19 non-plated holes — M2 mounting, "
        "Kailh Choc alignment posts, encoder bushings — must stay unplated, "
        "and a merged drill file records that only in a comment some fabs "
        "ignore.")
    add(f"- Zip contains {len(zip_members)} files: "
        + ", ".join(f"`{n}`" for n in zip_members) + ".")
    add("")

    ORDER_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not BOARD.exists():
        print(f"error: board not found at {BOARD}", file=sys.stderr)
        return 1

    try:
        cli = find_kicad_cli()
        print(f"kicad-cli: {cli}")
        check_board_not_open()

        # Reconcile the board against the assembly map BEFORE writing anything,
        # so a mismatch leaves any previous export untouched rather than
        # replacing it with a half-finished one.
        print("reading placements ...")
        positions = read_positions(cli)
        mapping = read_assembly_map()
        assembled = reconcile(positions, mapping)

        EXPORT_DIR.mkdir(parents=True, exist_ok=True)

        print("plotting gerbers + drill ...")
        plot_gerbers(cli)

        print("writing BOM + CPL ...")
        distinct = write_bom(assembled)
        write_cpl(assembled)

        print("zipping gerbers ...")
        zip_members = build_zip()

        print("writing order sheet ...")
        write_order_doc(assembled, mapping, positions, zip_members, distinct)

    except ExportError as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1

    hand_count = sum(
        1 for e in mapping.values()
        if e["Assemble"].lower() != "yes" and e["Note"].startswith("Hand-soldered")
    )
    print()
    print(f"  {ZIP_PATH.relative_to(PCB_DIR.parent)}  ({len(zip_members)} files)")
    print(f"  {BOM_PATH.relative_to(PCB_DIR.parent)}  ({distinct} distinct parts)")
    print(f"  {CPL_PATH.relative_to(PCB_DIR.parent)}  ({len(assembled)} placements)")
    print(f"  {ORDER_PATH.relative_to(PCB_DIR.parent)}")
    print()
    print(f"JLC assembles {len(assembled)} parts; {hand_count} are left for you.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
