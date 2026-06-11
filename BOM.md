# Bill of Materials — USB Application/System Audio Mixer

**Project:** DIY USB hardware audio mixer (per-app + system volume)
**Controller:** Raspberry Pi Pico 2 (RP2350)
**Interface:** USB composite device — HID Consumer Control (system volume, driverless) + CDC serial (per-app, host helper)
**Revision:** V1 prototype (hand-wired)
**Date:** 2026-06-09

---

## Core

| # | Component | Part / Model | Qty | Key spec | Purpose |
|---|-----------|--------------|-----|----------|---------|
| 1 | Microcontroller (prototype) | Raspberry Pi Pico 2 **H** (RP2350, with headers) | 1 | Native USB-FS, PIO ×12 SM, 26 GPIO | Breadboard/prototype build |
| 2 | Microcontroller (final) | Raspberry Pi Pico 2 (RP2350, no headers) | 1 | Same | Soldered final build |

## Physical interface

| # | Component | Part / Model | Qty | Key spec | Purpose |
|---|-----------|--------------|-----|----------|---------|
| 3 | Rotary encoder | Bourns **PEC11R-4220F-S0024** | 5 (4 used + spare) | 24 detents, 24 PPR, integrated SPST push switch, metal D-shaft, panel mount | Volume knobs + click-to-mute/select |
| 4 | Knobs | 3D-printed (PETG) | 4 | M3 heat-set inserts + grub screw, optional steel weights | Fit on encoder D-shaft |
| 5 | Aux push button | (TBD — not yet sourced) | 2 | Momentary, NO | Auxiliary functions |

## Display

| # | Component | Part / Model | Qty | Key spec | Purpose |
|---|-----------|--------------|-----|----------|---------|
| 6 | E-paper display | Waveshare **2.9" e-Paper Module (V3)** | 1 | 296×128, SSD1680, B/W, SPI, 3.3/5V, partial refresh; V3 is interface-compatible with V1/V2 | Show current volume level |

## USB / power

| # | Component | Part / Model | Qty | Key spec | Purpose |
|---|-----------|--------------|-----|----------|---------|
| 7 | USB-C breakout | Adafruit **4090** | 2 (1 used + spare) | USB-C device connector, onboard 5.1kΩ CC pull-downs | Fixed USB-C port for enclosure |
| 8 | USB pigtail | ELNONE micro-USB 5-wire pigtail | 1 | Micro-USB plug → flying leads | Connects 4090 → Pico micro-USB jack |

## Passives (already on hand)

| # | Component | Value | Qty needed | Purpose |
|---|-----------|-------|------------|---------|
| 9 | Ceramic capacitor | 10 nF | 8 (2 per encoder × 4) | RC debounce on encoder A/B channels |
| 10 | Ceramic capacitor | 100 nF | 4 (1 per encoder × 4) | RC debounce on encoder push switch |

> **Pull-ups:** none needed as discrete parts — using the RP2350 **internal pull-ups** (~50 kΩ) on all encoder A/B/SW and button lines (active-low wiring).

---

## Notes / open items

- **Aux buttons (item 5)** were added after the initial spec and have **no chosen part yet** — confirm before final order if you want them in V1.
- **Cap count:** verify you have at least **8× 10 nF** and **4× 100 nF** on hand.
- **USB path:** assumes the **pigtail** route (4090 → Pico's micro-USB jack). Alternative is soldering 4090 D+/D− to the Pico's TP2/TP3 test pads — not used here.
- **Display voltage:** wired to **3.3 V** (Pico `3V3(OUT)`), not 5 V — cleaner and logic-level matched. The module supports both.

## Sources

- Raspberry Pi Pico 2 datasheet & pinout — <https://www.raspberrypi.com/documentation/microcontrollers/pico-series.html>
- Waveshare 2.9" e-Paper Module wiki — <https://www.waveshare.com/wiki/2.9inch_e-Paper_Module_Manual>
- Waveshare Pico-ePaper-2.9 (official Pico mapping) — <https://www.waveshare.com/wiki/Pico-ePaper-2.9>
- Adafruit 4090 USB-C breakout — <https://www.adafruit.com/product/4090>
- Bourns PEC11R datasheet — <https://www.bourns.com/docs/Product-Datasheets/PEC11R.pdf>
