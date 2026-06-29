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
    Pcbnew → Tools → "Update PCB from Netlist" (point it at naub_v2.net), or
    use the netlist importer. Assign/confirm footprints first (see below).

PIN NUMBERS — READ THIS
    Standard parts (Pico, R, C, 2N7002) use REAL pad numbers → import-ready.
    Custom modules (display, encoder, Choc, backlight, headers) use pin NAMES
    as identifiers for readability. Before importing to the PCB, reconcile
    those pad NUMBERS against whatever KiCad footprint you actually place
    (e.g. the SnapEDA/Digi-Key DOGM128 symbol). The CONNECTIONS are the
    durable output; pad-number mapping is a 5-minute reconcile per footprint.

FOOTPRINTS
    Confirmed-standard : R_*, C_* (Resistor_SMD/Capacitor_SMD), SOT-23.
    Placeholder (swap) : Pico, DOGM128, EC11E encoder, Choc, LED55X46-A,
                         headers — marked "FIXME-FP" in the footprint field.

OPEN ITEM CARRIED IN HERE
    The ST7565 charge-pump CAP3 far-end (VOUT vs VSS) is still unconfirmed
    against the EA app schematic — see the ⚠ marker in the display section
    and PCB_DESIGN.md §4.2.
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

FIXME = "FIXME-FP:replace_with_real_footprint"


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


def encoder_ec11(ref):
    # Alps EC11E: A / C(common) / B + switch S1 / S2. Pin nums = names (reconcile to FP).
    return Part(name="EC11E", ref_prefix="SW", value="EC11E1834403", ref=ref, tool=SKIDL,
                footprint=FIXME + ":RotaryEncoder_Alps_EC11E-Switch",
                pins=[Pin(num="A", name="A", func=PASSIVE),
                      Pin(num="C", name="C", func=PASSIVE),
                      Pin(num="B", name="B", func=PASSIVE),
                      Pin(num="S1", name="S1", func=PASSIVE),
                      Pin(num="S2", name="S2", func=PASSIVE)])


def choc_switch(ref):
    return Part(name="Choc_V1", ref_prefix="SW", value="Kailh_Choc_Red", ref=ref, tool=SKIDL,
                footprint=FIXME + ":SW_Kailh_Choc_V1",
                pins=[Pin(num=1, name="1", func=PASSIVE),
                      Pin(num=2, name="2", func=PASSIVE)])


def pin_header(ref, names, value):
    return Part(name="Conn", ref_prefix="J", value=value, ref=ref, tool=SKIDL,
                footprint=FIXME + ":PinHeader_2.54mm",
                pins=[Pin(num=i + 1, name=n, func=PASSIVE) for i, n in enumerate(names)])


def pico2(ref="U1"):
    """Raspberry Pi Pico 2 (RP2350), 40-pin + 3-pin SWD. Real pad numbers."""
    pins = [
        (1, "GP0"), (2, "GP1"), (3, "GND"), (4, "GP2"), (5, "GP3"),
        (6, "GP4"), (7, "GP5"), (8, "GND"), (9, "GP6"), (10, "GP7"),
        (11, "GP8"), (12, "GP9"), (13, "GND"), (14, "GP10"), (15, "GP11"),
        (16, "GP12"), (17, "GP13"), (18, "GND"), (19, "GP14"), (20, "GP15"),
        (21, "GP16"), (22, "GP17"), (23, "GND"), (24, "GP18"), (25, "GP19"),
        (26, "GP20"), (27, "GP21"), (28, "GND"), (29, "GP22"), (30, "RUN"),
        (31, "GP26"), (32, "GP27"), (33, "GND"), (34, "GP28"), (35, "ADC_VREF"),
        (36, "3V3_OUT"), (37, "3V3_EN"), (38, "GND"), (39, "VSYS"), (40, "VBUS"),
        # 3-pin debug header on the Pico
        (41, "SWCLK"), (42, "GND"), (43, "SWDIO"),
    ]
    def fn(name):
        if name in ("3V3_OUT", "VBUS"):
            return PWROUT          # we source 3V3 / 5V FROM these
        if name in ("GND",):
            return PWRIN
        if name in ("VSYS", "3V3_EN", "ADC_VREF", "RUN"):
            return PASSIVE
        return BIDIR               # GPxx + SWD
    return Part(name="RPi_Pico2", ref_prefix="U", ref=ref, value="Pico2_RP2350", tool=SKIDL,
                footprint=FIXME + ":RPi_Pico_SMD_TH",
                pins=[Pin(num=n, name=nm, func=fn(nm)) for n, nm in pins])


def dogm128(ref="U2"):
    """EA DOGM128W-6 (ST7565R on glass). Pin nums = names (reconcile to FP)."""
    names = ["SI", "SCL", "A0", "RST", "CS1B",          # SPI interface
             "VDD", "VDD2", "VSS",                        # power / ground
             "V0", "V1", "V2", "V3", "V4", "VOUT",        # LCD bias + booster out
             "CAP1P", "CAP1N", "CAP2P", "CAP2N", "CAP3P"] # charge-pump flying caps
    return Part(name="DOGM128W-6", ref_prefix="U", ref=ref, value="EA_DOGM128W-6", tool=SKIDL,
                footprint=FIXME + ":EA_DOGM128",
                pins=[Pin(num=nm, name=nm, func=PASSIVE) for nm in names])


def backlight_led(ref="DS1"):
    return Part(name="LED55X46-A", ref_prefix="DS", ref=ref, value="EA_LED55X46-A", tool=SKIDL,
                footprint=FIXME + ":EA_LED55X46",
                pins=[Pin(num="A", name="A", func=PASSIVE),
                      Pin(num="K", name="K", func=PASSIVE)])


# ----------------------------------------------------------------------------
# Power rails
# ----------------------------------------------------------------------------
gnd = Net("GND")
p3v3 = Net("+3V3")          # Pico 3V3(OUT) → logic + display
vbus = Net("VBUS_5V")       # tapped from Pico VBUS pin → backlight ONLY (not power-in)

# ----------------------------------------------------------------------------
# MCU
# ----------------------------------------------------------------------------
pico = pico2()
pico["3V3_OUT"] += p3v3
pico["VBUS"] += vbus        # source 5V for backlight; power still ENTERS via the Pico USB jack
pico["GND"] += gnd          # connects ALL pins named GND at once

# SWD breakout (flush Pico buries its pads — bring SWCLK/SWDIO/GND to a header)
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
    ("ENC3", "GP14", "GP15", "GP16"),
    ("ENC4", "GP17", "GP18", "GP19"),
]
for i, (name, a_gp, b_gp, sw_gp) in enumerate(ENCODERS, start=1):
    enc = encoder_ec11(ref=f"SW{i}")
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

# 3 flying caps (pump stages)
c1 = cp("C_CP1"); c1[1] += dog["CAP1P"]; c1[2] += dog["CAP1N"]
c2 = cp("C_CP2"); c2[1] += dog["CAP2P"]; c2[2] += dog["CAP2N"]
# ⚠ UNCONFIRMED far-end: CAP3 ties to VOUT or VSS depending on booster ratio.
#    Verify against the EA DOGM128-6 "Application 3.3V" schematic before fab.
c3 = cp("C_CP3"); c3[1] += dog["CAP3P"]; c3[2] += dog["VOUT"]   # ⚠ VOUT vs GND — verify
# 1 reservoir cap on the boosted output
c_vout = cp("C_VOUT"); c_vout[1] += dog["VOUT"]; c_vout[2] += gnd
# 5 LCD-bias divider caps V0..V4 → GND
for v in ("V0", "V1", "V2", "V3", "V4"):
    cv = cp(f"C_{v}"); cv[1] += dog[v]; cv[2] += gnd

# ----------------------------------------------------------------------------
# Backlight — EA LED55X46-A, on VBUS(5V), ~100Ω, low-side PWM via 2N7002 @ GP13
#   For FIXED brightness instead: delete Q1/Rg/Rgs and tie DS1.K straight to GND.
# ----------------------------------------------------------------------------
bl = backlight_led()
r_bl = R("100", ref="R_BL", footprint="Resistor_SMD:R_1206_3216Metric")  # ~0.1W → 1206
n_bl_a = Net("BL_ANODE"); n_bl_k = Net("BL_CATHODE"); n_bl_g = Net("BL_PWM_GATE")
r_bl[1] += vbus
r_bl[2] += n_bl_a
bl["A"] += n_bl_a
bl["K"] += n_bl_k
q_bl = nmos_2n7002(ref="Q1")
q_bl["D"] += n_bl_k
q_bl["S"] += gnd
q_bl["G"] += n_bl_g
r_g = R("100", ref="R_BL_G"); r_g[1] += pico["GP13"]; r_g[2] += n_bl_g     # gate series
r_gs = R("10k", ref="R_BL_GS"); r_gs[1] += n_bl_g; r_gs[2] += gnd          # gate pulldown

# ----------------------------------------------------------------------------
# Rail decoupling (near Pico 3V3OUT)
# ----------------------------------------------------------------------------
c_bulk = C("10uF", ref="C_BULK", footprint="Capacitor_SMD:C_0805_2012Metric")
c_bulk[1] += p3v3; c_bulk[2] += gnd
c_dec = C("100nF", ref="C_DEC"); c_dec[1] += p3v3; c_dec[2] += gnd

# ----------------------------------------------------------------------------
# Optional expansion header (PCB_NOTES open item): spare GP22 + ADC GP26-28
#   GP26-28 reserved for a future fader/pot. Comment out if you don't want it.
# ----------------------------------------------------------------------------
spare = pin_header("J2", ["+3V3", "GP22", "GP26", "GP27", "GP28", "GND"], "EXPANSION")
spare["+3V3"] += p3v3
spare["GP22"] += pico["GP22"]
spare["GP26"] += pico["GP26"]
spare["GP27"] += pico["GP27"]
spare["GP28"] += pico["GP28"]
spare["GND"] += gnd

# ----------------------------------------------------------------------------
# Emit
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    # ERC()  # optional; expects power-flag conventions — enable once footprints set
    generate_netlist(file_="naub_v2.net")
    print("wrote naub_v2.net")
