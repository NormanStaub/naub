# Why this board has a custom router

**Written:** 2026-07-24, after routing the V2 board
**Code:** [`route_board.py`](route_board.py) · [`board_model.py`](board_model.py)
**Companion docs:** [PCB_DESIGN.md](../PCB_DESIGN.md) · [PCB_ACTION_PLAN.md](../PCB_ACTION_PLAN.md) §D3 · [BRINGUP.md](../BRINGUP.md)

Writing an autorouter is normally the wrong call. This documents why it was the right one
*here*, and — more usefully — the conditions under which it stops being right, so the next
person (or the next board) can make the decision again rather than inherit it.

---

## The options that existed

| Option | Verdict |
|---|---|
| KiCad's built-in autorouter | **Does not exist.** Removed years ago; KiCad 10 ships no router. |
| Freerouting + Specctra DSN/SES | The de facto standard. Blocked on tooling — see below. |
| Commercial autorouters | Not applicable; this is a KiCad hobby-scale project. |
| Route it by hand | Best quality. Highest cost, and not something I can do for you. |
| Write one | What happened. |

## Why Freerouting lost, and it was closer than it sounds

Freerouting is the answer to this question 95% of the time, and I checked it first. Two
things ruled it out, neither of them a criticism of the tool:

**1. `kicad-cli` 10.0.3 has no Specctra export.** The full subcommand list is
`3dpdf, brep, drill, dxf, gencad, gerbers, glb, hpgl, ipc2581, ipcd356, odb, pdf, ply,
pos, ps, stats, step, stl, stpz, svg, u3d, vrml, xao`. No `dsn`. DSN export and SES import
both live in the GUI only (`File → Export → Specctra DSN`). That makes every routing
iteration a manual round-trip through KiCad — and there were **seven** iterations here,
each one triggered by a defect the previous DRC run exposed. Seven GUI round-trips is a
different kind of task than seven 13-second script runs.

**2. Java 8 is installed; Freerouting 2.x wants 21.** So the setup cost was a JRE download
plus a Freerouting download before the first trace.

Net: the "cheap, off-the-shelf" path had a *higher* startup cost than writing a router for
this specific board. That is an unusual situation and it is the whole reason this file
exists. On almost any other board the arithmetic goes the other way.

## Why writing one was tractable

Autorouting is hard in general. It was not hard here, and the reasons are worth being
explicit about, because they are exactly the assumptions that break on the next board:

- **The problem is small.** 2 layers, 56 × 101.7 mm, 50 nets, 232 pads. After removing GND
  and merging duplicate pads, **85 connections**. That is a size where a plain A* maze
  router does fine.
- **GND deleted 27% of the problem.** Both layers carry a pour and DRC already showed zero
  unconnected GND items, so 58 of 232 pads needed no traces at all — only a check that they
  still reach the pour afterwards.
- **The board outline is one rectangle.** A single `gr_rect` on Edge.Cuts. No cutouts, no
  curved boundary, no keep-out regions to model.
- **The design rules are uniform.** One netclass: 0.2 mm track, 0.2 mm clearance, 0.6/0.3
  via. No differential pairs, no length matching, no controlled impedance, no BGA escape,
  no blind/buried vias. Every hard problem a maze router is bad at is absent.
- **Placement was already good.** The bring-up hooks had been placed deliberately in the
  prior session (debug-part ratsnest 374 mm → 125 mm; `J_LCD`'s five signals sit at
  dx = 0.00 from `U2.36–40`). Most of the board was 4 mm stubs that route themselves.

## What a custom router bought that a generic one would not

This is the part that justifies the decision on merit rather than on tooling accident.

**Board-specific strategy, encoded directly.** A generic router optimises connectivity
against a rule set. Three decisions here are specific to this board and would have taken
constraint-file gymnastics to express to Freerouting:

- GND is never routed — it lives in the pour.
- B.Cu is preferred (46 of 61 footprints are there), so F.Cu stays mostly intact pour
  underneath the display rather than becoming a co-equal signal plane.
- Vias are priced at a 5 mm detour, so the router goes around rather than through.

**Pour integrity, which is the actual risk on this board.** A trace that fences a GND pad
into a pocket is a real defect, and it is *invisible in the ratsnest* — the pad has a net,
so nothing complains. On a 2-layer board where every ground return goes through the pour,
that is the failure mode that matters. So `pour_map()` models where pour copper can still
exist after routing (inside the zone, clear of foreign copper by the zone clearance, with
room for a `min_thickness` channel), flood-fills it, and checks every GND pad against the
main region.

It found **9 GND pads cut off from the pour** and stitched each to the healthy F.Cu pour
with a via. For comparison, KiCad's own DRC reported only 5 unconnected items at that
stage — the model was stricter than the check, in the right direction. That analysis is
not something an off-the-shelf router would have done for me, and on this board it is
worth more than the routing itself.

## What it cost, honestly

**Four of my own bugs shipped into DRC before I caught them**, none found by reading the
code:

1. Oval pads rasterised as rectangles — five track ends landed in corners the pads do not
   have (`track_dangling`).
2. Grid quantisation — 11 clearance violations at 0.180–0.198 mm against a 0.200 mm rule.
3. Stitching vias placed on top of vias `commit()` had already emitted — 15 pairs of
   co-located drills.
4. Pad access cells keyed by `(ref, num)` when **33 pad names on this board are reused
   inside their own footprint** — `U1.3V3` alone is three physically separate pads. This
   one *silently dropped a pad from routing*, which is the worst class of bug here because
   nothing reports it.

A mature router has none of these. The mitigation was not care; it was **running
`kicad-cli pcb drc --refill-zones` after every single iteration and treating KiCad's
opinion as ground truth over my own**, plus a structural diff proving all 61 footprints and
232 pads were byte-identical before and after. That verification loop is the reason the
output is trustworthy — not the router's cleverness.

**What it still does not do:** rip-up and retry, topological optimisation, push-and-shove,
or any notion of analog sensitivity. It optimises path cost. It does not know the
charge-pump caps around U2 want small loop area (`PCB_DESIGN` §4.2). Hand-routing would be
better; machine-routing plus a review pass was the right trade for a first spin that mainly
needs to be fabricable and probeable.

## When to stop using this

Rewrite the decision — and pay the Freerouting setup cost, or route by hand — if any of
these become true:

- **More than 2 layers**, or blind/buried vias.
- **A BGA or fine-pitch part** that needs a real escape strategy.
- **Any high-speed or impedance-controlled net**, differential pairs, or length matching.
- **Density rises enough that the first pass stops succeeding.** Right now it routes 85/85
  with no rip-up at all. The moment it starts failing connections, the missing feature is
  rip-up-and-retry, and that is the point where writing more router stops being cheaper
  than adopting one.
- **Placement is still moving a lot.** Re-routing is one command, so this is currently an
  argument *for* the script — but only while the router keeps succeeding trivially.

## Do not try to predict the zone filler

The single most expensive mistake in this work was modelling a KiCad rule instead of
measuring it. `pour_map()` exists to catch GND pads that the routing has fenced out of
the pour, and it is genuinely useful — but it went through **three** refinements, each of
which still called `C_V1.2` healthy while KiCad's own fill left it unconnected:

1. flood-fill with a 1-cell erosion,
2. a stricter 2-cell erosion,
3. real thermal-spoke counting against `min_resolved_spokes`.

Only then did the actual bug surface, and it was not in the model at all: the stitching
routine let the rescued pad's *own* cell qualify as a via target whenever the pour passed
within a via's reach on the far side of a trace. A\* returned a single-cell path,
`commit()` emitted neither segment nor via, and `stitch()` reported success having done
nothing. The pad stayed open through every "fix".

The same lesson repeated on silkscreen. A pass to nudge reference designators off pads
scored candidate positions against pad overlap — and made it worse (`silk_over_copper`
33 → 37, `silk_overlap` 19 → 35), because KiCad's rule is not the rule I implemented.
That pass was deleted rather than shipped.

**So: the model gets you most of the way in one pass, and DRC decides.** `--force-stitch`
exists for exactly this — feed it the pads DRC reports and stop arguing with the filler.

## Reproducing

```sh
python place_passives.py naub_kicad.kicad_pcb placed.kicad_pcb    # optional
python route_board.py placed.kicad_pcb out.kicad_pcb
kicad-cli pcb drc --refill-zones --save-board --format json -o drc.json out.kicad_pcb
# if DRC reports unconnected GND pads, name them and route again:
python route_board.py placed.kicad_pcb out.kicad_pcb --force-stitch=C_V1.2
```

The DRC line is not optional, and neither is the loop. `--refill-zones` also solves a
problem that had been muddying every DRC reading in this project: without it, DRC measures
the *stale* zone fill and invents ~90–500 phantom clearance violations that have nothing to
do with geometry.

## A format fact worth keeping

A pad's stored `at` angle in `.kicad_pcb` is **absolute**, not relative to its footprint.
This was established by experiment rather than assumption: rotating a footprint's `at`
angle while leaving its pads alone made DRC report `lib_footprint_mismatch` on exactly the
11 parts rotated and nothing else, which can only happen if the pad's library-relative
angle changed. `board_model.py` had been computing `fang + pang`; every pad on this board
stores 180 and every footprint sits at a multiple of 90, so the error produced identical
geometry throughout and stayed invisible until a rotation was attempted.
