# Wiring & Pinout Specification

**Device:** USB Application/System Audio Mixer
**Controller:** Raspberry Pi Pico 2 (RP2350)
**Date:** 2026-06-09

This document tells you exactly which Pico pin every wire goes to. All pin
assignments are chosen so that **Waveshare's official Pico e-Paper example code
and the GxEPD2 library work with no pin changes**, and so that each encoder's
three wires land on adjacent pins for tidy routing.

> **Convention:** `GPx` = the RP2350 GPIO number (used in firmware).
> `pin N` = the physical pin number on the 40-pin Pico header (count these on
> the board). Pin 1 is the corner nearest the USB connector, on the same edge
> as the `GP0` silkscreen.

---

## 1. Master pin map (read this first)

| GPIO | Phys pin | Connected to | Signal |
|------|---------|--------------|--------|
| GP0  | 1  | Encoder 1 — A    | quadrature A (pull-up) |
| GP1  | 2  | Encoder 1 — B    | quadrature B (pull-up) |
| GP2  | 4  | Encoder 1 — SW   | push switch (pull-up) |
| GP3  | 5  | Encoder 2 — A    | quadrature A (pull-up) |
| GP4  | 6  | Encoder 2 — B    | quadrature B (pull-up) |
| GP5  | 7  | Encoder 2 — SW   | push switch (pull-up) |
| GP6  | 9  | Button 1         | momentary (pull-up) |
| GP7  | 10 | Button 2         | momentary (pull-up) |
| GP8  | 11 | Display **DC**   | data/command |
| GP9  | 12 | Display **CS**   | chip select (active low) |
| GP10 | 14 | Display **CLK**  | SPI1 SCK |
| GP11 | 15 | Display **DIN**  | SPI1 TX (MOSI) |
| GP12 | 16 | Display **RST**  | reset (active low) |
| GP13 | 17 | Display **BUSY** | busy (input) |
| GP14 | 19 | Encoder 3 — A    | quadrature A (pull-up) |
| GP15 | 20 | Encoder 3 — B    | quadrature B (pull-up) |
| GP16 | 21 | Encoder 3 — SW   | push switch (pull-up) |
| GP17 | 22 | Encoder 4 — A    | quadrature A (pull-up) |
| GP18 | 24 | Encoder 4 — B    | quadrature B (pull-up) |
| GP19 | 25 | Encoder 4 — SW   | push switch (pull-up) |

**Used:** 20 GPIO. **Spare:** GP20, GP21, GP22, GP26, GP27, GP28 (6 free).

**Power/ground pins used:**

| Pico pin | Phys pin | Use |
|----------|---------|-----|
| `3V3(OUT)` | 36 | Display VCC (+3.3 V) |
| `VBUS`     | 40 | (from USB — see §5) |
| `GND`      | 3, 8, 13, 18, 23, 28, 33, 38 | Common ground (any of these) |

---

## 2. Display — Waveshare 2.9" e-Paper Module (V3)

> **Version note:** This is the **V3** panel (label on the back). Waveshare
> states V3 is **hardware- and interface-compatible** with V1/V2, so the wiring
> below is unchanged. The version only affects the **firmware library class**
> (see note at end of this section).

Official pin names are printed on the module's header. Wire them as follows
(this is Waveshare's standard Pico mapping):

| Module pin | Function | → Pico | Phys pin |
|-----------|----------|--------|---------|
| **VCC**  | Power 3.3 V | `3V3(OUT)` | 36 |
| **GND**  | Ground | `GND` | 38 (or any GND) |
| **DIN**  | SPI data in (MOSI) | `GP11` (SPI1 TX) | 15 |
| **CLK**  | SPI clock | `GP10` (SPI1 SCK) | 14 |
| **CS**   | Chip select (active low) | `GP9` | 12 |
| **DC**   | Data/command | `GP8` | 11 |
| **RST**  | Reset (active low) | `GP12` | 16 |
| **BUSY** | Busy status (output → Pico input) | `GP13` | 17 |

Notes:
- The display is **write-only** over SPI — there is **no MISO** wire.
- VCC may be 3.3 V or 5 V; we use **3.3 V** for clean logic levels.
- These exact pins match the GxEPD2 Pico example, so the reference firmware
  runs without editing the pin defines.
- **Firmware class (confirm at firmware time):** the current 296×128 B/W panel
  is SSD1680-based and uses GxEPD2 class `GxEPD2_290_T94` (or the newer
  `GxEPD2_290_GDEY029T94`). Verify the exact class against the **V3** sticker /
  Waveshare wiki before flashing — wiring is unaffected either way.

---

## 3. Rotary encoders (×4) — Bourns PEC11R-4220F-S0024

Each encoder has **5 terminals**: three on the encoder side (A, C, B) and two
on the switch side (SW pins). Looking at the part with the shaft up:

```
        ┌─────────────┐
        │   ENCODER   │
   A ───┤ o    C    o ├─── B      <- encoder channels (C = common)
        │      o      │
        │             │
  SW ───┤ o         o ├─── SW     <- push switch (not polarized)
        └─────────────┘
```

**Wire each encoder identically (example shown for Encoder 1):**

| Encoder terminal | → Pico | Debounce cap | Notes |
|------------------|--------|--------------|-------|
| **A**  | GP0 | 10 nF from GP0 → GND | internal pull-up enabled |
| **C** (common) | `GND` | — | shared ground |
| **B**  | GP1 | 10 nF from GP1 → GND | internal pull-up enabled |
| **SW** (pin 1) | GP2 | 100 nF from GP2 → GND | internal pull-up enabled |
| **SW** (pin 2) | `GND` | — | switch other side to ground |

**Per-encoder GPIO assignments:**

| Encoder | A | B | SW |
|---------|-----|-----|-----|
| 1 | GP0  | GP1  | GP2  |
| 2 | GP3  | GP4  | GP5  |
| 3 | GP14 | GP15 | GP16 |
| 4 | GP17 | GP18 | GP19 |

**How it works:** A and B idle HIGH (internal pull-up). Turning the shaft pulls
A/B to GND through the common pin in a quadrature pattern; firmware reads
direction from the phase. Pressing the shaft connects SW to GND. The caps form
an RC low-pass with the ~50 kΩ internal pull-up to swallow contact bounce
(10 nF on the fast A/B lines, 100 nF on the slower switch line).

---

## 4. Aux buttons (×2)

Simple momentary buttons, active-low:

| Button | → Pico | Other leg | Notes |
|--------|--------|-----------|-------|
| Button 1 | GP6 | `GND` | internal pull-up; press = LOW |
| Button 2 | GP7 | `GND` | internal pull-up; press = LOW |

Optional: add a 100 nF cap from each GPIO → GND for debounce (same idea as the
encoder switch). Firmware debounce alone is usually enough for these.

---

## 5. USB-C input — Adafruit 4090 → Pico

The 4090 carries the real USB connection into the device. The CC resistors are
already on the 4090, so no extra parts are needed. Connect the 4090's pads to
the **micro-USB pigtail**, which plugs into the Pico's micro-USB jack:

| Adafruit 4090 pad | Pigtail wire | Carries |
|-------------------|--------------|---------|
| **VBUS** | red    | +5 V |
| **GND**  | black  | ground |
| **D+**   | green  | USB data + |
| **D-**   | white  | USB data − |
| **CC**   | (leave) | handled by onboard 5.1 kΩ resistors |

> The Pico is powered **through its own USB jack** via this pigtail — you do
> **not** also wire 4090 VBUS to the Pico's `VBUS` pin (pin 40). Pick one power
> entry, not both.
>
> **Alternative (not used):** solder 4090 D+→Pico **TP2**, D-→**TP3**,
> VBUS→pin 40, GND→GND, and skip the pigtail. The pigtail route avoids
> soldering to the Pico's tiny test pads.

---

## 6. Ground strategy

All grounds are common. For a clean hand-wired build, run a single **ground
bus** (a length of wire, or a ground rail on perfboard) and tie to it:

- every encoder **C** (common) pin
- every encoder **SW pin 2**
- both button "other leg"s
- display **GND**
- all debounce-cap ground ends
- one Pico `GND` pin

---

## 7. Quick assembly checklist

- [ ] Display: 8 wires (VCC, GND, DIN, CLK, CS, DC, RST, BUSY) per §2
- [ ] Encoder 1 → GP0/GP1/GP2 (+ 2×10 nF, 1×100 nF)
- [ ] Encoder 2 → GP3/GP4/GP5 (+ caps)
- [ ] Encoder 3 → GP14/GP15/GP16 (+ caps)
- [ ] Encoder 4 → GP17/GP18/GP19 (+ caps)
- [ ] Button 1 → GP6, Button 2 → GP7
- [ ] USB-C: 4090 → pigtail → Pico micro-USB jack (4 wires)
- [ ] Common ground bus tied together
- [ ] Display VCC on `3V3(OUT)` (pin 36), **not** 5 V

---

## Sources

- Raspberry Pi Pico 2 / RP2350 pinout & datasheet — <https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html>
- Waveshare 2.9" e-Paper Module manual — <https://www.waveshare.com/wiki/2.9inch_e-Paper_Module_Manual>
- Waveshare Pico-ePaper-2.9 (official Pico pin mapping) — <https://www.waveshare.com/wiki/Pico-ePaper-2.9>
- Adafruit 4090 USB-C breakout (CC resistor detail) — <https://www.adafruit.com/product/4090>
- Bourns PEC11R datasheet — <https://www.bourns.com/docs/Product-Datasheets/PEC11R.pdf>
