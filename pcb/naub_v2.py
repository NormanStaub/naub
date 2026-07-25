#!/usr/bin/env python3
"""
naub V2 — circuit definition as code (SKiDL) → KiCad netlist.

This is the electrical connection spec from PCB_DESIGN.md, expressed as a
runnable script. It is the source of truth for "what connects to what"; you
still do placement / routing / outline in the KiCad GUI.

WHY THIS EXISTS
    Instead of hand-drawing a schematic, review the nets here (diff-able,
    version-controlled) and import the generated netlist into KiCad.

RUN
    pip install skidl
    python naub_v2.py            # writes naub_v2.net (KiCad netlist format)

THEN IN KICAD
    Create the KiCad project IN THIS DIRECTORY (pcb/) — the bundled
    fp-lib-table then auto-registers the project footprint lib naub.pretty
    under the nickname "naub". Open the PCB editor and use
    File → Import → Netlist… (Load and Test first, then Update PCB).
    Re-run this script + re-import after any electrical change.

PIN NUMBERS / FOOTPRINTS — all real now, import-ready
    Every footprint below resolves against KiCad's standard libs or
    naub.pretty, and every pin number matches that footprint's pads:
      MCU      : naub:Waveshare_RP2350-Tiny_Hybrid (2026-07-16, supersedes the
                 Pico 2 — see PCB_DESIGN.md §0). Hybrid solder-down carrier
                 footprint: 23-contact edge ring (castellation finger + 1.0mm
                 alignment/solder hole each) + 13 solder-through holes for the
                 back pads (GP16-23, 3V3, GND, SWD). Pads are NAMED (GP0...,
                 3V3, GND, VSYS, SWCLK, SWDIO), not numbered. GP16 is spent on
                 the module's onboard WS2812 (left unconnected here); GP24/25
                 don't exist on the carrier (NC mode-select pads); GP27/28/29
                 have 47R series resistors on-module (fine for GPIO/ADC).
      DOGM128  : Display:EA_DOGM128-6 (stock KiCad; pads 21-40 = display,
                 pads 1-3/18-20 = the LED55X46 backlight pins — the backlight
                 module solders through the SAME footprint, no separate part)
      Encoder  : naub:RotaryEncoder_Bourns_PEC11L-4xxxF-Sxxxx_Vertical
                 (pads lettered A/C/B/S1/S2 to match these pins)
      Choc     : naub:SW_Kailh_Choc_V1
      Headers  : Connector_PinHeader_2.54mm:PinHeader_1x0N_P2.54mm_Vertical
      Testpts  : TestPoint:TestPoint_THTPad_D2.0mm_Drill1.0mm (stock KiCad)
      R/C/FET  : Resistor_SMD / Capacitor_SMD / Package_TO_SOT_SMD (as before)

RP2350-TINY DELTAS vs the old Pico 2 netlist (PCB_DESIGN.md §0)
    - ENC3 switch: GP16 → GP23 (GP16 = onboard WS2812)
    - Backlight rail: Pico VBUS pin → module VSYS edge contact (USB 5V arrives
      over the adapter FPC; same ~5V, 3x 300R unchanged)
    - SWD header J1 now taps the module's SWCLK/GND/SWDIO solder-through pads
    - Expansion header J2 is GONE: GP22 went to BTN5. (GP26-29 were left dangling
      too, until the bring-up hooks below gave them J_ADC — see that section.)
      J2's ref is retired for good; the new rows are named J_LCD / J_ADC so that
      nothing here collides with the J2 in older docs and board revisions.

BRING-UP HOOKS (added 2026-07-24, before the first fab order)
    14 parts at the bottom of this file exist purely to make the first batch
    diagnosable: 10 THT probe pads, two 0R rail links (JP_3V3 / JP_BL) that cut the
    board into module | display | backlight, and two unpopulated 1x6 rows (J_LCD
    logic-analyzer tap, J_ADC spare-IO/bodge). Read that section before laying them
    out — several carry placement constraints that are the whole reason they work.
    Procedure that uses them: BRINGUP.md.

COUNT — 56 parts / 50 nets  (was 42 / 44 before the bring-up hooks)
    +14 parts as above. +6 nets: the two rail splits (+3V3_LCD, VSYS_BL) and the
    four SPARE_GP26..29 that J_ADC brought into existence. Older doc text quoting
    42/44, or 48 nets, predates this and the J2 removal respectively.

DO NOT OPEN THIS PROJECT'S SCHEMATIC EDITOR AND PRESS F8
    pcb/naub_kicad.kicad_sch is an intentionally EMPTY placeholder — this script is
    the schematic. "Update PCB from Schematic" (F8) would offer to delete every
    footprint on the board. When importing this netlist, also leave "delete
    footprints not in netlist" UNCHECKED: the 4 mounting holes and the
    naub_v2_layout template footprint exist only on the board, not here.

RESOLVED (was an open item)
    ST7565 charge-pump CAP3 far-end = CAP1N (pin 30), per the EA DOGM128-6
    datasheet p.4 "Single Supply 3.0/3.3V" application schematic
    (2× 1µF chain: CAP3P–CAP1N and CAP1N–CAP1P; VOUT cap goes to GND).

PEC11L HAND-SOLDER CAUTION
    Bourns specs wave soldering only ("hand soldering not recommended").
    Hobby hand assembly works in practice — fine tip, minimal dwell time.
"""

from skidl import *

# --- version-compat shim: Pin function enum moved to Pin.types in newer SKiDL
try:
    pin_types = Pin.types
except AttributeError:  # older SKiDL
    pin_types = Pin

PASSIVE = pin_types.PASSIVE
BIDIR = pin_types.BIDIR
PWRIN = pin_types.PWRIN
PWROUT = pin_types.PWROUT
INPUT = pin_types.INPUT


# ----------------------------------------------------------------------------
# Part factories (self-contained — no external symbol libs needed)
# ----------------------------------------------------------------------------
# NOTE ON `tag=ref` (added 2026-07-24): SKiDL derives each component's KiCad tstamp
# (unique ID) from its `tag`. With no tag, SKiDL invents a RANDOM one per run, so every
# regeneration produced a different tstamp for every part — the netlist was not
# reproducible and diffs were pure noise, defeating the "diff-able source of truth"
# premise above. Every part here has a unique explicit ref, so ref doubles as a stable
# tag. Keep passing it. Consequence for KiCad: tstamps are now stable, but the ones on
# the EXISTING board predate this change, so import with **"match by reference"**, not
# by unique ID, or KiCad will think every footprint is new.
def R(value, ref=None, footprint="Resistor_SMD:R_0603_1608Metric"):
    return Part(name="R", ref_prefix="R", value=value, ref=ref, tag=ref, tool=SKIDL,
                footprint=footprint,
                pins=[Pin(num=1, name="~", func=PASSIVE),
                      Pin(num=2, name="~", func=PASSIVE)])


def C(value, ref=None, footprint="Capacitor_SMD:C_0603_1608Metric"):
    return Part(name="C", ref_prefix="C", value=value, ref=ref, tag=ref, tool=SKIDL,
                footprint=footprint,
                pins=[Pin(num=1, name="~", func=PASSIVE),
                      Pin(num=2, name="~", func=PASSIVE)])


def nmos_2n7002(ref=None):
    # SOT-23: pad1=Gate, pad2=Source, pad3=Drain
    return Part(name="2N7002", ref_prefix="Q", value="2N7002", ref=ref, tag=ref, tool=SKIDL,
                footprint="Package_TO_SOT_SMD:SOT-23",
                pins=[Pin(num=1, name="G", func=INPUT),
                      Pin(num=2, name="S", func=PASSIVE),
                      Pin(num=3, name="D", func=PASSIVE)])


def encoder_pec11l(ref):
    # Bourns PEC11L (11mm LOW PROFILE): A / C(common) / B + switch S1 / S2.
    # PEC11L-4020F-S0020 = no detents, 20 PPR, 20mm flatted shaft, push switch —
    # keeps BOM_V2's zero-detent smooth-feel intent (ex-Alps EC11E1834403).
    # Other shaft lengths: -4015F-/-4025F-; detented feel: detent codes 1/2/3.
    # Footprint pads are lettered to match these pin numbers — no reconcile needed.
    # SH = the two plated mounting lugs on the metal frame. Tied to GND below:
    # they are the parts a user grabs, so a floating shaft would dump ESD through
    # the encoder contacts into the GPIO, and lugs soldered into the pour hold
    # far better against twisting/pressing than lugs in isolated holes.
    return Part(name="PEC11L", ref_prefix="SW", value="PEC11L-4020F-S0020", ref=ref, tag=ref, tool=SKIDL,
                footprint="naub:RotaryEncoder_Bourns_PEC11L-4xxxF-Sxxxx_Vertical",
                pins=[Pin(num="A", name="A", func=PASSIVE),
                      Pin(num="C", name="C", func=PASSIVE),
                      Pin(num="B", name="B", func=PASSIVE),
                      Pin(num="S1", name="S1", func=PASSIVE),
                      Pin(num="S2", name="S2", func=PASSIVE),
                      Pin(num="SH", name="SH", func=PASSIVE)])


def choc_switch(ref):
    return Part(name="Choc_V1", ref_prefix="SW", value="Kailh_Choc_Red", ref=ref, tag=ref, tool=SKIDL,
                footprint="naub:SW_Kailh_Choc_V1",
                pins=[Pin(num=1, name="1", func=PASSIVE),
                      Pin(num=2, name="2", func=PASSIVE)])


def pin_header(ref, names, value):
    fp = f"Connector_PinHeader_2.54mm:PinHeader_1x{len(names):02d}_P2.54mm_Vertical"
    return Part(name="Conn", ref_prefix="J", value=value, ref=ref, tag=ref, tool=SKIDL,
                footprint=fp,
                pins=[Pin(num=i + 1, name=n, func=PASSIVE) for i, n in enumerate(names)])


def test_point(ref, net, footprint="TestPoint:TestPoint_THTPad_D2.0mm_Drill1.0mm"):
    """A single probe pad. THT on purpose (see the bring-up section at the bottom).

    D2.0mm/1.0mm drill, not the smaller D1.5/0.7: a 1.0mm hole swallows a 0.64mm
    square header pin (0.9mm across the diagonal — a 0.7mm drill will NOT take one)
    or a 20AWG bodge wire. On a hand-built first article the ability to solder
    something *into* the test point beats a tidier SMD dot every time.
    """
    tp = Part(name="TestPoint", ref_prefix="TP", value="TP", ref=ref, tag=ref, tool=SKIDL,
              footprint=footprint,
              pins=[Pin(num=1, name="1", func=PASSIVE)])
    tp[1] += net
    return tp


def link(ref, net_in, net_out):
    """0Ω 0603 series link — a rail you can OPEN. See "Bring-up hooks" below.

    Deliberately a 0603 chip and not a 2.54mm shunt header: a shunt would stand
    ~11mm off the BACK of the board, which is the side the module and every
    passive already occupy and the side that faces the enclosure floor. A 0402/0603
    link costs no height, is still a two-second removal with tweezers + iron, and
    doubles as a place to fit a 1Ω current-sense part if you ever want the rail
    current on a scope instead of a bench meter.
    """
    j = R("0R", ref=ref)
    j[1] += net_in
    j[2] += net_out
    return j


def rp2350_tiny(ref="U1"):
    """Waveshare RP2350-Tiny module on the naub hybrid carrier footprint.

    Pad numbers ARE the signal names (the footprint names its pads GP0...,
    3V3, GND, VSYS, SWCLK, SWDIO), so pin num == pin name throughout.
    Multiple pads share the numbers 3V3 (edge + back) and GND (edge + back +
    SWD row) — KiCad nets them together, same as the Pico's many GND pads.

    Physical map (module top view, FPC/USB edge up):
      edge left   : VSYS GND 3V3 GP29 GP28 GP27 GP26 GP15 GP14
      edge right  : GP0..GP8   ·   edge bottom: GP13..GP9
      back pads   : GND+GP16..19 col · 3V3+GP23..20 col · SWDIO/GND/SWCLK row
    GP16 = onboard WS2812 (do not use for I/O); GP24/25 not brought out.
    """
    sigs = (["GP%d" % i for i in range(24)] +            # GP0..GP23 (no 24/25)
            ["GP26", "GP27", "GP28", "GP29",
             "3V3", "GND", "VSYS", "SWCLK", "SWDIO"])
    def fn(name):
        if name in ("3V3", "VSYS"):
            return PWROUT          # 3V3 from the module LDO; VSYS = USB 5V in
        if name == "GND":
            return PWRIN
        return BIDIR               # GPxx + SWD
    return Part(name="RP2350_Tiny", ref_prefix="U", ref=ref, tag=ref, value="Waveshare_RP2350-Tiny", tool=SKIDL,
                footprint="naub:Waveshare_RP2350-Tiny_Hybrid",
                pins=[Pin(num=s, name=s, func=fn(s)) for s in sigs])


def dogm128(ref="U2"):
    """EA DOGM128W-6 (ST7565R on glass). Real pad numbers per datasheet/KiCad FP.

    Pads 21-40 are the display glass; pads 1-3 (A1-A3) and 18-20 (C1-C3) are
    the EA LED55X46 backlight's pins — the backlight module clips under the
    display and solders through the SAME footprint (3 independent LED paths).
    """
    pins = [
        (36, "SI"), (37, "SCL"), (38, "A0"), (39, "RST"), (40, "CS1B"),  # SPI
        (35, "VDD"), (34, "VDD2"), (26, "VSS"), (33, "VSS"),             # power
        (21, "V0"), (22, "V1"), (23, "V2"), (24, "V3"), (25, "V4"),      # LCD bias
        (32, "VOUT"),                                                    # booster out
        (29, "CAP1P"), (30, "CAP1N"), (28, "CAP2P"), (27, "CAP2N"), (31, "CAP3P"),
        (1, "A1"), (2, "A2"), (3, "A3"),      # backlight anodes (per LED path)
        (18, "C1"), (19, "C2"), (20, "C3"),   # backlight cathodes
    ]
    return Part(name="DOGM128W-6", ref_prefix="U", ref=ref, tag=ref, value="EA_DOGM128W-6", tool=SKIDL,
                footprint="Display:EA_DOGM128-6",
                pins=[Pin(num=n, name=nm, func=PASSIVE) for n, nm in pins])


# ----------------------------------------------------------------------------
# Power rails
# ----------------------------------------------------------------------------
gnd = Net("GND")
p3v3 = Net("+3V3")          # module 3V3 (ME6217 LDO, 800mA) — SOURCE side of JP_3V3
p3v3_lcd = Net("+3V3_LCD")  # display side of JP_3V3 (U2 VDD/VDD2 + its local 100nF)
vsys = Net("VSYS_5V")       # USB 5V, arrives over the adapter FPC — SOURCE side of JP_BL
vsys_bl = Net("VSYS_BL")    # backlight side of JP_BL (R_BL1/2/3 only)

# --- Rail links: the two cuts that make this board bring-up-able (2026-07-24) ---
# The whole board falls into three independently testable blocks along two seams,
# because the load lists happen to be disjoint:
#     +3V3     → U1.3V3, C_BULK, C_DEC  ||JP_3V3||  U2.VDD/VDD2, C_LCD_VDD
#     VSYS_5V  → U1.VSYS               ||JP_BL||   R_BL1/2/3 → backlight
# Nothing else touches either rail — the encoders and Choc keys are switch-to-GND
# with internal pull-ups, so they draw from neither. Two 0R parts therefore isolate
# (a) the whole display, and (b) the whole backlight, from the module.
#
# Why this matters for a FIRST spin: the display is the one part that cannot come
# back off (it solders directly, and U1's front-side fillets live underneath it —
# PCB_ACTION_PLAN §1.2). If a display solder bridge pulls the module's LDO down,
# with the rails hard-wired you cannot tell the dead module from the dead display.
# Open JP_3V3 and the question answers itself.
#
# Both ship STUFFED with 0R — this is not a build option, it is a service seam.
# Decoupling stays on the SOURCE side of JP_3V3 on purpose: C_BULK/C_DEC belong to
# the LDO, so opening the link must not leave the regulator undecoupled. The display
# keeps its own C_LCD_VDD downstream, so the LCD side is decoupled either way.
jp_3v3 = link("JP_3V3", p3v3, p3v3_lcd)
jp_bl = link("JP_BL", vsys, vsys_bl)

# ----------------------------------------------------------------------------
# MCU — Waveshare RP2350-Tiny (PCB_DESIGN.md §0; supersedes the Pico 2)
# ----------------------------------------------------------------------------
pico = rp2350_tiny()        # keeps the name `pico` — every net below reads the same
pico["3V3"] += p3v3
pico["VSYS"] += vsys        # 5V tap for the backlight; USB power/data stay on the FPC
pico["GND"] += gnd          # connects ALL pads named GND at once (edge + back + SWD)
# GP16 stays unconnected on purpose: it drives the module's onboard WS2812.

# SWD breakout (module back pads are buried once soldered down — bring
# SWCLK/SWDIO/GND out to a header; route from the three solder-through holes)
swd = pin_header("J1", ["SWCLK", "GND", "SWDIO"], "SWD")
n_swclk = Net("SWCLK"); n_swclk += pico["SWCLK"], swd["SWCLK"]
swd["GND"] += gnd
n_swdio = Net("SWDIO"); n_swdio += pico["SWDIO"], swd["SWDIO"]

# ----------------------------------------------------------------------------
# Encoders ×4  (A/B: 10nF debounce, SW: 100nF debounce; internal pull-ups)
#   NO external pull-ups and NO DNP footprints for them — decided 2026-07-24, and the
#   line that used to sit here recommending them is withdrawn. Rationale + the ranked
#   fallbacks if fast spins ever miss counts: PCB_DESIGN.md §2.1 (authoritative).
#   The first fallback is swapping these 10nF A/B caps to 4.7nF, which needs no board
#   change at all — that is what makes shipping without the pull-ups safe.
# ----------------------------------------------------------------------------
ENCODERS = [
    ("ENC1", "GP0", "GP1", "GP2"),
    ("ENC2", "GP3", "GP4", "GP5"),
    ("ENC3", "GP14", "GP15", "GP23"),   # SW was GP16 on the Pico map; GP16 = Tiny's WS2812
    ("ENC4", "GP17", "GP18", "GP19"),
]
for i, (name, a_gp, b_gp, sw_gp) in enumerate(ENCODERS, start=1):
    enc = encoder_pec11l(ref=f"SW{i}")
    na = Net(f"{name}_A"); nb = Net(f"{name}_B"); nsw = Net(f"{name}_SW")
    na += pico[a_gp], enc["A"]
    nb += pico[b_gp], enc["B"]
    nsw += pico[sw_gp], enc["S1"]
    enc["C"] += gnd
    enc["S2"] += gnd
    enc["SH"] += gnd            # metal frame lugs → ground (ESD path + retention)
    ca = C("10nF", ref=f"C_{name}_A"); ca[1] += na; ca[2] += gnd
    cb = C("10nF", ref=f"C_{name}_B"); cb[1] += nb; cb[2] += gnd
    csw = C("100nF", ref=f"C_{name}_SW"); csw[1] += nsw; csw[2] += gnd

# ----------------------------------------------------------------------------
# Aux buttons ×5 (Choc, active-low, internal pull-up, no diodes)
#   BTN3/4/5 all come off the module's back-pad row (GP20/21/22) — same route class.
#   BTN5 claims GP22 (the ex-"spare" digital). J2 no longer exists at all.
# ----------------------------------------------------------------------------
BUTTONS = [("BTN1", "GP6"), ("BTN2", "GP7"), ("BTN3", "GP20"), ("BTN4", "GP21"), ("BTN5", "GP22")]
for i, (name, gp) in enumerate(BUTTONS, start=5):   # SW5..SW9 (encoders took SW1..SW4)
    btn = choc_switch(ref=f"SW{i}")
    nbtn = Net(name)                    # BTN1..BTN5 — named so the layout reads clearly
    nbtn += pico[gp], btn[1]
    btn[2] += gnd

# ----------------------------------------------------------------------------
# Display — EA DOGM128W-6 (ST7565R)
# ----------------------------------------------------------------------------
dog = dogm128()
# 4-wire SPI (write-only) + control — named so the layout shows LCD_SCL, not N$12
n_lcd_si  = Net("LCD_SI");  n_lcd_si  += pico["GP11"], dog["SI"]
n_lcd_scl = Net("LCD_SCL"); n_lcd_scl += pico["GP10"], dog["SCL"]
n_lcd_a0  = Net("LCD_A0");  n_lcd_a0  += pico["GP8"],  dog["A0"]
n_lcd_rst = Net("LCD_RST"); n_lcd_rst += pico["GP12"], dog["RST"]
n_lcd_cs  = Net("LCD_CS");  n_lcd_cs  += pico["GP9"],  dog["CS1B"]
# power — downstream of JP_3V3, so the whole display can be stranded (see Power rails)
dog["VDD"] += p3v3_lcd
dog["VDD2"] += p3v3_lcd
dog["VSS"] += gnd
# local VDD decoupling (100nF at the module) — LCD side of the link, stays with the load
c_vdd = C("100nF", ref="C_LCD_VDD"); c_vdd[1] += p3v3_lcd; c_vdd[2] += gnd

# --- ST7565 charge-pump / bias network: 9× 1µF, MUST sit at the module pads ---
# value standardized to 1µF/16V (VOUT node can reach ~13V). PCB_DESIGN §4.2.
def cp(ref):
    return C("1uF", ref=ref, footprint="Capacitor_SMD:C_0805_2012Metric")

# 3 flying caps (pump stages) — per EA datasheet p.4 "Single Supply 3.0/3.3V"
# schematic: CAP1P–CAP1N, CAP2P–CAP2N, and CAP3P–CAP1N (CAP3 shares CAP1N).
n_cap1n = Net("LCD_CAP1N")                       # shared by CP1 and CP3
n_cap1p = Net("LCD_CAP1P"); n_cap2p = Net("LCD_CAP2P")
n_cap2n = Net("LCD_CAP2N"); n_cap3p = Net("LCD_CAP3P")
c1 = cp("C_CP1"); n_cap1p += dog["CAP1P"], c1[1]; n_cap1n += dog["CAP1N"], c1[2]
c2 = cp("C_CP2"); n_cap2p += dog["CAP2P"], c2[1]; n_cap2n += dog["CAP2N"], c2[2]
c3 = cp("C_CP3"); n_cap3p += dog["CAP3P"], c3[1]; n_cap1n += c3[2]  # RESOLVED: CAP1N, not VOUT/VSS
# 1 reservoir cap on the boosted output
n_lcd_vout = Net("LCD_VOUT")
c_vout = cp("C_VOUT"); n_lcd_vout += dog["VOUT"], c_vout[1]; c_vout[2] += gnd
# 5 LCD-bias divider caps V0..V4 → GND
bias_nets = {}
for v in ("V0", "V1", "V2", "V3", "V4"):
    nv = Net(f"LCD_{v}")
    cv = cp(f"C_{v}"); nv += dog[v], cv[1]; cv[2] += gnd
    bias_nets[v] = nv        # V0 gets a probe pad — see "Bring-up / debug hooks"

# ----------------------------------------------------------------------------
# Backlight — EA LED55X46-A (amber, Vf≈1.9V), through the DOGM128 footprint
# (pads 1-3/18-20), on VSYS(5V, over the USB adapter FPC), low-side PWM via
# 2N7002 @ GP13.
#   3 independent LED paths → one series R per path (datasheet: "each path").
#   3× 300Ω → ~10 mA/path, ~31 mA total (matches PCB_DESIGN §3 budget);
#   ~32 mW per R → 0603 is fine (the old single 100Ω 1206 is gone).
#   For FIXED brightness instead: delete Q1/Rg/Rgs and tie C1-C3 straight to GND.
# ----------------------------------------------------------------------------
n_bl_k = Net("BL_CATHODE"); n_bl_g = Net("BL_PWM_GATE")
for i in (1, 2, 3):
    r = R("300", ref=f"R_BL{i}")
    r[1] += vsys_bl          # downstream of JP_BL — lets you kill/meter the backlight alone
    n_bl_a = Net(f"BL_A{i}"); n_bl_a += r[2], dog[f"A{i}"]
    dog[f"C{i}"] += n_bl_k
q_bl = nmos_2n7002(ref="Q1")
q_bl["D"] += n_bl_k
q_bl["S"] += gnd
q_bl["G"] += n_bl_g
r_g = R("100", ref="R_BL_G")
n_bl_pwm = Net("BL_PWM"); n_bl_pwm += pico["GP13"], r_g[1]                 # MCU side
r_g[2] += n_bl_g                                                          # gate series
r_gs = R("10k", ref="R_BL_GS"); r_gs[1] += n_bl_g; r_gs[2] += gnd          # gate pulldown

# ----------------------------------------------------------------------------
# Rail decoupling (near the module's 3V3 contacts)
# ----------------------------------------------------------------------------
c_bulk = C("10uF", ref="C_BULK", footprint="Capacitor_SMD:C_0805_2012Metric")
c_bulk[1] += p3v3; c_bulk[2] += gnd
c_dec = C("100nF", ref="C_DEC"); c_dec[1] += p3v3; c_dec[2] += gnd

# ============================================================================
# BRING-UP / DEBUG HOOKS  (added 2026-07-24, for the first PCB batch)
# ============================================================================
# Everything below exists to make the FIRST fabricated batch diagnosable and
# bodgeable. None of it carries signal in normal operation — it is all probe
# surface. Added while the layout was still unrouted, when it was free.
#
# All of it belongs on the BACK (B.Cu) — that is the service side: U1 and all 30
# passives are back-mounted and the front faces the panel. A test pad on the front
# is a test pad you cannot reach with the enclosure assembled.

# --- Probe pads -------------------------------------------------------------
# Ranked by what they actually buy you, most valuable first.
#
# TP_VOUT is the single highest-value pad on the board. The ST7565's on-glass
# charge pump is 9 caps with one non-obvious pairing (CAP3P→CAP1N, see §4.2), it
# is assembled by hand, and every way of getting it wrong presents identically as
# "blank screen". VOUT reads ~9-13V when the pump runs and ~VDD when it doesn't,
# which splits the entire display fault tree in one measurement. TP_V0 then splits
# the survivors again: pump good + bias wrong is a contrast problem, not a cap
# problem.
tp_vout = test_point("TP_VOUT", n_lcd_vout)     # charge-pump output: pump alive?
tp_v0 = test_point("TP_V0", bias_nets["V0"])    # top of the bias divider: contrast

# Rails. TP_VSYS/TP_3V3 sit on the SOURCE side of their links, TP_3V3L on the load
# side, so a meter across TP_3V3 → TP_3V3L reads the drop (and, with the link
# lifted, the display's whole current) without disturbing anything.
tp_vsys = test_point("TP_VSYS", vsys)           # 5V in, as delivered by the FPC
tp_3v3 = test_point("TP_3V3", p3v3)             # LDO output, module side
tp_3v3l = test_point("TP_3V3L", p3v3_lcd)       # display rail, load side of JP_3V3

# Grounds. THREE, not one: a probe return is only honest if it is next to the
# thing being probed, and these pads are >60mm apart on a 56x102mm board. Place
# one per cluster — module, display/charge-pump, backlight.
tp_gnd1 = test_point("TP_GND1", gnd)            # pair with TP_VSYS  (see injection note)
tp_gnd2 = test_point("TP_GND2", gnd)            # pair with TP_3V3
tp_gnd3 = test_point("TP_GND3", gnd)            # by the charge pump, for TP_VOUT/TP_V0

# Backlight path — gate drive in, switched cathode out. Confirms Q1 and the PWM
# separately from the LEDs: gate swinging but cathode stuck = dead FET.
tp_blpwm = test_point("TP_BLPWM", n_bl_pwm)     # GP13 → R_BL_G (MCU side)
tp_blk = test_point("TP_BLK", n_bl_k)           # Q1 drain / LED cathodes

# POWER INJECTION: these pads are the bench-supply hookup, no extra parts needed.
#   5V   → TP_VSYS + TP_GND1        3.3V → TP_3V3 + TP_GND2
# Place each pair ADJACENT so a two-lead clip fits. This matters more here than on
# most boards: power reaches this PCB *through* U1 (VSYS arrives over the adapter
# FPC and enters at the module's VSYS pad), so without these pads there is no way
# to energize the board at all until a module is already soldered on. With them you
# can current-limit a bench supply to ~50mA and smoke-test the populated back side
# BEFORE committing the module — and U1 cannot be removed once U2 is on.

# --- J_LCD: 1x6 logic-analyzer row on the display bus ------------------------
# Unpopulated (holes only); solder a header in when you need it.
# "The display is blank" has exactly two first-order causes and this row separates
# them in seconds: clip a logic analyzer on and see whether the MCU is transmitting
# at all. GND is deliberately ON the row (pin 6) so the analyzer's ground comes from
# the same connector instead of a flying lead.
#
# PLACEMENT: horizontally just above U2's pin row, over pads 36-40.
#   REVISED 2026-07-24 (was "next to the module's GP8-GP13 contacts, NOT at the display
#   end"). That instruction was written without checking U1's orientation on the board.
#   U1 is placed at rot=180, which puts GP8-GP13 spread along its BOTTOM edge (y~91,
#   x 144.6-158.6) in a jumbled order, while ALL 20 display pins are one row at
#   y=106.78 with SI/SCL/A0/RST/CS adjacent at x 167.5-177.6. So the display end is now
#   both the shorter route (~5mm stubs) AND the better tap: it proves the signal
#   ARRIVED, where an MCU-side tap only proves one was sent. Both arguments agree here.
#
# PIN ORDER follows U2's physical x-order (SI 167.46 → CS 177.62), NOT signal
# convention, so the six pins land as a straight comb with zero crossings under the
# glass. Free to do — the header is unpopulated and logic-analyzer channel order is
# arbitrary. If J_LCD is ever moved somewhere else, revisit this order.
#   GND sits at the LEFT END rather than after CS for a purely mechanical reason: with
#   GND last, the row has to start at U2.36's x and pin 6 lands 0.47mm from the board
#   edge. Leading with GND shifts the row one pitch left, so all five signal pins align
#   EXACTLY under their U2 pads (zero x-offset, 4.78mm straight stubs) and the far end
#   clears the edge. Ground at one end of the row suits a logic analyzer fine.
j_lcd = pin_header("J_LCD", ["GND", "SI", "SCL", "A0", "RST", "CS"], "LCD_DEBUG")
j_lcd["SCL"] += n_lcd_scl
j_lcd["SI"] += n_lcd_si
j_lcd["CS"] += n_lcd_cs
j_lcd["A0"] += n_lcd_a0
j_lcd["RST"] += n_lcd_rst
j_lcd["GND"] += gnd

# --- J_ADC: 1x6 spare-pin / bodge row ---------------------------------------
# SUPERSEDES the "Spare ADC pins — DELIBERATELY UNROUTED" decision that stood here
# until 2026-07-24. That call was made for the SHIPPING board and its reasoning was
# specifically "an internal header costs 6 traces on a crowded 2-layer board" — a
# routing objection, not an objection to the pins being available. It is answered by
# constraining the placement rather than by dropping the row:
#
#   PLACEMENT: hard against the module's GP26-GP29 edge contacts (they are adjacent
#   on the edge ring, left side, between 3V3 and GP15). Stubs of a few mm each. If
#   this row cannot be placed there, drop it rather than dragging six traces across
#   the board — the original objection stands in that case.
#
# Unpopulated: holes only, no header shipped, no BOM cost. What it buys on a first
# batch is somewhere to LAND A WIRE — the thing you most often need and the thing a
# fabricated board cannot give you after the fact. Also keeps the fader/pot option
# open (GP26-28 are ADC-capable; GP27/28/29 have 47R series parts on-module).
j_adc = pin_header("J_ADC", ["GP26", "GP27", "GP28", "GP29", "3V3", "GND"], "SPARE_IO")
for gp in ("GP26", "GP27", "GP28", "GP29"):
    n_spare = Net(f"SPARE_{gp}")
    n_spare += pico[gp], j_adc[gp]
j_adc["3V3"] += p3v3
j_adc["GND"] += gnd

# --- NOT retrofittable: board-level RESET ------------------------------------
# The carrier footprint brings out 36 pads and RUN is not among them (23 edge
# contacts + GND/GP16-23/3V3/SWDIO/SWCLK on the back), so no reset button can be
# added here — RESET and BOOTSEL live on the panel-mounted USB-C adapter and reach
# the module only through the FPC. That is by design and the buttons are panel-
# accessible, but it does mean a bad FPC seat costs you both.
# >>> BEFORE ORDERING: diff the footprint against Waveshare's published pinout. If
# >>> the real module exposes RUN and the footprint simply omitted it, that is a
# >>> footprint bug worth catching now, and a board-level reset is worth having.

# ----------------------------------------------------------------------------
# Emit
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    # ERC()  # optional; expects power-flag conventions — enable once footprints set
    generate_netlist(file_="naub_v2.net")
    print("wrote naub_v2.net")
