"""Read naub_kicad.kicad_pcb into something a router can reason about.

Pulled out of route_board.py so the geometry can be tested on its own -- if the
pad math is wrong, everything downstream is wrong in ways that look like a bad
router rather than a bad parser.

Coordinates are KiCad board mm throughout. Angles follow KiCad's convention:
positive is counter-clockwise on screen, which means the rotation applied to a
footprint-local point is by MINUS the stated angle.
"""
import math
import re

TOK = re.compile(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()]+')


def parse(text):
    """KiCad s-expression -> nested lists. Atoms stay strings; quotes stripped."""
    stack = [[]]
    for t in TOK.findall(text):
        if t == '(':
            stack.append([])
        elif t == ')':
            top = stack.pop()
            stack[-1].append(top)
        elif t.startswith('"'):
            stack[-1].append(t[1:-1].replace('\\"', '"').replace('\\\\', '\\'))
        else:
            stack[-1].append(t)
    return stack[0][0]


def find(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def first(node, key):
    f = find(node, key)
    return f[0] if f else None


def val(node, key, idx=1, cast=float, default=None):
    f = first(node, key)
    if f is None or len(f) <= idx:
        return default
    return cast(f[idx])


class Pad:
    __slots__ = ('ref', 'num', 'net', 'x', 'y', 'angle', 'w', 'h', 'shape',
                 'layers', 'drill', 'is_th', 'rratio')

    def corner_radius(self):
        """Every KiCad pad shape here is a rounded rectangle with some radius.

        rect r=0, roundrect r=rratio*min(w,h), oval/circle r=min(w,h)/2. Treating
        them uniformly matters: the first routing pass rasterised ovals as plain
        rectangles and put five track ends in corners the pad does not actually
        have, which DRC reported as dangling tracks.
        """
        if self.shape == 'rect':
            return 0.0
        if self.shape in ('oval', 'circle'):
            return min(self.w, self.h) / 2
        return (self.rratio or 0.25) * min(self.w, self.h)

    def __repr__(self):
        return f"<{self.ref}.{self.num} {self.net} @{self.x:.3f},{self.y:.3f}>"

    def corners(self):
        """Four corners of the pad rectangle (roundrect/oval treated as their bbox)."""
        a = math.radians(-self.angle)
        ca, sa = math.cos(a), math.sin(a)
        hw, hh = self.w / 2, self.h / 2
        return [(self.x + dx * ca - dy * sa, self.y + dx * sa + dy * ca)
                for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]


class Board:
    def __init__(self, path):
        self.path = path
        self.text = open(path, encoding='utf8').read()
        self.tree = parse(self.text)
        self._read_outline()
        self._read_pads()
        self._read_rules()

    # -- board outline ----------------------------------------------------
    def _read_outline(self):
        xs, ys = [], []
        for kind in ('gr_rect', 'gr_line', 'gr_arc', 'gr_poly', 'gr_circle'):
            for g in find(self.tree, kind):
                if val(g, 'layer', 1, str) != 'Edge.Cuts':
                    continue
                for k in ('start', 'end', 'mid', 'center'):
                    for p in find(g, k):
                        xs.append(float(p[1]))
                        ys.append(float(p[2]))
        self.xmin, self.xmax = min(xs), max(xs)
        self.ymin, self.ymax = min(ys), max(ys)

    # -- design rules -----------------------------------------------------
    def _read_rules(self):
        import json
        import os
        pro = os.path.splitext(self.path)[0] + '.kicad_pro'
        ds = json.load(open(pro, encoding='utf8'))['board']['design_settings']
        cls = json.load(open(pro, encoding='utf8'))['net_settings']['classes'][0]
        self.track_w = cls['track_width']
        self.clearance = cls['clearance']
        self.via_d = cls['via_diameter']
        self.via_drill = cls['via_drill']
        self.edge_clear = ds['rules']['min_copper_edge_clearance']
        self.hole_clear = ds['rules']['min_hole_clearance']

    # -- pads -------------------------------------------------------------
    def _read_pads(self):
        self.pads = []
        self.footprints = []
        for fp in find(self.tree, 'footprint'):
            at = first(fp, 'at')
            fx, fy = float(at[1]), float(at[2])
            fang = float(at[3]) if len(at) > 3 else 0.0
            ref = None
            for p in find(fp, 'property'):
                if len(p) > 2 and p[1] == 'Reference':
                    ref = p[2]
            flay = val(fp, 'layer', 1, str)
            self.footprints.append((ref, fx, fy, fang, flay))
            a = math.radians(-fang)
            ca, sa = math.cos(a), math.sin(a)
            for pd in find(fp, 'pad'):
                p = Pad()
                p.ref, p.num = ref, pd[1]
                ptype, p.shape = pd[2], pd[3]
                pat = first(pd, 'at')
                lx, ly = float(pat[1]), float(pat[2])
                pang = float(pat[3]) if len(pat) > 3 else 0.0
                p.x = fx + lx * ca - ly * sa
                p.y = fy + lx * sa + ly * ca
                p.angle = fang + pang
                sz = first(pd, 'size')
                p.w, p.h = float(sz[1]), float(sz[2])
                p.layers = set(first(pd, 'layers')[1:])
                p.rratio = val(pd, 'roundrect_rratio', 1, float, None)
                n = first(pd, 'net')
                p.net = n[1] if n else None
                dr = first(pd, 'drill')
                p.drill = None
                if dr:
                    nums = [x for x in dr[1:] if not isinstance(x, list)
                            and re.match(r'^[\d.]+$', str(x))]
                    if nums:
                        p.drill = max(float(x) for x in nums)
                p.is_th = ptype in ('thru_hole', 'np_thru_hole')
                self.pads.append(p)

    def on_layer(self, pad, layer):
        if pad.is_th:
            return True
        return layer in pad.layers or '*.Cu' in pad.layers

    def nets(self):
        d = {}
        for p in self.pads:
            if p.net:
                d.setdefault(p.net, []).append(p)
        return d


if __name__ == '__main__':
    b = Board('naub_kicad.kicad_pcb')
    print(f"outline {b.xmax-b.xmin:.1f} x {b.ymax-b.ymin:.1f} mm  "
          f"(x {b.xmin}..{b.xmax}, y {b.ymin}..{b.ymax})")
    print(f"rules: track {b.track_w} clearance {b.clearance} "
          f"via {b.via_d}/{b.via_drill} edge {b.edge_clear}")
    print(f"{len(b.footprints)} footprints, {len(b.pads)} pads")
    nets = b.nets()
    print(f"{len(nets)} nets")
    th = sum(1 for p in b.pads if p.is_th)
    print(f"through-hole pads: {th}, smd: {len(b.pads)-th}")
    shapes = {}
    for p in b.pads:
        shapes[p.shape] = shapes.get(p.shape, 0) + 1
    print("shapes:", shapes)
    # sanity: J_LCD signal pins should sit at dx=0.00 from U2.36-40
    jl = {p.num: p for p in b.pads if p.ref == 'J_LCD'}
    u2 = {p.num: p for p in b.pads if p.ref == 'U2'}
    print("\nJ_LCD vs U2 alignment check:")
    for jn, un in (('2', '36'), ('3', '37'), ('4', '38'), ('5', '39'), ('6', '40')):
        if jn in jl and un in u2:
            print(f"  J_LCD.{jn} ({jl[jn].net}) x={jl[jn].x:.3f}  "
                  f"U2.{un} ({u2[un].net}) x={u2[un].x:.3f}  "
                  f"dx={jl[jn].x-u2[un].x:+.3f}  dy={jl[jn].y-u2[un].y:+.3f}")
