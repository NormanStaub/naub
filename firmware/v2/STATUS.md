# V2 status: what landed, what is unfinished

Written 2026-09-05, covering the work since `48a20ec` (the font change). The
detail lives in the commit messages and in `README.md` / `CONFIG_MENU.md` /
`PROTOCOL.md`; this is the map, and the honest list of what is *not* done.

---

## What landed

### The panel
- **Backlight follows you** (`device/idle.py`): full while in use, 10% after a
  minute, off after five, fading between — 3 s down, 0.1 s up, interpolated in
  perceptual space. Any knob or key wakes it and still does what it was pressed
  for.
- **The frame loop is paced to the glass at 92.3 Hz.** The ST7565R scans at
  `fOSC/(4*65)` — 77 Hz typical, 92 at the fast end of tolerance — so the loop
  used to rewrite the controller's RAM about five times per displayed frame.
- **Wipe edges are 2 px**, and eased animations state a duration rather than a
  per-frame fraction. Capping the loop had retimed them: the drawer's fall and
  the shelf's scroll went from ~29 ms to 110 (4x), and the mode wipe -- gated
  on the 16 ms meter tick, which can only be tested once a frame and so fires
  every 21.7 -- crossed the panel in 13 jumps of up to 27 px. It is frame-locked
  now: same 282 ms, 27 steps. `_ease()` derives the fraction from `FRAME_NS`.
- **The meters run at 46 Hz, not 62.5**, for that same gating reason, and always
  have. `ballistics.FALL` was tuned by eye against 46, so it was left alone and
  the docstring corrected.
- **Every clock is `time.monotonic_ns()`.** The float `monotonic()` is single
  precision here and its granularity doubles as uptime doubles (0.488 ms at
  1.5 h, ~8 ms after a day). A 16 ms tick timed against that eventually stops
  being a tick.

### The mixer
- **One app, one column**: assigning an app another column is pinned to swaps
  the two, on both device and host.
- **Assignments outlive everything** — `%LOCALAPPDATA%\naub\pins.json`, pushed
  back on connect. They were being thrown away on every deploy.
- **The switcher's list is the device's** (`device/wheel.py`): an app must be
  audible for 4 s in one run to earn a place, and keeps it for 10 minutes after
  it goes quiet. Notification chimes never appear.
- **Discord (and anything else) meters the session you can actually hear.** A
  program can hold several sessions; `appmeter` kept whichever the enumeration
  returned last, measured six times out of six to be the silent one.
- **Peak markers** sit a pixel clear of the bar and fall slower
  (`device/ballistics.py`, one home for all three meters).

### The CONFIG screen (new)
- Fourth mode, replacing CUSTOM 2. Three knobs labelled on screen: tabs,
  cursor, value — turned live, no arming, no commit.
- Settings are tuples; `CONFIG_MENU.md` is the guide to adding them.
- **ADVANCED DISPLAY** submenu: the ST7565R registers bring-up swept by hand,
  live on the glass.
- **DISPLAY VIEW ANGLE is the contrast register**, mapped over the measured
  25–40 range, kept in agreement with the raw row in both directions.
- **SHADING** shows a waving flag whose gradient is `host/frc.py`'s encoder
  transcribed, so it previews the switcher's icons honestly. That is why the
  screen runs four FRC planes.
- The list scrolls smoothly.
- **A `CARD` kind**: a page that is a picture rather than a list, and the
  `SYS > GITHUB` page that uses it — the repo's QR beside the link it encodes.
  A card's view simply has no rows, so the cursor, scrollbar and value knob all
  do nothing without being told cards exist. `gen_qr.py` decodes the code before
  baking it, and the rendered framebuffer decodes back to the URL.

### Host
- The loopback capture (a 48 kHz thread) runs **only while the EQ screen is
  up**; `ameter` drops to 5 Hz when the switcher is shut.
- `naub_bridge.py --sessions` prints the grouped WASAPI table.
- **`naubd` is now the V2 daemon**, ported from `naub_bridge.py` — the whole of
  it: meters, `apps`/`ameter`, pins, the EQ into Equalizer APO, and the
  loopback spectrum with its own FFT. It is what runs at login; the Python
  bridge stays as the development tool and the two share `pins.json`. proto=1
  and the V1 Python host are retired.
- Three things the port turned up, none of them visible from the Python side:
  - **DTR must be asserted**, or CircuitPython's `usb_cdc.data.connected` stays
    false and the device silently drops every line it tries to send. The Python
    bridge sets `dtr = True` before opening; nothing had written down that the
    device→host direction depends on it.
  - **And asserting it is not instant.** `connected` flips a moment after the
    line does, so a host that opens the port and writes `hello` straight away
    gets no banner: the device runs `announce()` while it still believes nobody
    is there and throws all three lines away. **`naub_bridge.py` has this race
    too** — it simply never needed the banner, so nobody noticed the link was
    one-way on connect. `naubd` waits 250 ms once per connect.
  - The bridge's `time.sleep(0.002)` means its wake rate is set by whatever
    else on the PC has raised the Windows timer resolution — measured at
    **408 Hz**, not the 31.5 Hz its docstring assumes, and unaffected by
    `--rate`.

### The board dying: found and fixed
Caught on the console, reproducible within a second of the daemon connecting:

```
File "code.py", line 639, in <listcomp>
ValueError: invalid syntax for integer with base 10
```

Line 639 is the `smeter` handler, and the value it choked on was a token like
`5hello` — **a half-written line with the next one glued onto it.**

The cause was on the host. `serialport-rs` has one timeout for both directions,
and `naubd` set it to 10 ms; `naub_bridge.py` uses `timeout=0` for reads and
`write_timeout=0.5` for writes, so the daemon's write timeout was **50× shorter
than the Python's**. At that length `resync()`'s burst timed out routinely — and
a timed-out write has already put *some* of the line on the wire. The daemon
then treated a timeout as "device busy, skip this line, carry on", which left
the device holding an unterminated fragment for the next line to land on.

Fixed on both sides:

- **Host:** write timeout to 500 ms; reads made non-blocking (`bytes_to_read`
  first, the way pyserial's `in_waiting` does) so the long timeout does not
  stall the meter loop; line and terminator written in one call; **any** write
  failure now drops the link instead of continuing on a stream that may be
  desynced — there is no way to ask how many bytes landed, so carrying on is
  never safe. On connect the daemon writes a bare newline first, to close off
  any fragment a previous host left behind (the device's receive buffer
  survives a soft reload).
- **Device:** the message dispatch in `demo_mixer.py` is wrapped in
  `try/except` and prints `ignoring bad line ...`. §1 already promises unknown
  verbs are ignored; a line whose *numbers* are unparseable deserves the same,
  because the wire is not a trusted source. Verified by injecting
  `smeter 10 20 5hello` — the panel now shrugs and keeps rendering at 92 fps.

Regression test: killing the daemon mid-stream six times in a row, which
reliably poisoned the device before, now leaves it running throughout.

The earlier `RuntimeError: name too long` seen during the same window was never
reproduced separately, but it is the same shape — a corrupted line reaching a
handler that did no validation — and the guard covers it.

### Known and unexplained (device)
- **`PLANE GUARD`** still fires: `PLANE GUARD: i=2 n_planes=1 len(views)=1
  mode=0 mx.mode=1` was logged flipping into EQ mode. Harmless (the guard is
  doing its job) but the underlying mismatch is still there.

---

## Unfinished

### Settings that exist but do nothing
Ten of the twenty rows are placeholders in the right shape — they were built
so the list, scrolling and tabs had something to be tested against. Wiring one
is the `apply_display()` pattern in `CONFIG_MENU.md` §4.

| tab | key | what it would take |
|---|---|---|
| DISP | `shading` | the drawer/icon pipeline would have to accept a plane count at runtime; the icons are baked 4-plane (`gen_switcher.py`) |
| DISP | `dim_after` | trivial — `idle.Idle.stages`, but the stages are a tuple built at construction |
| SYS | `meter_rate` | the host's `--rate`, so it needs a protocol verb |
| SYS | `knob_step` | the `0.04` per detent in `demo_mixer` |
| SYS | `hold_mute` | gate the `HOLD_NS` branch |
| SYS | `media_keys` | gate `md.tap()` |
| OS | `autoconnect`, `keep_columns` | host-side behaviour, so both need a verb |
| OS | `link`, `firmware` | INFO rows showing hardcoded strings; should report the real link state and a real version |

### Not built at all
- **CUSTOM 1** is still a stub: flipping to it moves the mode bar and shows the
  mixer screen behind it. It is the last placeholder mode.
- **No persistence for any config value.** Everything resets on reload, which
  is deliberate while the display registers are being calibrated — no setting
  can brick the panel across a power cycle. When it is wanted, `key` is the
  handle: a dict to NVM or to the host's own state file.
- **No config protocol.** Same shape as the EQ's: `cfg <key> <value>` both ways.
- **No `ACTION` kind** (RESET, FACTORY). It belongs on the ENC3 *click*, never
  on a detent.
- **The tab strip fits three tabs.** A fourth needs a narrower cell layout in
  `draw_tabs()`.
- **The device shows nothing before the host connects** — column names, levels
  and the EQ state all come from the bridge. Pins survive on the host, not on
  the device.

### Open decisions, waiting on you
- **`AUTO_PREAMP = False`** (`host/eqapo.py`). Boost is boost; the alternative
  guarantees headroom but measured a 12.3 dB drop across everything else when
  one band was maxed.
- **The mode list does not wrap**, so there is no wrap-direction question yet —
  but if it ever does, forward from the last mode to the first would wipe
  right-to-left, following the button rather than the destination.
- **3-frame shading modes were dropped** from SHADING, because a 3-frame
  pattern inside the 4-plane cycle repeats a frame and shows the wrong grey.
  They can come back with that caveat if you want them.
- **The scrollbar is 2 px**, thinner than the bar in the sketch.

### Known and unexplained
- **`PLANE GUARD`**: the screen going dark was `views[i]` with `i` past the end
  of a shorter plane list. `set_screen()` keeps the three in step and a
  9700-transition soak never reproduced the original fault, so the root cause
  is still unproven. The guard prints one line if it ever fires; that line is
  worth reporting.
- **`--rate 50` is really 31.5 Hz.** Windows' timer granularity is 15.6 ms and
  the bridge's 2 ms sleep rounds up to it. Left alone deliberately: fixing it
  means `timeBeginPeriod(1)` and a loop spinning sixteen times as often, which
  is more load on the PC to feed a meter its own ballistics already smooth.

---

## Portability

**Windows is the reference platform; the code should stay as general-purpose as
is reasonable, Linux included.** `MACOS_PORT.md` is the study behind that — the
short version is that transport, deploy, the bridge loop, system volume and the
build-time tooling port cleanly, while per-app metering and per-app volume are
where the platforms genuinely diverge. Linux has **not** been studied; the shape
looks more favourable than macOS's, because PipeWire and PulseAudio expose
per-stream volume and peak monitoring the way WASAPI does, but nothing here has
been verified against them and none of that should be quoted as a finding.

The rule for ongoing work: keep platform-specific calls behind the module that
owns them, and prefer a platform-neutral library to a second platform-specific
one where the cost is comparable. Three items outstanding, all found by the
macOS study and all worth fixing on Windows anyway:

| where | what |
|---|---|
| `naub_bridge.find_ports()` | `_iface()` reads `MI_nn` from the hwid, a Windows registry convention, and falls back to a trailing `:N` in `p.location`. Neither is reliable elsewhere, and on macOS `comports()` returns a `cu.*` *and* a `tty.*` node for every interface, so the console/data pair would be picked wrongly. Needs a per-platform branch. |
| `host/gdifont.py` | GDI, and its docstring blames Pillow for something Pillow supports (`.FON` opens with `index=`; the real fault is an unreachable `FT_ENCODING_NONE` charmap). Build-time only, so the answer is `freetype-py` — one platform-neutral path, not a second platform-specific one. |
| `naub_bridge --rate` | 50 means a measured 31.5 Hz only because of Windows' 15.6 ms timer granularity. Anywhere else it would really be 50, which is the wrong default on a platform where a poll costs a captured buffer instead of a free peak. The rate should be stated, not inherited from a scheduler quirk. |

The structural move, whenever it is worth doing: split `appmeter` / `eqapo` /
`sysmeter` into platform-suffixed modules behind a small factory — the shape the
V1 `host/naub_host/` already used (`audio_windows.py`, `eq_windows.py`).
`naub_bridge.py` needs three changes to be platform-neutral: `PIN_PATH`, the
`_iface()` branch, and `import appmeter` becoming a dispatch. Doing that on
Windows first makes any port additive rather than a fork.

---

## Traps this codebase has already fallen into

Collected because each one cost real time, and each is still reachable:

- **A short slice assignment resizes a `bytearray` on CPython and raises
  `IndexError` on the device** — so the PC preview looks perfect and the panel
  crashes. Clip every copy; validate at construction.
- **Slicing the source of an assignment allocates.** Slice the destination.
- **`ctypes.cast` on a COM interface does not take a reference.** The cached
  session managers dangled and killed the bridge with an access violation
  minutes later, nowhere near the cause. `QueryInterface` takes it properly.
- **MicroPython's small-int boundary**: shifting a 16-bit value up 40 bits
  makes a 56-bit integer, which goes on the heap — thirty times a plane a frame
  in the patch overlay.
- **A crashed `code.py` suspends auto-reload**, so deploys appear to do nothing
  until Ctrl-C then Ctrl-D over the console port.
- **Test the sound, not the drawing.** The EQ drew correct curves for days
  while writing `Filter: OFF` for every band.
