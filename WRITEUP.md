# naub — Developer Handoff / Technical Writeup

**Purpose:** get a developer (or a future you, on a different machine) productive
fast, and record the decisions, findings, and gotchas that are **not** captured
in the other docs or the code. The formal specs live in `BOM.md`, `WIRING.md`,
and `V1_BUILD.md`; **this file is the context around them** — the "why," the
dead ends already explored, and the current state of play.

**Last updated:** 2026-06-17 · **Branch:** `main` · **Remote:** `github.com/NormanStaub/naub`

> Note: `.claude/` is git-ignored, so any local Claude memory/config does **not**
> transfer with the repo. The design rationale that mattered has been folded into
> this file on purpose, so the repo is self-contained.

---

## 1. What this project is (30-second version)

A USB **composite device** (Raspberry Pi Pico 2 / RP2350) that acts as a
hardware audio mixer:

- **System volume** → USB **HID Consumer Control** → driverless on Win/macOS/Linux.
- **Per-app volume** → **USB CDC serial** → a small host helper that talks to the
  OS audio API (Win WASAPI / Linux PipeWire / macOS virtual driver). *deej-style.*

Four rotary encoders (turn = volume, press = mute/select), a 2.9" e-paper display
for level readout, two aux buttons.

---

## 2. Current state — what exists vs. what doesn't

**Exists and validated (bench):**
- Full hardware spec + pin map (`BOM.md`, `WIRING.md`) — pin map chosen so the
  display lines match Waveshare's official Pico mapping, and each encoder's pins
  are adjacent for tidy routing.
- Three CircuitPython **bring-up examples** under `examples/` (see §5). These
  prove pin map, encoder decode, and — most importantly — the **e-paper refresh
  behavior and UX pattern**. They are bench/validation code, **not** final firmware.

**Does NOT exist yet:**
- **Final firmware.** No HID Consumer Control, no CDC serial, no PIO encoder
  decode yet — the examples use `rotaryio` and a hand-written display driver.
- **Host helper.** Nothing written for any OS. Protocol not yet specified.
- **Custom PCB.** V1 is hand-wired. PCB is V2 (see §8 / roadmap).
- **Aux buttons.** Part not sourced. Buttons may be dropped from V1.

**The display work is the deep part of this project.** Most of the hard-won
knowledge is about making e-paper feel acceptable — see §6.

---

## 3. Dev environment setup (new machine)

1. **Clone:** `git clone https://github.com/NormanStaub/naub.git`
2. **Hardware:** Pico 2 **H** (header version) for prototyping + the parts in `BOM.md`.
3. **Firmware runtime:** **CircuitPython 10.2.x, ARM build, for Raspberry Pi
   Pico 2 (RP2350).** Flash via BOOTSEL → drag the `.uf2` onto the `RP2350` drive.
4. **Run an example:** copy that example's `code.py` to the `CIRCUITPY` root, and
   copy its libs to `CIRCUITPY/lib/`:
   - `adafruit_display_text/` (rasterizes text into the framebuffer)
   - `adafruit_ticks.mpy` (dependency of the above)
   - **No `adafruit_ssd1680` needed** — the panel is driven manually (see §6).
5. The `CIRCUITPY` drive auto-mounts at `/Volumes/CIRCUITPY` on macOS (it's a
   declared working directory). Edit `code.py` in place and it auto-reloads.

> **Doc vs. reality gotcha:** `V1_BUILD.md` §5 and `WIRING.md` §2 mention the
> **GxEPD2** library (that's Arduino/C++). The actual examples are **CircuitPython
> with a hand-rolled SSD1680 driver** — we reference the GxEPD2 `GxEPD2_290_T94`
> *recipe* (init sequence + waveform command bytes) but do not use the library.
> If the final firmware stays CircuitPython, GxEPD2 never enters the picture.

---

## 4. Architecture notes

- **Two independent volume channels by transport:** system volume is "free"
  (HID, no host software); per-app volume *requires* the host helper. Bring up
  HID first — it's the driverless win and needs nothing on the host.
- **Host helper design (planned, not built):** one text protocol over CDC, three
  thin OS adapters. Windows/Linux are clean (WASAPI / PipeWire); **macOS is the
  hard one** — per-app output volume needs a **virtual audio driver**, which is
  the bulk of the macOS effort. Reference prior art: deej
  (`github.com/omriharel/deej`).
- **GPIO budget:** 20 of 26 used; spare GP20/21/22/26/27/28. Plenty of headroom.

---

## 5. The examples (build on each other)

Order of sophistication — read/extend the last one:

| Dir | What it proves |
|---|---|
| `encoder_bar_epaper/` | **Reference for everything.** One encoder → level bar + number. Hand-written SSD1680 driver, windowed partial refresh, incremental drawing. **Its README is the canonical partial-refresh latency characterization — read it first.** |
| `dual_encoder_blobs/` | Two encoders, two bars, press-blobs. Multi-region partial refresh in one gate-line window. |
| `dithered_preview/` | **Most refined.** The UX answer to e-paper latency: live number + bar, **dithered "preview"** of the pending change that commits to solid on settle. Press-blobs + Button 1 toggle + idle-deferred ghost cleanup. |

Common implementation facts across all three:
- Framebuffer is 1-bpp, maintained in **landscape 296×128 logical** space; `px()`
  maps to the panel's native **128×296** RAM with `MNX/MNY` orientation flips.
- Refresh recipe: **full** = `0x22 0xF7`, **partial** = `0x22 0xFC` (the panel's
  on-chip OTP fast waveform), **windowed** to the changed gate lines `GY0..GY1`
  so the rest of the panel is never driven and stays clean.
- Drawing is **incremental** (only the changed band repainted) — this was the one
  real CPU-side latency win (~90–170 ms → ~20–30 ms per update).
- A full-screen **ghost-sweep** refresh runs occasionally (every N partials,
  and/or after idle) to clear partial-refresh ghosting; deferred to idle so it
  never flashes mid-interaction.

---

## 6. Display — the hard-won knowledge (read before touching refresh)

This is the crux of the project. **The e-paper refresh floor is ~0.5 s and it is
a hard physical limit, not a tuning problem.** Full characterization is in
`examples/encoder_bar_epaper/README.md`; summary + the decisions around it:

**Refresh latency facts (measured on our panel):**
- Clean partial refresh ≈ **498 ms**, and it is **flat regardless of pixel
  count** — refresh time is set by **waveform duration** (frame count × frame
  period), *not* changed area. Targeting fewer pixels saves only a couple ms of
  SPI data, **not** refresh time.
- Vendor's **0.3 s** partial spec is **@ 25 °C**; we measure ~0.49 s at cooler
  room temp. **The whole gap is temperature** — e-paper waveforms stretch when
  cold. Not addressable in firmware.
- **Custom fast LUT (`0x32`) is a dead end** — ~395 ms at 6 frames but causes
  whole-screen ghosting/flicker (the analog power-cycle/VCOM per update isn't
  contained by the gate window). Modest gain, real visual cost.
- **Lean repeated-partial (`0xCC` reuse-waveform) saved only ~8 ms** vs the full
  `0xFC` reload — pure waveform time dominates. Not worth the SSD1680 LUT-quirk risk.
- **Net floor: ~0.5 s clean partial @ room temp.** Accepted trade for zero-power
  hold + sunlight readability.

**The UX answer (this is the product-defining decision):**
- **Update-on-settle, not per-detent.** Don't fire a refresh on every click —
  let the detents give live tactile feedback, render bar+number together in one
  refresh, and the `dithered_preview` example previews the pending change as a
  gray dither that commits to solid ~0.5 s after the knob stops. This makes the
  0.5 s invisible during a sweep. **This is the intended model for final firmware.**

**Driver approach decision:** the SSD1680 is driven **directly** (no display
library) so the refresh waveform is under our control. Keep it that way — the
whole latency story depends on issuing `0xFC`/`0xF7` and windowing the gate lines
ourselves.

---

## 7. Decision log (the stuff not written down anywhere else)

These came out of analysis/conversation and would otherwise be re-litigated:

**(a) E-ink is a firm choice — do not propose switching display tech.**
The ~0.5 s floor is accepted in exchange for zero-power image hold + sunlight
readability. "Use an OLED/LCD for fluid motion" is a *non-answer* for this product.

**(b) No faster 2.9"-class panel meaningfully helps.** Surveyed Good Display /
Waveshare fast-refresh parts (2026-06). Findings:
- Our panel ≈ **Good Display GDEY029T94** (SSD1680) — its rated 0.3 s partial @25 °C
  *is* our measured behavior. We already own the category baseline.
- Across SSD1680/SSD1685/UC8253 2.9"-class parts, **partial refresh is ~0.3 s
  everywhere** — it's FPL-film + waveform physics, shared by the whole class.
- "Fast refresh ~1.5 s" marketing = faster **full** refresh (one flicker), **not**
  faster partial. It would only speed our periodic ghost-sweep, not per-detent feel.
- The only genuinely fast e-paper is **Modos / Caster** (open-source FPGA
  controller, ~75 Hz, per-pixel) — but it's a parallel-driver architecture for
  **4.3″–13″** panels, not a tiny SPI module. Not viable for a knob display.
- **Only upgrade worth a look:** `GDEY029T71H` (2.9″, 384×168, SSD1685) — *if* we
  ever want higher resolution + a faster ghost-sweep. Partial stays 0.3 s; it
  will **not** change how a turning knob feels.

**(c) Swapping the display's interface PCB is a dead end.** On the Waveshare
module the **SSD1680 is COG-bonded to the panel's own FPC ribbon** — the carrier
board you'd swap is just the FPC connector + charge-pump passives (boost
inductor/caps for the ±high-voltage rails) + the SPI header. The controller,
LUTs, and waveform engine travel with the panel, so a different carrier changes
none of the limiters. (It only touches drive-rail voltages, which the SSD1680
itself programs and caps.)

**(d) Final-PCB MCU: stay on RP2350.** Reasoning:
- MCU is not the bottleneck (the panel is), so switching families discards
  validated firmware for zero functional gain.
- RP2350 fits naub's needs unusually well: **PIO quadrature decode** (one state
  machine per encoder, glitch-free, zero CPU), native **USB composite HID+CDC**,
  ample GPIO.
- **RP2350-E9 erratum** (the internal **pull-down** input-latch issue) is already
  sidestepped: the design wires everything **active-low with internal pull-ups +
  RC debounce**. Do **not** reintroduce it by switching to internal pulldowns on
  the final board.
- **Module-down vs chip-down:** for an ultimate-but-small-batch build, **reflow a
  headerless Pico 2 module onto the custom PCB** (inherits flash, crystal, USB
  protection, on-board SMPS — low bring-up risk). Go **bare-RP2350 chip-down**
  only for a real production run or if enclosure thinness demands it (then you own
  QSPI flash routing, the 12 MHz crystal, the RP2350 core SMPS rails, USB ESD,
  BOOTSEL). Firmware is unchanged either way.
- **Honest fallback:** RP2040 would also do this job (PIO + USB-FS, predates E9,
  cheaper) — only worth switching for a genuine volume/cost reason, since it means
  re-validating. Absent that, RP2350 is already working — keep it.

---

## 8. Known issues / gotchas

- **Button 1 wiring:** in `dual_encoder_blobs/` Button 1 (GP6) is intentionally
  unused — **it was miswired during bring-up.** In `dithered_preview/` it works
  and uses the illuminated button's **blue (NO switch) wires**, *not* the
  red/black LED wires. Watch this when wiring buttons.
- **Aux buttons not sourced** (`BOM.md` item 5) — pick a part or drop them from V1.
- **Debounce caps are optional for V1** — firmware debounce usually suffices; the
  10 nF (A/B) / 100 nF (SW) caps solder in later with no rework if you see jitter.
- **e-paper "relax" artifact:** the first partial after a full refresh slightly
  lightens deep blacks, recovering over a few refreshes — inherent to the
  full↔partial waveform-density difference; mitigated by rarer full refreshes.
- **Power entry:** feed the Pico from **one** source only — the USB-C pigtail
  **or** the Pico's own jack, never both.
- **Display on 3.3 V**, not 5 V (logic-level matched; `3V3(OUT)`, pin 36).
- **`SETTLE_S` below ~0.5 s has little effect** — refresh duration dominates any
  shorter debounce.

---

## 9. Open items

- [ ] Source or drop the **aux button** part.
- [x] Specify the **CDC text protocol** for the host helper. → **`PROTOCOL.md`**
      (proto=1: deej-style per-app volume + bidirectional, with Base64 1-bpp
      program icons pushed to the e-paper). Implemented by `firmware/` + `host/`.
- [ ] Decide whether final firmware stays **CircuitPython** or moves to C/Pico-SDK
      (PIO decode + TinyUSB composite). Examples are CircuitPython today.
- [ ] Confirm display firmware-class detail **only if** ever using GxEPD2/Arduino
      (`GxEPD2_290_T94` vs `GxEPD2_290_GDEY029T94`); irrelevant for the current
      hand-rolled CircuitPython driver.

---

## 10. Roadmap

1. **Firmware proper:** HID Consumer Control + CDC serial; PIO hardware quadrature
   decode (1 SM/encoder); e-paper **update-on-settle / clear-on-idle**.
2. **Host helper:** one text protocol, three OS adapters (Win/Linux clean; macOS
   needs a virtual audio driver).
3. **V2 hardware:** custom carrier PCB — Pico 2 module footprint (reflow-down),
   integrated USB-C + CC resistors, encoder/cap networks, display FPC connector,
   PCB-as-faceplate for rigid knob mounting.
4. **V3 (optional):** bare-RP2350 chip-down, only if going to production volume.
