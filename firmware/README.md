# naub firmware (V1)

CircuitPython firmware for the Pico 2 (RP2350) that implements the **per-app
mixer over USB CDC** (the [`PROTOCOL.md`](../PROTOCOL.md) `proto=1` link) and
renders the e-paper display, **including host-pushed program icons.**

Builds directly on the validated `examples/dithered_preview` SSD1680 driver and
update-on-settle UX — the refresh path is unchanged on purpose (see
`../WRITEUP.md` §6).

## What it does

- Reads encoders 1 & 2 (rotate = volume 0–100, press = mute toggle) and Button 1.
- Emits `vol` / `mute` / `btn` events live over the CDC **data** port.
- Accepts `label` / `icon` / `iconclear` / `level` / `mute` from the host and
  draws a 4-column panel: **icon · label · level bar · number**, with a strike
  over the icon when muted.
- `hello` → banner + full state snapshot; `ping` → `pong`.

Channels 0–1 are live (encoders populated); 2–3 are drawn but idle until wired.

## Flashing

1. Flash **CircuitPython 10.2.x (RP2350 build)** via BOOTSEL (drag the `.uf2`).
2. Copy to the `CIRCUITPY` drive root:
   - `boot.py`  ← **required** (enables the second/data CDC port)
   - `code.py`
3. Copy to `CIRCUITPY/lib/`:
   - `adafruit_display_text/`
   - `adafruit_ticks.mpy`
4. **Power-cycle the board** (unplug/replug) so `boot.py` takes effect — only
   then does the second serial port enumerate. You should now see **two** serial
   ports; the host helper auto-finds the data one.

## Notes

- Debug `print()` goes to the **console** port (REPL); the protocol uses the
  **data** port. They are separate — opening the data port in the host won't
  disturb the REPL.
- If `code.py` prints `WARN: usb_cdc.data is None`, `boot.py` didn't run — make
  sure it's at the drive root and that you power-cycled (a soft reload is not
  enough to re-enumerate USB).
- Refresh is ~0.5 s per update by design (e-paper physics). `vol` events are sent
  per-detent regardless, so host audio stays responsive even while the panel
  catches up.
