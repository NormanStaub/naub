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
      R/C/FET  : Resistor_SMD / Capacitor_SMD / Package_TO_SOT_SMD (as before)

RP2350-TINY DELTAS vs the old Pico 2 netlist (PCB_DESIGN.md §0)
    - ENC3 switch: GP16 → GP23 (GP16 = onboard WS2812)
    - Backlight rail: Pico VBUS pin → module VSYS edge contact (USB 5V arrives
      over the adapter FPC; same ~5V, 3x 300R unchanged)
    - SWD header J1 now taps the module's SWCLK/GND/SWDIO solder-through pads
    - Expansion header J2 gains GP29 (extra ADC — was internal on the Pico)

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
def R(value, ref=None, footprint="Resistor_SMD:R_0603_1608Metric"):
    return Part(name="R", ref_prefix="R", value=value, ref=ref, tool=SKIDL,
                footprint=footprint,
                pins=[Pin(num=1, name="~", func=PASSIVE),
                      Pin(num=2, name="~", func=PASSIVE)])


def C(value, ref=None, footprint="Capacitor_SMD:C_0603_1608Metric"):
    return Part(name="C", ref_prefix="C", value=value, ref=ref, tool=SKIDL,
                footprint=footprint,
                pins=[Pin(num=1, name="~", func=PASSIVE),
                      Pin(num=2, name="~", func=PASSIVE)])


def nmos_2n7002(ref=None):
    # SOT-23: pad1=Gate, pad2=Source, pad3=Drain
    return Part(name="2N7002", ref_prefix="Q", value="2N7002", ref=ref, tool=SKIDL,
                footprint="Package_TO_SOT_SMD:SOT-23",
                pins=[Pin(num=1, name="G", func=INPUT),
                      Pin(num=2, name="S", func=PASSIVE),
                      Pin(num=3, name="D", func=PASSIVE)])


def encoder_pec11l(ref):
    # Bourns PEC11L (11mm LOW PROFILE): A / C(common) / B + switch S1 / S2.
    # PEC11L-4215F-S0015 = detent code 2 (30 detents), 15 PPR, 15mm flatted shaft,
    # push switch. Chosen for the finest detent in the series (12 deg/click).
    # 30 detents against 15 pulses is TWO detents per quadrature cycle, so the
    # firmware sets rotaryio divisor=2 to get one count per click -- see
    # firmware/code.py. Swapping to detent code 1 (-4115F-S0020, 20/20) would make
    # that line unnecessary; codes 0/3 change the feel entirely.
    # Other shaft lengths: -4220F-/-4225F- (20/25mm); 15mm is the shortest offered.
    # Footprint pads are lettered to match these pin numbers — no reconcile needed.
    # Detent code and resolution do NOT change the pinout, body or footprint.
    return Part(name="PEC11L", ref_prefix="SW", value="PEC11L-4215F-S0015", ref=ref, tool=SKIDL,
                footprint="naub:RotaryEncoder_Bourns_PEC11L-4xxxF-Sxxxx_Vertical",
                pins=[Pin(num="A", name="A", func=PASSIVE),
                      Pin(num="C", name="C", func=PASSIVE),
                      Pin(num="B", name="B", func=PASSIVE),
                      Pin(num="S1", name="S1", func=PASSIVE),
                      Pin(num="S2", name="S2", func=PASSIVE)])


def choc_switch(ref):
    return Part(name="Choc_V1", ref_prefix="SW", value="Kailh_Choc_Red", ref=ref, tool=SKIDL,
                footprint="naub:SW_Kailh_Choc_V1",
                pins=[Pin(num=1, name="1", func=PASSIVE),
                      Pin(num=2, name="2", func=PASSIVE)])


def pin_header(ref, names, value):
    fp = f"Connector_PinHeader_2.54mm:PinHeader_1x{len(names):02d}_P2.54mm_Vertical"
    return Part(name="Conn", ref_prefix="J", value=value, ref=ref, tool=SKIDL,
                footprint=fp,
                pins=[Pin(num=i + 1, name=n, func=PASSIVE) for i, n in enumerate(names)])


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
    return Part(name="RP2350_Tiny", ref_prefix="U", ref=ref, value="Waveshare_RP2350-Tiny", tool=SKIDL,
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
    return Part(name="DOGM128W-6", ref_prefix="U", ref=ref, value="EA_DOGM128W-6", tool=SKIDL,
                footprint="Display:EA_DOGM128-6",
                pins=[Pin(num=n, name=nm, func=PASSIVE) for n, nm in pins])


# ----------------------------------------------------------------------------
# Power rails
# ----------------------------------------------------------------------------
gnd = Net("GND")
p3v3 = Net("+3V3")          # module 3V3 (ME6217 LDO, 800mA) → logic + display
vsys = Net("VSYS_5V")       # USB 5V, arrives over the adapter FPC → backlight ONLY

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
swd["SWCLK"] += pico["SWCLK"]
swd["GND"] += gnd
swd["SWDIO"] += pico["SWDIO"]

# ----------------------------------------------------------------------------
# Encoders ×4  (A/B: 10nF debounce, SW: 100nF debounce; internal pull-ups)
#   Optional fast-spin insurance: add 10k DNP pull-ups A/B→+3V3 in layout (PCB_DESIGN §2)
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
    ca = C("10nF", ref=f"C_{name}_A"); ca[1] += na; ca[2] += gnd
    cb = C("10nF", ref=f"C_{name}_B"); cb[1] += nb; cb[2] += gnd
    csw = C("100nF", ref=f"C_{name}_SW"); csw[1] += nsw; csw[2] += gnd

# ----------------------------------------------------------------------------
# Aux buttons ×4 (Choc, active-low, internal pull-up, no diodes)
# ----------------------------------------------------------------------------
BUTTONS = [("BTN1", "GP6"), ("BTN2", "GP7"), ("BTN3", "GP20"), ("BTN4", "GP21")]
for i, (name, gp) in enumerate(BUTTONS, start=5):   # SW5..SW8 (encoders took SW1..SW4)
    btn = choc_switch(ref=f"SW{i}")
    btn[1] += pico[gp]
    btn[2] += gnd

# ----------------------------------------------------------------------------
# Display — EA DOGM128W-6 (ST7565R)
# ----------------------------------------------------------------------------
dog = dogm128()
# 4-wire SPI (write-only) + control
dog["SI"] += pico["GP11"]
dog["SCL"] += pico["GP10"]
dog["A0"] += pico["GP8"]
dog["RST"] += pico["GP12"]
dog["CS1B"] += pico["GP9"]
# power
dog["VDD"] += p3v3
dog["VDD2"] += p3v3
dog["VSS"] += gnd
# local VDD decoupling (100nF at the module)
c_vdd = C("100nF", ref="C_LCD_VDD"); c_vdd[1] += p3v3; c_vdd[2] += gnd

# --- ST7565 charge-pump / bias network: 9× 1µF, MUST sit at the module pads ---
# value standardized to 1µF/16V (VOUT node can reach ~13V). PCB_DESIGN §4.2.
def cp(ref):
    return C("1uF", ref=ref, footprint="Capacitor_SMD:C_0805_2012Metric")

# 3 flying caps (pump stages) — per EA datasheet p.4 "Single Supply 3.0/3.3V"
# schematic: CAP1P–CAP1N, CAP2P–CAP2N, and CAP3P–CAP1N (CAP3 shares CAP1N).
c1 = cp("C_CP1"); c1[1] += dog["CAP1P"]; c1[2] += dog["CAP1N"]
c2 = cp("C_CP2"); c2[1] += dog["CAP2P"]; c2[2] += dog["CAP2N"]
c3 = cp("C_CP3"); c3[1] += dog["CAP3P"]; c3[2] += dog["CAP1N"]  # RESOLVED: CAP1N, not VOUT/VSS
# 1 reservoir cap on the boosted output
c_vout = cp("C_VOUT"); c_vout[1] += dog["VOUT"]; c_vout[2] += gnd
# 5 LCD-bias divider caps V0..V4 → GND
for v in ("V0", "V1", "V2", "V3", "V4"):
    cv = cp(f"C_{v}"); cv[1] += dog[v]; cv[2] += gnd

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
    r[1] += vsys
    r[2] += dog[f"A{i}"]
    dog[f"C{i}"] += n_bl_k
q_bl = nmos_2n7002(ref="Q1")
q_bl["D"] += n_bl_k
q_bl["S"] += gnd
q_bl["G"] += n_bl_g
r_g = R("100", ref="R_BL_G"); r_g[1] += pico["GP13"]; r_g[2] += n_bl_g     # gate series
r_gs = R("10k", ref="R_BL_GS"); r_gs[1] += n_bl_g; r_gs[2] += gnd          # gate pulldown

# ----------------------------------------------------------------------------
# Rail decoupling (near the module's 3V3 contacts)
# ----------------------------------------------------------------------------
c_bulk = C("10uF", ref="C_BULK", footprint="Capacitor_SMD:C_0805_2012Metric")
c_bulk[1] += p3v3; c_bulk[2] += gnd
c_dec = C("100nF", ref="C_DEC"); c_dec[1] += p3v3; c_dec[2] += gnd

# ----------------------------------------------------------------------------
# Optional expansion header (PCB_NOTES open item): spare GP22 + ADC GP26-29
#   GP26-28 reserved for a future fader/pot; GP29 = bonus ADC the Pico kept
#   internal (47R series on-module on GP27/28/29 — harmless for GPIO/ADC).
#   Comment out if you don't want it.
# ----------------------------------------------------------------------------
spare = pin_header("J2", ["+3V3", "GP22", "GP26", "GP27", "GP28", "GP29", "GND"], "EXPANSION")
spare["+3V3"] += p3v3
spare["GP22"] += pico["GP22"]
spare["GP26"] += pico["GP26"]
spare["GP27"] += pico["GP27"]
spare["GP28"] += pico["GP28"]
spare["GP29"] += pico["GP29"]
spare["GND"] += gnd

# ----------------------------------------------------------------------------
# Emit
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    # ERC()  # optional; expects power-flag conventions — enable once footprints set
    generate_netlist(file_="naub_v2.net")
    print("wrote naub_v2.net")
