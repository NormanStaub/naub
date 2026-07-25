#!/usr/bin/env python3
"""Re-place the back-side passives, then hand the board back to route_board.py.

    python place_passives.py naub_kicad.kicad_pcb out.kicad_pcb

CLOSE KICAD FIRST.

WHAT MOVES AND WHAT DOES NOT
    Fixed: everything that defines the user interface or the mechanics -- U2 (the
    display), SW1-SW9 (encoders and keys), the mounting holes, and U1, whose
    position is set by the adapter FPC. Fixed too: the bring-up hooks (TP_*, JP_*,
    J1, J_LCD), whose placement was argued out separately in PCB_DESIGN 7.

    Movable: the R/C/Q passives, which is the whole point -- their placement was
    never deliberate. Plus one deliberate exception, J_ADC, explained below.

WHY THIS EXISTS
    The passives had clearly been dropped in convenient clumps rather than placed
    against the pins they serve. The headline defect: C_DEC, the decoupling cap for
    the module's 3V3 rail, sat 22.6 mm from U1's 3V3 pad. At that distance the loop
    inductance (very roughly 1 nH/mm) swamps the capacitor and it decouples nothing
    above a few MHz. A decoupling cap belongs against its pin or it may as well not
    be fitted.

ROTATION IS RESTRICTED, ON PURPOSE
    KiCad's pad `at` angle can be read as footprint-relative or absolute, and this
    board cannot distinguish them: every pad on it stores 180, and no non-square pad
    sits on a footprint rotated by +-90. So rather than guess:

      - 180 degree flips are always allowed. An axis-aligned rectangle rotated 180
        degrees is itself, so the geometry is identical under either reading.
      - 90 degree rotations are allowed ONLY for parts whose every pad is square to
        within SQUARE_TOL. There the ambiguity cannot matter -- the worst case is a
        0.05 mm difference in pad extent, well inside route_board.py's 0.08 mm
        rasterisation guard, and kicad-cli DRC is the authority on the result either
        way. This is what lets C_DEC stand on end in the 1.9 mm gap between U1's
        courtyard and J_ADC's, which is the only place it can both fit and do its job.

    Translation is unaffected by any of this.
"""
import collections
import math
import re
import sys

from board_model import Board

# Placement is set by the enclosure, the FPC, or an argued-out decision elsewhere.
FIXED = ({'U1', 'U2', 'J1', 'J_LCD', 'JP_3V3', 'JP_BL', 'REF**', None}
         | {f'SW{i}' for i in range(1, 10)}
         | {f'TP_{n}' for n in ('3V3', '3V3L', 'BLK', 'BLPWM', 'GND1', 'GND2',
                                'GND3', 'V0', 'VOUT', 'VSYS')})

# Deliberate exception. J_ADC's six pins sit one column off U1's GP26-29 edge
# contacts, which is a good place for a debug header and a terrible place for the
# only gap on U1's power corner: it walls off x 165.2-166.8 across the whole
# 3V3/GND row, and that is the one spot C_DEC needs. Two millimetres right costs
# J_ADC's stubs ~2 mm each and buys the module proper decoupling. The comb stays
# aligned -- same rows, same order, still dx-constant to the module.
# The four bench-supply pads move 2 mm right with it, keeping their 4 mm pair
# spacing and staying well inside the 0.5 mm edge clearance (board edge x=181.48).
PREMOVES = {'J_ADC':   (168.00, 85.58, 0),
            'TP_VSYS': (172.50, 73.00, 180), 'TP_GND1': (176.50, 73.00, 180),
            'TP_3V3':  (172.50, 78.00, 180), 'TP_GND2': (176.50, 78.00, 180)}

# A capacitor's electrical target is the PIN it serves, not merely "the nearest pad
# on the same net". Measuring the latter lets a cap satisfy itself by sitting next to
# another cap or a test point: the first version of this script left C_DEC 22.6 mm
# from U1 because JP_3V3 was 3.5 mm away on the same rail, and moved C_V0 next to
# TP_V0 rather than to U2.21. Only these parts count as an anchor.
ANCHORS = {'U1', 'U2'} | {f'SW{i}' for i in range(1, 10)}

# Nets whose length matters more than the sum suggests. A MOSFET gate is a
# high-impedance node and belongs short; the PWM feeding it through R_BL_G is a
# low-impedance logic output that can travel. Without this the optimiser trades a
# short gate for a short PWM run, which is backwards.
NET_WEIGHT = {'BL_PWM_GATE': 3.0}

SQUARE_TOL = 0.10    # |pad w - h| under this counts as square (see the note above)
STEP = 0.25          # search grid, mm
RADIUS = 14.0        # how far a part may be dragged from where it is now
MIN_GAP = 0.65       # pad-to-pad, between different parts
                     # the GND pour keeps room for thermal spokes (see ROUTING.md)
BODY_GAP = 0.15       # courtyard-to-courtyard, same side of the board
MIN_GAIN = 1.0       # don't churn placement for less than this
EDGE_KEEPOUT = 1.2   # pad edge to board edge


class Placer:
    def __init__(self, board):
        self.b = board
        self.fp = {f[0]: list(f) for f in board.footprints}
        self.byref = collections.defaultdict(list)
        for p in board.pads:
            self.byref[p.ref].append(p)
        self.pos = {}      # ref -> (x, y, angle)
        for ref, fx, fy, fang, flay in board.footprints:
            self.pos[ref] = (fx, fy, fang)
        self.square = {r: all(abs(p.w - p.h) <= SQUARE_TOL for p in ps)
                       for r, ps in self.byref.items()}
        self.movable = [r for r in self.byref
                        if r not in FIXED and r not in PREMOVES
                        and re.match(r'^(C_|R_|Q\d)', r or '')]

    def pads_at(self, ref, x, y, ang):
        """Absolute pad geometry for `ref` if its footprint sat at (x, y, ang)."""
        ox, oy, oang = self.fp[ref][1], self.fp[ref][2], self.fp[ref][3]
        a = math.radians(-(ang - oang))
        ca, sa = math.cos(a), math.sin(a)
        out = []
        for p in self.byref[ref]:
            dx, dy = p.x - ox, p.y - oy
            out.append((x + dx * ca - dy * sa, y + dx * sa + dy * ca,
                        max(p.w, p.h), p.net, p.num))
        return out

    def current_pads(self, skip=None):
        out = []
        for ref in self.byref:
            if ref == skip:
                continue
            x, y, a = self.pos[ref]
            for px, py, r, net, num in self.pads_at(ref, x, y, a):
                out.append((px, py, r, net, ref))
        return out

    def body(self, ref, x, y, ang):
        """Absolute courtyard bbox for `ref` placed at (x, y, ang)."""
        cy = self.b.courtyard.get(ref)
        if not cy:
            return None
        lx0, ly0, lx1, ly1 = cy
        a = math.radians(-ang)
        ca, sa = math.cos(a), math.sin(a)
        xs, ys = [], []
        for lx, ly in ((lx0, ly0), (lx1, ly0), (lx1, ly1), (lx0, ly1)):
            xs.append(x + lx * ca - ly * sa)
            ys.append(y + lx * sa + ly * ca)
        return min(xs), min(ys), max(xs), max(ys)

    def bodies(self, skip=None):
        out = []
        for ref, fx, fy, fang, flay in self.b.footprints:
            if ref == skip or ref is None:
                continue
            x, y, a = self.pos[ref]
            bb = self.body(ref, x, y, a)
            if bb:
                out.append((bb, flay, ref))
        return out

    def legal(self, ref, x, y, ang, others, bodies=None, layer=None):
        b = self.b
        if bodies is not None:
            mine = self.body(ref, x, y, ang)
            if mine:
                for (bx0, by0, bx1, by1), flay, oref in bodies:
                    if flay != layer:
                        continue   # a body on the other side of the board
                    if (mine[0] < bx1 + BODY_GAP and bx0 < mine[2] + BODY_GAP and
                            mine[1] < by1 + BODY_GAP and by0 < mine[3] + BODY_GAP):
                        return False
        for px, py, r, net, num in self.pads_at(ref, x, y, ang):
            if not (b.xmin + EDGE_KEEPOUT < px < b.xmax - EDGE_KEEPOUT and
                    b.ymin + EDGE_KEEPOUT < py < b.ymax - EDGE_KEEPOUT):
                return False
            for qx, qy, qr, qnet, qref in others:
                if abs(qx - px) > 6 or abs(qy - py) > 6:
                    continue
                need = r / 2 + qr / 2 + MIN_GAP
                if (qx - px) ** 2 + (qy - py) ** 2 < need * need:
                    return False
        return True

    def cost(self, ref, x, y, ang, others):
        """Weighted sum of pad -> nearest ANCHOR pad on the same net.

        GND is excluded: it rides the pour, so counting it would drag every cap
        toward the nearest ground pad for no electrical benefit. Nets with no anchor
        pad at all (the gate node, VSYS_BL) fall back to any same-net pad.
        """
        tot = 0.0
        for px, py, r, net, num in self.pads_at(ref, x, y, ang):
            if not net or net == 'GND':
                continue
            cand = [(qx, qy) for qx, qy, qr, qnet, qref in others
                    if qnet == net and qref in ANCHORS]
            if not cand:
                cand = [(qx, qy) for qx, qy, qr, qnet, qref in others if qnet == net]
            if not cand:
                continue
            best = min(math.hypot(qx - px, qy - py) for qx, qy in cand)
            tot += best * NET_WEIGHT.get(net, 1.0)
        return tot

    def improve(self, ref):
        others = self.current_pads(skip=ref)
        bodies = self.bodies(skip=ref)
        layer = {f[0]: f[4] for f in self.b.footprints}[ref]
        cx, cy, ca = self.pos[ref]
        base = self.cost(ref, cx, cy, ca, others)
        best = (base, cx, cy, ca)
        n = int(RADIUS / STEP)
        angles = [ca, ca + 180] + ([ca + 90, ca + 270] if self.square[ref] else [])
        for ang in angles:
            for iy in range(-n, n + 1):
                for ix in range(-n, n + 1):
                    x, y = cx + ix * STEP, cy + iy * STEP
                    c = self.cost(ref, x, y, ang, others)
                    if c >= best[0] - 1e-9:
                        continue
                    if not self.legal(ref, x, y, ang, others, bodies, layer):
                        continue
                    best = (c, x, y, ang)
        if base - best[0] >= MIN_GAIN:
            self.pos[ref] = best[1:]
            return base, best[0]
        return None

    def swaps(self):
        """Exchange two parts' positions when that helps both.

        The per-part search is greedy and order-dependent, so identical footprints
        competing for the same row can end up assigned to each other's pins -- the
        V0-V4 bias caps did exactly that under U2's pin row. A swap pass fixes the
        assignment without touching the geometry."""
        n = 0
        for i, a in enumerate(self.movable):
            for b in self.movable[i + 1:]:
                if self.b.courtyard.get(a) != self.b.courtyard.get(b):
                    continue
                pa, pb = self.pos[a], self.pos[b]
                oa = self.current_pads(skip=a)
                before = self.cost(a, *pa, oa) + self.cost(b, *pb, self.current_pads(skip=b))
                self.pos[a], self.pos[b] = pb, pa
                after = (self.cost(a, *self.pos[a], self.current_pads(skip=a)) +
                         self.cost(b, *self.pos[b], self.current_pads(skip=b)))
                if after < before - 0.05:
                    n += 1
                else:
                    self.pos[a], self.pos[b] = pa, pb
        print(f"  swap pass: {n} exchanges")
        return n

    def run(self, passes=5):
        moved = {}
        for it in range(passes):
            order = sorted(self.movable, key=lambda r: -self.cost(
                r, *self.pos[r], self.current_pads(skip=r)))
            changed = 0
            for ref in order:
                r = self.improve(ref)
                if r:
                    changed += 1
                    old = moved.get(ref, (r[0], None))[0]
                    moved[ref] = (old, r[1])
            print(f"  pass {it+1}: {changed} parts improved")
            if not changed and not self.swaps():
                break
        return moved


def write_board(path, out, positions, original):
    s = open(path, encoding='utf8').read()
    chunks = re.split(r'(\n\t\(footprint )', s)
    n = 0
    for i, c in enumerate(chunks):
        m = re.search(r'\(property "Reference" "([^"]+)"', c)
        if not m:
            continue
        ref = m.group(1)
        if ref not in positions:
            continue
        x, y, a = positions[ref]
        if (round(x, 4), round(y, 4), round(a) % 360) == (
                round(original[ref][0], 4), round(original[ref][1], 4),
                round(original[ref][2]) % 360):
            continue
        delta = round(a - original[ref][2]) % 360
        a = ((a + 180) % 360) - 180
        c = re.sub(
            r'(\n\t\t\(at )[-\d.]+ [-\d.]+(?: [-\d.]+)?(\))',
            lambda z: f"{z.group(1)}{x:g} {y:g}" + (f" {a:g}" if a else "") + z.group(2),
            c, count=1)
        if delta:
            # Pad angles are absolute (see board_model), so a rotation has to carry
            # them along or the footprint stops matching its library copy -- which is
            # how this convention got pinned down in the first place.
            def bump(z):
                pa = float(z.group(2)) if z.group(2) else 0.0
                pa = ((pa + delta + 180) % 360) - 180
                return f"{z.group(1)}" + (f" {pa:g}" if pa else "")
            c = re.sub(r'(\n\t\t\t\(at [-\d.]+ [-\d.]+)(?: ([-\d.]+))?', bump, c)
        chunks[i] = c
        n += 1
    # a placement change invalidates every trace; drop the copper and re-route
    body = ''.join(chunks)
    body, ns = re.subn(r'\n\t\(segment\n(?:\t\t.*\n)*?\t\)', '', body)
    body, nv = re.subn(r'\n\t\(via\n(?:\t\t.*\n)*?\t\)', '', body)
    open(out, 'w', encoding='utf8', newline='').write(body)
    print(f"  wrote {n} footprint moves; stripped {ns} segments and {nv} vias")


def main(src, dst):
    b = Board(src)
    pl = Placer(b)
    original = dict(pl.pos)
    for ref, (x, y, a) in PREMOVES.items():
        pl.pos[ref] = (x, y, a)
        print(f"pre-move {ref} -> ({x}, {y}, {a})")
    print(f"{len(pl.movable)} movable passives")
    moved = pl.run()
    print(f"\n{len(moved)} parts re-placed:")
    tot_before = tot_after = 0.0
    for ref in sorted(moved):
        before, after = moved[ref]
        ox, oy, oa = original[ref]
        nx, ny, na = pl.pos[ref]
        d = math.hypot(nx - ox, ny - oy)
        flip = ' flip' if round(na - oa) % 360 else ''
        print(f"  {ref:11s} ({ox:7.2f},{oy:7.2f}) -> ({nx:7.2f},{ny:7.2f})  "
              f"moved {d:5.1f} mm{flip:5s}  net length {before:6.2f} -> {after:6.2f}")
        tot_before += before
        tot_after += after
    print(f"\n  connection length over moved parts: {tot_before:.1f} -> {tot_after:.1f} mm"
          f"  ({tot_before-tot_after:.1f} mm saved)")
    write_board(src, dst, pl.pos, original)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
