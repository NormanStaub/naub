#!/usr/bin/env python3
"""A 2-layer maze router for naub_kicad.kicad_pcb.

    python route_board.py naub_kicad.kicad_pcb routed.kicad_pcb

WHY THIS EXISTS
    KiCad ships no autorouter and kicad-cli 10 has no Specctra DSN export, so the
    usual Freerouting round-trip needs a GUI operator and a JRE. This board is small
    enough (56 x 101.7 mm, 2 layers, ~110 connections) to route directly, and doing
    it here means the strategy can be board-specific rather than generic:

      - GND is never routed. Both layers carry a GND pour and DRC already reports
        zero unconnected GND items, so 58 of 232 pads are done for free.
      - B.Cu is preferred, because 46 of 61 footprints live there. F.Cu costs more
        per step so it gets used for crossings rather than as a co-equal plane,
        which keeps the front pour under the display as intact as possible.
      - Vias are expensive (5 mm equivalent). On a board this size the router will
        usually rather go around than punch through.

HOW
    0.1 mm grid, 8-way A* over a 2-layer state space. Obstacles are pad copper
    dilated by clearance + half a track width, tagged with the net that owns them:
    a cell is passable for net N when it is free or owned by N, which is what lets
    a trace leave its own pad. Cells claimed by two different nets are hard blocked.

    Multi-pad nets grow: route the first pad to the second, then treat every cell of
    the resulting trace as a source and A* from the whole trace to the next pad. That
    produces real T-junctions instead of daisy chains.

NOT A SUBSTITUTE FOR REVIEW
    The router optimises path cost. It does not know that the charge-pump caps around
    U2 want small loop area, or which nets are noisy. Read PCB_DESIGN.md 4.2 and look
    at the result before believing it.
"""
import heapq
import math
import sys
import time

import numpy as np

from board_model import Board

GRID = 0.1          # mm per cell
ORTH, DIAG = 10, 14  # cost units for a step
VIA_COST = 500       # 5 mm equivalent -- vias are a last resort, not a shortcut
LAYER_PENALTY = {0: 1.0, 1: 1.35}   # 0 = B.Cu (preferred), 1 = F.Cu
LAYERS = ['B.Cu', 'F.Cu']
SKIP_NETS = {'GND'}

# Obstacles are tested at cell centres on a 0.1 mm grid, so a legal cell can still
# sit a fraction of a cell inside the true keep-out. The first pass produced 11
# clearance violations in the 0.180-0.198 mm range against a 0.200 mm rule -- all
# quantisation, none of them more than 0.02 mm short. GUARD buys that back.
GUARD = 0.08
ACCESS_ERODE = 0.05   # keep track endpoints off the exact pad boundary

# Extra keep-out around GND pads only. Nothing routes to GND -- it lives in the
# pour -- so a signal trace is free to hug a GND pad right down to the 0.2 mm
# rule, and several did. That leaves no room for the zone's thermal spokes, and
# the board came back with 9 starved_thermal errors (pads reachable from one side
# only, against a min_resolved_spokes of 2). Buying back room here is cheaper than
# rewriting the zone rules.
GND_HALO = 0.45


class Grid:
    def __init__(self, board):
        self.b = board
        self.margin = 1.0
        self.x0 = board.xmin - self.margin
        self.y0 = board.ymin - self.margin
        self.W = int((board.xmax - board.xmin + 2 * self.margin) / GRID) + 1
        self.H = int((board.ymax - board.ymin + 2 * self.margin) / GRID) + 1
        self.net_ids = {}
        for p in board.pads:
            if p.net and p.net not in self.net_ids:
                self.net_ids[p.net] = len(self.net_ids) + 1
        # owner[layer]: 0 free, -1 hard blocked, >0 owned by that net id
        self.owner = np.zeros((2, self.H, self.W), np.int32)
        self.via_owner = np.zeros((2, self.H, self.W), np.int32)
        # keyed by id(pad), NOT (ref, num): 33 pad names on this board are reused
        # inside their own footprint -- U1.3V3 alone is three physically separate
        # pads (two castellations and a back solder-through pad). Keying by name
        # kept only the last of each and left the others quietly unrouted.
        self.pad_cells = {}
        self._build()

    def to_cell(self, x, y):
        return int(round((x - self.x0) / GRID)), int(round((y - self.y0) / GRID))

    def to_mm(self, ix, iy):
        return self.x0 + ix * GRID, self.y0 + iy * GRID

    # -- rasterising ------------------------------------------------------
    def _mask(self, pad, inflate):
        """Rounded-rect coverage mask for pad grown by `inflate`, plus its window.

        Every shape on this board (rect / roundrect / oval / circle) is a rounded
        rectangle, so one routine covers all four exactly. Growing a rounded rect
        by d grows both half-extents and the corner radius by d.
        """
        r = pad.corner_radius() + inflate
        hw, hh = pad.w / 2 + inflate, pad.h / 2 + inflate
        a = math.radians(-pad.angle)
        ca, sa = math.cos(a), math.sin(a)
        cx, cy = self.to_cell(pad.x, pad.y)
        rc = int(math.ceil(math.hypot(hw, hh) / GRID)) + 1
        y0, y1 = max(0, cy - rc), min(self.H, cy + rc + 1)
        x0, x1 = max(0, cx - rc), min(self.W, cx + rc + 1)
        if y0 >= y1 or x0 >= x1:
            return None, 0, 0, 0, 0
        yy, xx = np.mgrid[y0:y1, x0:x1]
        dx = (self.x0 + xx * GRID) - pad.x
        dy = (self.y0 + yy * GRID) - pad.y
        lx = np.abs(dx * ca + dy * sa)     # into pad-local space
        ly = np.abs(-dx * sa + dy * ca)
        ex = np.maximum(lx - (hw - r), 0.0)
        ey = np.maximum(ly - (hh - r), 0.0)
        mask = (lx <= hw) & (ly <= hh) & (ex * ex + ey * ey <= r * r)
        return mask, y0, y1, x0, x1

    def _stamp(self, arr, layer, pad, inflate, net_id, only_free=False):
        """Paint pad + inflate into arr[layer], merging ownership.

        only_free is for the GND halo: it claims empty space but never overwrites
        another net's keep-out into a hard block. Without that, the halo could
        swallow a neighbouring pad's entire escape area and the pad would be
        dropped from routing silently -- which is how U1.3V3 came back unconnected.
        """
        mask, y0, y1, x0, x1 = self._mask(pad, inflate)
        if mask is None or not mask.any():
            return
        sub = arr[layer, y0:y1, x0:x1]
        if only_free:
            sub[mask & (sub == 0)] = net_id
        elif net_id < 0:
            sub[mask] = -1
        else:
            clash = mask & (sub != 0) & (sub != net_id)
            sub[mask & (sub == 0)] = net_id
            sub[clash] = -1

    def _build(self):
        b = self.b
        half = b.track_w / 2
        infl_track = b.clearance + half + GUARD
        infl_via = b.clearance + b.via_d / 2 + GUARD
        for p in b.pads:
            nid = self.net_ids.get(p.net, -1) if p.net else -1
            if p.net is None and p.drill and p.w <= p.drill + 0.01:
                extra = b.hole_clear - b.clearance   # bare hole, no annulus
            else:
                extra = 0.0
            for li, lname in enumerate(LAYERS):
                if not b.on_layer(p, lname):
                    continue
                self._stamp(self.owner, li, p, infl_track + extra, nid)
                self._stamp(self.via_owner, li, p, infl_via + extra, nid)
            # record raw copper cells (no inflation) as the pad's access points
            if p.net:
                cells = set()
                for li, lname in enumerate(LAYERS):
                    if not b.on_layer(p, lname):
                        continue
                    cells |= self._raw_cells(li, p)
                self.pad_cells[id(p)] = cells

        # Second pass, lowest priority: widen the keep-out around GND pads into
        # whatever space is still free, so the pour keeps room for thermal spokes.
        gnd_id = self.net_ids.get('GND', -1)
        for p in b.pads:
            if p.net != 'GND':
                continue
            for li, lname in enumerate(LAYERS):
                if not b.on_layer(p, lname):
                    continue
                self._stamp(self.owner, li, p, infl_track + GND_HALO, gnd_id,
                            only_free=True)

        # board edge: the whole track (or via pad) stays inside outline - clearance
        for arr, ee in ((self.owner, b.edge_clear + half + GUARD),
                        (self.via_owner, b.edge_clear + b.via_d / 2 + GUARD)):
            xa, _ = self.to_cell(b.xmin + ee, 0)
            xb, _ = self.to_cell(b.xmax - ee, 0)
            _, ya = self.to_cell(0, b.ymin + ee)
            _, yb = self.to_cell(0, b.ymax - ee)
            arr[:, :ya + 1, :] = -1
            arr[:, yb:, :] = -1
            arr[:, :, :xa + 1] = -1
            arr[:, :, xb:] = -1

    def _raw_cells(self, layer, pad):
        """Cells a track may legally END in -- i.e. solidly inside the pad.

        Eroded by ACCESS_ERODE so a segment endpoint lands inside the copper
        rather than exactly on its boundary. Small pads that erode to nothing
        fall back to the un-eroded shape, and failing that to the pad centre.
        """
        for erode in (ACCESS_ERODE, 0.0):
            mask, y0, y1, x0, x1 = self._mask(pad, -erode)
            if mask is not None and mask.any():
                ys, xs = np.nonzero(mask)
                return {(layer, int(y0 + a), int(x0 + b)) for a, b in zip(ys, xs)}
        cx, cy = self.to_cell(pad.x, pad.y)
        return {(layer, cy, cx)}


NEIGH = [(-1, 0, ORTH), (1, 0, ORTH), (0, -1, ORTH), (0, 1, ORTH),
         (-1, -1, DIAG), (-1, 1, DIAG), (1, -1, DIAG), (1, 1, DIAG)]


class Router:
    def __init__(self, grid):
        self.g = grid
        self.tracks = []      # (net, layer, [(x,y),...])
        self.vias = []        # (net, x, y)

    def passable(self, layer, iy, ix, nid):
        o = self.g.owner[layer, iy, ix]
        return o == 0 or o == nid

    def route_net(self, net, pads):
        g = self.g
        nid = g.net_ids[net]
        groups = []
        for p in pads:
            c = g.pad_cells.get(id(p), set())
            c = {k for k in c if self.passable(k[0], k[1], k[2], nid)}
            if c:
                groups.append((p, c))
            else:
                # never drop a pad quietly -- an unreachable pad is an unrouted
                # connection that will not show up as a routing failure
                print(f"    !! {p.ref}.{p.num} has no reachable copper (walled in)")
        if len(groups) < 2:
            return 0, len(pads) - 1 if len(pads) > 1 else 0
        # merge pads that already share cells (e.g. U1's castellation + back pad)
        merged = []
        for p, c in groups:
            hit = None
            for m in merged:
                if m['cells'] & c:
                    hit = m
                    break
            if hit:
                hit['cells'] |= c
                hit['pads'].append(p)
            else:
                merged.append({'cells': set(c), 'pads': [p]})
        if len(merged) < 2:
            return 0, 0
        connected = merged[0]
        rest = merged[1:]
        done = fail = 0
        while rest:
            # nearest remaining group by centroid, then A* from the whole net
            def cen(m):
                return (sum(p.x for p in m['pads']) / len(m['pads']),
                        sum(p.y for p in m['pads']) / len(m['pads']))
            cx, cy = cen(connected)
            rest.sort(key=lambda m: (cen(m)[0] - cx) ** 2 + (cen(m)[1] - cy) ** 2)
            tgt = rest.pop(0)
            path = self.astar(connected['cells'], tgt['cells'], nid)
            if path is None:
                fail += 1
                connected['cells'] |= tgt['cells']
                connected['pads'] += tgt['pads']
                continue
            self.commit(net, nid, path)
            connected['cells'] |= tgt['cells'] | set(path)
            connected['pads'] += tgt['pads']
            done += 1
        return done, fail

    def astar(self, sources, targets, nid):
        g = self.g
        W, H = g.W, g.H
        owner = g.owner
        via_owner = g.via_owner
        tset = targets
        tx = sum(c[2] for c in tset) / len(tset)
        ty = sum(c[1] for c in tset) / len(tset)

        def h(layer, iy, ix):
            dx, dy = abs(ix - tx), abs(iy - ty)
            return ORTH * abs(dx - dy) + DIAG * min(dx, dy)

        gscore = {}
        came = {}
        pq = []
        for (l, iy, ix) in sources:
            if not (0 <= iy < H and 0 <= ix < W):
                continue
            o = owner[l, iy, ix]
            if not (o == 0 or o == nid):
                continue
            k = (l, iy, ix)
            gscore[k] = 0
            heapq.heappush(pq, (h(l, iy, ix), 0, k))
        if not pq:
            return None
        limit = 4_000_000
        popped = 0
        while pq:
            f, gs, cur = heapq.heappop(pq)
            if gs > gscore.get(cur, 1 << 60):
                continue
            if cur in tset:
                path = [cur]
                while cur in came:
                    cur = came[cur]
                    path.append(cur)
                return path[::-1]
            popped += 1
            if popped > limit:
                return None
            l, iy, ix = cur
            pen = LAYER_PENALTY[l]
            for dy, dx, base in NEIGH:
                ny, nx = iy + dy, ix + dx
                if not (0 <= ny < H and 0 <= nx < W):
                    continue
                o = owner[l, ny, nx]
                if not (o == 0 or o == nid):
                    continue
                if dy and dx:   # no corner cutting between two blocked cells
                    o1 = owner[l, iy, nx]
                    o2 = owner[l, ny, ix]
                    if not ((o1 == 0 or o1 == nid) and (o2 == 0 or o2 == nid)):
                        continue
                ng = gs + int(base * pen)
                k = (l, ny, nx)
                if ng < gscore.get(k, 1 << 60):
                    gscore[k] = ng
                    came[k] = cur
                    heapq.heappush(pq, (ng + h(*k), ng, k))
            # via to the other layer
            ol = 1 - l
            vo1, vo2 = via_owner[l, iy, ix], via_owner[ol, iy, ix]
            if (vo1 == 0 or vo1 == nid) and (vo2 == 0 or vo2 == nid):
                o = owner[ol, iy, ix]
                if o == 0 or o == nid:
                    ng = gs + VIA_COST
                    k = (ol, iy, ix)
                    if ng < gscore.get(k, 1 << 60):
                        gscore[k] = ng
                        came[k] = cur
                        heapq.heappush(pq, (ng + h(*k), ng, k))
        return None

    def commit(self, net, nid, path):
        """Claim the path in the obstacle map and turn it into segments/vias."""
        g = self.g
        half_t = int(math.ceil((self.g.b.clearance + self.g.b.track_w / 2 + GUARD) / GRID))
        half_v = int(math.ceil((self.g.b.clearance + self.g.b.via_d / 2 + GUARD) / GRID))
        for (l, iy, ix) in path:
            self._claim(g.owner, l, iy, ix, half_t, nid)
            self._claim(g.via_owner, l, iy, ix, half_v, nid)
        # split into per-layer runs; layer changes are vias
        runs = []
        cur = [path[0]]
        for prev, node in zip(path, path[1:]):
            if node[0] != prev[0]:
                runs.append(cur)
                x, y = g.to_mm(prev[2], prev[1])
                self.vias.append((net, x, y))
                for li in (0, 1):
                    self._claim(g.owner, li, prev[1], prev[2], half_v, nid)
                    self._claim(g.via_owner, li, prev[1], prev[2], half_v, nid)
                cur = [node]
            else:
                cur.append(node)
        runs.append(cur)
        for run in runs:
            if len(run) < 2:
                continue
            pts = [g.to_mm(c[2], c[1]) for c in self._simplify(run)]
            if len(pts) >= 2:
                self.tracks.append((net, LAYERS[run[0][0]], pts))

    def _claim(self, arr, layer, iy, ix, r, nid):
        y0, y1 = max(0, iy - r), min(self.g.H, iy + r + 1)
        x0, x1 = max(0, ix - r), min(self.g.W, ix + r + 1)
        sub = arr[layer, y0:y1, x0:x1]
        sub[sub == 0] = nid
        sub[(sub != nid)] = -1

    @staticmethod
    def _simplify(run):
        out = [run[0]]
        for i in range(1, len(run) - 1):
            ay, ax = run[i][1] - run[i - 1][1], run[i][2] - run[i - 1][2]
            by, bx = run[i + 1][1] - run[i][1], run[i + 1][2] - run[i][2]
            if (ay, ax) != (by, bx):
                out.append(run[i])
        out.append(run[-1])
        return out


ZONE_CLEAR = 0.5      # zone-to-foreign-copper, from the zones' connect_pads clearance
THERMAL_GAP = 0.5     # zone thermal_gap: how far a spoke reaches to a pad


def pour_map(grid, router):
    """Where the GND pour can still flow, per layer, after the signal routing.

    This board is 2-layer and every ground return goes through the pour, so a
    trace that fences a GND pad into a pocket is a real defect, not cosmetic --
    and it is invisible in the ratsnest because the pad "has" a net. Model it:
    a cell can hold pour copper if it is inside the zone, clear of foreign copper
    by ZONE_CLEAR, and has room for a min_thickness-wide channel (approximated by
    requiring its 3x3 neighbourhood to be clear too).
    """
    b = grid.b
    H, W = grid.H, grid.W
    gnd_id = grid.net_ids.get('GND')
    block = np.zeros((2, H, W), bool)
    for p in b.pads:
        if p.net == 'GND':
            continue
        for li, lname in enumerate(LAYERS):
            if not b.on_layer(p, lname):
                continue
            m, y0, y1, x0, x1 = grid._mask(p, ZONE_CLEAR)
            if m is not None:
                block[li, y0:y1, x0:x1] |= m
    # routed foreign copper
    for net, layer, pts in router.tracks:
        if net == 'GND':
            continue
        li = LAYERS.index(layer)
        r = int(math.ceil((ZONE_CLEAR + b.track_w / 2) / GRID))
        for (x1m, y1m), (x2m, y2m) in zip(pts, pts[1:]):
            ax, ay = grid.to_cell(x1m, y1m)
            bx, by = grid.to_cell(x2m, y2m)
            n = max(abs(bx - ax), abs(by - ay)) or 1
            for i in range(n + 1):
                cx = ax + (bx - ax) * i // n
                cy = ay + (by - ay) * i // n
                block[li, max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1] = True
    for net, x, y in router.vias:
        if net == 'GND':
            continue
        cx, cy = grid.to_cell(x, y)
        r = int(math.ceil((ZONE_CLEAR + b.via_d / 2) / GRID))
        block[:, max(0, cy - r):cy + r + 1, max(0, cx - r):cx + r + 1] = True
    # zone stops at the board edge
    e = b.edge_clear
    xa, _ = grid.to_cell(b.xmin + e, 0)
    xb, _ = grid.to_cell(b.xmax - e, 0)
    _, ya = grid.to_cell(0, b.ymin + e)
    _, yb = grid.to_cell(0, b.ymax - e)
    block[:, :ya + 1, :] = True
    block[:, yb:, :] = True
    block[:, :, :xa + 1] = True
    block[:, :, xb:] = True

    ok = ~block
    solid = np.zeros_like(ok)
    solid[:, 1:-1, 1:-1] = (ok[:, :-2, 1:-1] & ok[:, 2:, 1:-1] & ok[:, 1:-1, :-2] &
                            ok[:, 1:-1, 2:] & ok[:, 1:-1, 1:-1])
    return solid, gnd_id


def largest_component(solid_layer):
    """Flood-fill label; return a bool mask of the biggest connected region."""
    from collections import deque
    H, W = solid_layer.shape
    seen = np.zeros((H, W), np.int32)
    best, best_size, cur = None, 0, 0
    for sy in range(H):
        cand = np.nonzero(solid_layer[sy] & (seen[sy] == 0))[0]
        for sx in cand:
            if seen[sy, sx]:
                continue
            cur += 1
            q = deque([(sy, int(sx))])
            seen[sy, sx] = cur
            cells = 0
            while q:
                y, x = q.popleft()
                cells += 1
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < H and 0 <= nx < W and solid_layer[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = cur
                        q.append((ny, nx))
            if cells > best_size:
                best_size, best = cells, cur
    return seen == best, best_size


def check_and_stitch(grid, router, board):
    """Find GND pads the pour can no longer reach and drop a stitching via at each.

    A pad in a pocket on one layer is still rescuable through the other: F.Cu here
    is nearly untouched copper. So route a short GND stub from the pad to a spot
    where a via can legally land, and let the via reach the healthy pour.
    """
    solid, gnd_id = pour_map(grid, router)
    main = []
    for li in range(2):
        m, sz = largest_component(solid[li])
        main.append(m)
        print(f"  pour {LAYERS[li]}: main region {sz*GRID*GRID:.0f} mm2")
    orphans = []
    for p in board.pads:
        if p.net != 'GND':
            continue
        connected = False
        for li, lname in enumerate(LAYERS):
            if not board.on_layer(p, lname):
                continue
            m, y0, y1, x0, x1 = grid._mask(p, THERMAL_GAP + 0.1)
            if m is not None and (main[li][y0:y1, x0:x1] & m).any():
                connected = True
                break
        if not connected:
            orphans.append(p)
    if not orphans:
        print("  every GND pad still reaches the pour")
        return 0, 0
    print(f"  {len(orphans)} GND pad(s) cut off from the pour: " +
          ", ".join(f"{p.ref}.{p.num}" for p in orphans))
    fixed = 0
    for p in orphans:
        if stitch(grid, router, board, p, main, gnd_id):
            fixed += 1
    print(f"  stitched {fixed}/{len(orphans)}")
    return len(orphans), fixed


def stitch(grid, router, board, pad, main, gnd_id, reach=8.0):
    """Route pad -> nearest legal via site that touches the pour on either layer."""
    cx, cy = grid.to_cell(pad.x, pad.y)
    rc = int(reach / GRID)
    vr = int(math.ceil((board.via_d / 2 + ZONE_CLEAR + 0.1) / GRID))
    targets = set()
    for li in range(2):
        y0, y1 = max(1, cy - rc), min(grid.H - 1, cy + rc)
        x0, x1 = max(1, cx - rc), min(grid.W - 1, cx + rc)
        for y in range(y0, y1):
            for x in range(x0, x1):
                vo = grid.via_owner[:, y, x]
                if not all(v == 0 or v == gnd_id for v in vo):
                    continue
                if main[li][max(0, y - vr):y + vr + 1, max(0, x - vr):x + vr + 1].any():
                    targets.add((li, y, x))
    if not targets:
        return False
    src = grid.pad_cells.get(id(pad), set())
    src = {c for c in src if router.passable(c[0], c[1], c[2], gnd_id)}
    if not src:
        return False
    path = router.astar(src, targets, gnd_id)
    if path is None:
        return False
    el, ey, ex = path[-1]
    router.commit('GND', gnd_id, path)
    # A zone connects to a same-net track directly, so a via is only needed when
    # the pour we reached is on the OTHER layer. If A* already changed layer at
    # this cell, commit() has placed that via -- adding another put 15 pairs of
    # co-located drills in the first stitching pass.
    vr = int(math.ceil((board.via_d / 2 + ZONE_CLEAR + 0.1) / GRID))
    here = main[el][max(0, ey - vr):ey + vr + 1, max(0, ex - vr):ex + vr + 1].any()
    if not here:
        x, y = grid.to_mm(ex, ey)
        if not any(abs(vx - x) < 1e-6 and abs(vy - y) < 1e-6
                   for _, vx, vy in router.vias):
            router.vias.append(('GND', x, y))
    return True


def emit(board, tracks, vias, out_path):
    import uuid
    seen = set()
    deduped = []
    for net, x, y in vias:
        k = (net, round(x, 4), round(y, 4))
        if k not in seen:
            seen.add(k)
            deduped.append((net, x, y))
    vias = deduped
    lines = []
    for net, layer, pts in tracks:
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            lines.append(
                f'\t(segment\n\t\t(start {x1:.4f} {y1:.4f})\n\t\t(end {x2:.4f} {y2:.4f})\n'
                f'\t\t(width {board.track_w})\n\t\t(layer "{layer}")\n'
                f'\t\t(net "{net}")\n\t\t(uuid "{uuid.uuid4()}")\n\t)')
    for net, x, y in vias:
        lines.append(
            f'\t(via\n\t\t(at {x:.4f} {y:.4f})\n\t\t(size {board.via_d})\n'
            f'\t\t(drill {board.via_drill})\n\t\t(layers "F.Cu" "B.Cu")\n'
            f'\t\t(net "{net}")\n\t\t(uuid "{uuid.uuid4()}")\n\t)')
    s = board.text
    cut = s.rstrip()
    assert cut.endswith(')')
    cut = cut[:-1].rstrip('\n')
    open(out_path, 'w', encoding='utf8', newline='').write(
        cut + '\n' + '\n'.join(lines) + '\n)\n')


def main(src, dst):
    t0 = time.time()
    b = Board(src)
    print(f"board {b.xmax-b.xmin:.1f} x {b.ymax-b.ymin:.1f} mm, "
          f"{len(b.pads)} pads, track {b.track_w} clearance {b.clearance}")
    g = Grid(b)
    print(f"grid {g.W} x {g.H} @ {GRID} mm ({g.W*g.H*2/1e6:.2f} M cells)")
    r = Router(g)
    nets = {n: p for n, p in b.nets().items() if n not in SKIP_NETS}

    def span(ps):
        return max(math.hypot(a.x - c.x, a.y - c.y) for a in ps for c in ps)
    order = sorted(nets.items(), key=lambda kv: span(kv[1]))
    tot_done = tot_fail = 0
    failed = []
    for net, pads in order:
        d, f = r.route_net(net, pads)
        tot_done += d
        tot_fail += f
        flag = '  <-- FAILED' if f else ''
        print(f"  {net:14s} {len(pads):2d} pads  span {span(pads):6.1f} mm  "
              f"routed {d} failed {f}{flag}")
        if f:
            failed.append(net)
    print("\nchecking the GND pour:")
    check_and_stitch(g, r, b)
    length = sum(math.hypot(x2 - x1, y2 - y1)
                 for _, _, pts in r.tracks for (x1, y1), (x2, y2) in zip(pts, pts[1:]))
    print(f"\nconnections routed {tot_done}, failed {tot_fail}")
    print(f"segments {sum(len(p)-1 for _,_,p in r.tracks)}, vias {len(r.vias)}, "
          f"copper {length:.1f} mm")
    if failed:
        print("unrouted nets:", ", ".join(failed))
    emit(b, r.tracks, r.vias, dst)
    print(f"wrote {dst} in {time.time()-t0:.1f}s")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
