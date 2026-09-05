# naub V2 bench test kit

## Live input monitor  <- start here

    python3 ~/naub-module-test/live_inputs.py

Full-screen live view of all 17 inputs. No CIRCUITPY drive needed - it pushes a
reporter over the REPL. Encoders show a movement bar, live position, and CW/CCW
totals; switches light up green while held. Each input ticks off as you exercise
it (encoders need BOTH directions), with a running 13-item coverage line so you
can see when everything is covered. Ctrl-C to quit.

`--raw 5` prints 5s of raw frames instead, for checking the link without a TTY.

## Stage 4 - display bring-up (AFTER U2 is soldered)

    python3 ~/naub-module-test/run_stage4.py --check   # bus shorts only, panel not driven
    python3 ~/naub-module-test/run_stage4.py           # full bring-up

Order matters, and it follows BRINGUP.md Stage 4:
1. **Short check on GP8-GP12** first, and it refuses to go further if it finds
   one - you just soldered 20 pins under glass you cannot lift.
2. **SPI1 + ST7565 init** (EA DOGM128-6 "Single Supply 3.0/3.3V" sequence:
   bias 1/9, booster 4x, 0x2F power control, V0 ratio 0x27).
3. **Stops and asks for TP_VOUT** before you look at the glass - 9-13V means the
   pump runs, ~3.3V means the cap network is wrong (CAP3P->CAP1N, PCB_DESIGN 4.2).
4. **Six test patterns**: all-on, all-off, vertical stripes, horizontal stripes,
   checkerboard, border+diagonal. These separate dead SEG lines from dead COM
   lines from page-addressing errors.
5. **Contrast sweep** 0x00-0x3C so you can pick the value that looks right.
6. **Backlight ramp** on GP13 (no-op until JP_BL is fitted).

Two constants at the top of `stage4_display.py` if something looks wrong:
`ORIENT_180` (flips ADC/COM if the image is upside down) and `COL_OFFSET`
(set to 4 if the image is shifted horizontally - ST7565R has 132 columns,
DOGM128 uses 128).

## deploy_repl.py

    python3 ~/naub-module-test/deploy_repl.py stage3_test.py

Writes any local .py to the board as `code.py` over the REPL. Use when the
CIRCUITPY drive will not mount (this board's mass-storage interface binds on
macOS but never produces a block device).

## Stage 3 - encoders + Chocs, BEFORE the display goes on

    ~/naub-module-test/run_stage3.sh

Or by hand: copy `stage3_test.py` to `CIRCUITPY/code.py`, then
`screen /dev/cu.usbmodem101 115200`.

What it does, in order:
1. **Bridge scan** - re-run after soldering 9 new parts. Drives each of the 17
   inputs low, watches the other 16. Encoder contacts closed at a detent are
   reported as normal, not failed.
2. **9 switches** - one at a time, with bounce measurement (edge count + settle
   time). Flags any switch already low before you touch it.
3. **4 encoders** - CW then CCW, checks direction is not inverted and that the
   count matches the detent count; then a **fast-spin** check for missed counts.
   Missed counts *only* at speed points at the RC filter (PCB_DESIGN 2.1,
   first fallback is 10nF -> 4.7nF).
4. **Summary** - explicit ALL CLEAR / DO NOT FIT THE DISPLAY YET.
5. **Live monitor** - free poking afterwards.

`DETENTS_PER_REV = 20` at the top assumes PEC11L-4x20. Change if your part differs.

## Notes
- GP17-GP23 are the module's back solder-through pads. CircuitPython's
  `waveshare_rp2350_tiny` board definition does **not** name them, so this test
  uses `microcontroller.pin.GPIO17`..`GPIO23`. Same Pin objects, no downside.
- ENC3_SW is on **GP23**, remapped from GP16 (GP16 drives the onboard WS2812).

## Files
- `stage3_test.py` / `run_stage3.sh` - the above
- `selftest.py`, `selftest2.py`, `prove.py` - pre-solder module acceptance
- `waveshare_factory_test_backup.uf2` - stock Waveshare image, if you ever want it back
- `cp-10.3.0-tiny.uf2` - CircuitPython currently on the module
