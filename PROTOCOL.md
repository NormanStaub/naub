# naub — Host ⇄ Device Protocol (v1)

**Transport:** USB **CDC** (virtual serial), the *data* interface of the Pico's
composite device. **System volume** rides a separate **HID Consumer Control**
interface and is **not** part of this protocol — it is driverless and needs no
host software. This document covers only the **per-app / CDC** channel.

**Lineage:** this is *deej, extended.* [deej](https://github.com/omriharel/deej)
sends one line of pipe-separated slider values, host → nothing back. naub keeps
the "plain-text, one-message-per-line, greppable in a serial monitor" virtue but
makes it **tagged and bidirectional**, because naub's display is an output the
host drives — including **program icons** rendered on the e-paper.

---

## 1. Framing

- **One message per line.** Lines are terminated with `\n` (0x0A). A leading
  `\r` is tolerated and stripped.
- **Encoding:** UTF-8, printable ASCII for all verbs/args. Icon payloads are
  **Base64** (also ASCII), so the entire protocol stays line-safe and
  human-inspectable.
- **Tokens** are separated by a single space. The **verb** is the first token.
- The **last field** of `label` and `icon` messages may itself contain spaces /
  is parsed as "rest of line" — see each message.
- **Unknown verbs are ignored** by both sides (forward compatibility). A device
  that receives a verb it doesn't understand must not error or stall.
- Max practical line length is set by the largest icon (32×32 → 128 B → ~172 B
  Base64, ~190 B total). Keep a ≥256 B line buffer; ≥512 B is comfortable.

**Direction tags below:** `D→H` = device to host, `H→D` = host to device.

---

## 2. Channels

- Channels are indexed **0–3** (four encoders). All four are wired and live on
  V1: channels **0–2** are the app knobs and channel **3** is the system/master
  knob (also the EQ-menu toggle — see §10).
- A channel maps, host-side, to one or more application audio sessions (deej's
  `slider_mapping`). The reserved match token `__system__` means the default
  output device (system/master) — useful when you want a knob to ride the master
  endpoint instead of an app.

---

## 3. Device → Host (events & telemetry)

The device emits an event **only in response to physical user input.** It must
**never** echo a state the host pushed (`level` / `mute` / `label` / `icon`) —
that would create a feedback loop. This one rule keeps the link clean.

| Message | Meaning |
|---|---|
| `naub proto=1 channels=4 fw=<ver>` | **Banner / handshake.** Sent once on boot, and again in response to `hello`. `fw` is a free-form firmware version string. |
| `vol <ch> <0..100>` | A knob moved (switch **up**). Emitted **live, per detent** (every value change), so host audio tracks the knob smoothly. `<ch>` 0–3, value 0–100 integer. |
| `mute <ch> <0\|1>` | An encoder was **clicked** (pressed and released *without* turning). Reports the *new* mute state. Fires on **release**, so it can be told apart from a hold-and-turn. |
| `cycle <ch> <dir>` | The knob was **turned while its switch was held** — a request to reassign this channel's audio source. `<dir>` is `+1` (next) or `-1` (previous), one per detent. The host owns the source list and replies with new `label`/`icon`/`level`/`mute`. |
| `eq <band> <0..100>` | *(proto=2)* An **EQ band knob moved** while the EQ menu is open. `<band>` is `0`=low `1`=mid `2`=high; value 0–100 (50 = flat). Emitted **live, per detent**, like `vol`. The host applies it to its global tone control (Windows: Equalizer APO). See §10. |
| `btn <n> <0\|1>` | Aux button `n` toggled to the given latch state. V1 firmware emits `btn 0 <s>` for Button 1. |
| `pong` | Reply to `ping`. Liveness only. |

**Encoder gestures (firmware):** turn = `vol`; **hold switch + turn** = `cycle` (volume does not move while held); **click** (press+release, no turn) = `mute`. The device disambiguates on release: if it rotated during the hold it was a cycle (no mute is sent); otherwise it's a mute toggle.

A `hello` is answered with the **banner only** — no `vol`/`mute` snapshot. The
device boots with all levels at 0 and is *not* the source of truth for volume at
startup: the **host** reads each app's real volume/mute and pushes `level`/`mute`
during its first reconcile. (If the device replied with a `vol 0` snapshot, the
host would apply it as user input and zero every app — so it deliberately does
not.)

---

## 4. Host → Device (display & assignment)

The host owns everything the device *shows*: the per-channel label text, the
program icon, the displayed level, and the displayed mute state.

| Message | Meaning |
|---|---|
| `hello` | Ask the device to (re)send its banner and a full state snapshot. Send this right after opening the port. |
| `ping` | Liveness probe; device replies `pong`. |
| `label <ch> <text…>` | Set channel `<ch>`'s text label (e.g. the app name). `<text…>` is the rest of the line, may contain spaces; the device truncates to what fits (~11 chars at the V1 font). |
| `icon <ch> <w> <h> <base64>` | Push a **1-bpp** icon bitmap for `<ch>`. `<w> <h>` are pixel dimensions; payload is Base64 of packed rows (see §5). V1 expects **32×32**; larger is clipped, smaller is centered. |
| `iconclear <ch>` | Remove the icon for `<ch>`. |
| `level <ch> <0..100>` | Set the **displayed** level for `<ch>` *without* user input — e.g. initial sync, or when another app changed the volume out from under naub. The device updates its bar/number and does **not** echo a `vol`. |
| `mute <ch> <0\|1>` | Set the **displayed** mute state for `<ch>` (symmetric with `level`). No echo. |
| `eqset <band> <0..100>` | *(proto=2)* Set the **displayed** EQ band point (`0`=low `1`=mid `2`=high) without user input — e.g. seeding the EQ graph with the host's persisted tone settings on reconnect. Symmetric with `level`: the device moves the graph point and does **not** echo an `eq`. See §10. |

`level` / `mute` are how the host keeps the panel honest: if the user changes
Spotify's volume in the Windows mixer, the host pushes `level` so the knob
display matches reality.

---

## 5. Icon bitmap format

Icons are **1 bit per pixel**, **black-ink = 1**.

- **Dimensions:** `<w> <h>` in pixels. V1 display reserves a 32×32 box per
  channel; send 32×32 for a 1:1 fit.
- **Packing:** row-major, top row first. Each row is `ceil(w/8)` bytes
  (the **stride**); within a byte the **MSB is the leftmost pixel** (bit 7 =
  x0). Rows are byte-aligned (the last bits of a row's final byte are padding).
- **Bit value:** `1` = black (ink) pixel, `0` = white (no ink). E-paper draws
  ink as black on a white field, so `1` = visible.
- **Transport:** the packed byte array is **Base64**-encoded (standard alphabet,
  with `=` padding) and sent as the single final field of the `icon` message.

The host is responsible for turning a full-color program icon into this:
load → grayscale → resize to 32×32 → **Floyd–Steinberg dither** to 1-bpp → pack →
Base64. The panel is physically 1-bit, but dithering its dots makes icons read as
grayscale; full-range Floyd–Steinberg gave the most legible result when compared
live on the real 32×32 panel. So the wire stays plain 1-bpp yet icons show tonal
depth without any grayscale-waveform/refresh cost.

**Worked size:** 32×32, stride = 4 B, 32 rows = **128 bytes** → 172 Base64 chars.

---

## 6. A typical session

```
# host opens the CDC data port, then:
H→D  hello
D→H  naub proto=1 channels=4 fw=v1.0

# host reconciles channel 0 -> Spotify (it reads the real app volume = 63):
H→D  label 0 Spotify
H→D  icon 0 32 32 /4AAf/4B...<base64>...AAA=
H→D  level 0 63
H→D  mute 0 0

# user turns knob 0 down:
D→H  vol 0 61
D→H  vol 0 59
D→H  vol 0 57          # host sets Spotify session volume = 0.57 on each line

# user presses knob 0 (mute):
D→H  mute 0 1          # host mutes the Spotify session

# another app changed channel 1's volume externally; host corrects the display:
H→D  level 1 40
```

---

## 7. Port identification

CircuitPython with `usb_cdc.enable(console=True, data=True)` exposes **two**
serial ports: the REPL/console and the **data** port this protocol uses. They are
not reliably ordered across machines, so the host **probes**: open a candidate,
send `hello`, and if a line beginning with `naub ` comes back within a short
window, that's the data port. The console port won't answer. (A fixed COM port
may also be set in host config to skip probing.)

---

## 8. Design notes & rationale

- **Why tagged text, not deej's bare value line?** deej is one-directional, so a
  positional line suffices. naub's host must also *address the display* (which
  channel's icon/label/level), so messages need a verb and a channel index. Plain
  text keeps deej's debuggability — you can drive the panel by hand from any
  serial terminal.
- **Why Base64 icons on the same line protocol instead of a binary framing?**
  One transport, no mode-switching, still greppable, and 128-byte icons are tiny.
  A binary/chunked path is only worth it if icons grow large (>~1 KB) or update
  often — not the V1 case.
- **Why "events only on user input, never echo host state"?** It makes the loop
  trivially non-oscillating: host-initiated changes flow H→D and stop; user
  changes flow D→H and stop. The host is the source of truth for *audio*; the
  device is the source of truth for *user intent*.
- **Live `vol` vs. settle.** The device emits `vol` **per detent** so audio
  tracks the knob immediately (cheap WASAPI call). The *display*, by contrast,
  does **not** scroll while turning: it commits a single clean bar a beat
  (`SETTLE_S`) after the knob stops, with no numeric overlay. This is purely
  device-local rendering and independent of when `vol` is sent — the audio is
  realtime regardless of how lazily the e-paper repaints.

---

## 9. Reserved / future

- `btn <n>` is defined for n≥0; V1 uses only `btn 0`.
- A periodic `state …` snapshot line (full vector, deej-style) is intentionally
  **not** in v1 — `hello` covers resync. Add it under `proto=2` if a heartbeat is
  ever wanted.
- HID system-volume lives on its own interface and never appears here.

---

## 10. EQ overlay (proto=2)

A **global tone control** — bass / mid / treble for the whole output, *not*
per-app (Windows exposes no per-session EQ). The device gives it a screen and
three knobs; the host applies it through an existing system-EQ engine (Windows:
**Equalizer APO**, driven by writing a small hot-reloaded include file).

**Gesture (firmware).** Clicking the **system knob** (the channel mapped to
`__system__`, ch3 on V1) toggles the EQ menu open/closed. While open:

- The three **app encoders** become the **low / mid / high** bands. Turning emits
  `eq <band> <0..100>` live, per detent — the host applies it immediately.
- **Clicking** an app encoder **flattens** that band (sends `eq <band> 50`).
- The panel is replaced by a parametric-style graph: three control points (one
  per band, above their knobs) joined by a smooth curve. The curve is
  **illustrative** — it shows intent, not the literal filter response.

**Direction split** mirrors `vol`/`level`: `eq` is **D→H** (user moved a band);
`eqset` is **H→D** (host sets a displayed point, e.g. seeding the graph from
persisted settings on reconnect). The device never echoes an `eqset` as an `eq`.

**Host mapping.** Band value `0..100` maps linearly to **−12..+12 dB** (50 =
flat). V1 host filters: low-shelf @120 Hz, peak @1 kHz (Q≈0.9), high-shelf
@4 kHz. The engine is optional — if Equalizer APO isn't installed the host
ignores `eq` (the menu still opens; nothing is applied).

**Compatibility.** `eq`/`eqset` are additive; unknown verbs are ignored (§1), so
a proto=1 host and a proto=2 device (or vice-versa) interoperate — the EQ feature
is simply inert on whichever side lacks it.

---

## 11. V2 mixer screen (proto=2)

The V2 panel is a 128×64 LCD showing three app columns at once rather than one
channel at a time, so it needs *continuous* level data and its own notion of
"which apps exist". These verbs are additive; a proto=1 host and a V2 device
interoperate with the meters simply sitting still (§1: unknown verbs are
ignored). Implemented in `firmware/v2/device/link.py` and
`firmware/v2/host/naub_bridge.py`.

| Message | Dir | Meaning |
|---|---|---|
| `meter <v0> <v1> <v2>` | H→D | Live level for each of the three **columns**, 0–100, pushed continuously (50 Hz by default). Purely a display feed — it never affects audio and is never echoed. |
| `apps <A,B,C>` | H→D | Every app with a **live audio session**, in the host's preference order — the app switcher's list, and what its counter counts. **Comma** separated, because process names can contain spaces. An empty list is legal and means nothing is playing. |
| `ameter <v0> <v1> …` | H→D | One level per entry of the last `apps` list, 0–100. Sent at the meter rate while the switcher is **open**, and at 5 Hz when it is shut — the device needs it either way, because this is what it times each app's playing against (see *What the device offers*, below). |
| `pick <ch> <APP>` | D→H | The user assigned `<APP>` to column `<ch>` from the switcher. The host **pins** that column: it stops reassigning it as the busiest sessions churn, and it remembers the pin across restarts. **One app, one column** — if another column already holds `<APP>`, the two swap. |
| `mode <idx>` | D→H | The mode bar was flipped (BTN1 back, BTN2 forward), **and on connect**. `<idx>` indexes the device's mode list — `APP CTRL`, `EQ`, `CUSTOM 1`, `CUSTOM 2`. Not merely informational any more: the host starts and stops the loopback capture with it, so a host that ignores it just does more work than it needs to. |
| `wheel <0|1>` | D→H | The switcher opened or closed, and on connect. Lets the host meter the app list at a useful rate while it is being looked at and a lazy one when it is not. |
| `eqcaps <n> <fmin> <fmax> <maxdb> <sr> <ok>` | H→D | On connect: how many bands the host offers, the frequency range, the gain full scale, the endpoint sample rate (the device needs it to draw a truthful curve), and whether the engine would actually be heard. **`ok=0` is not an error** — Equalizer APO is attached per endpoint, so it is entirely normal for it to be installed and yet inert on the current device. The device says so rather than pretending to EQ. |
| `eqband <i> <kind> <f0> <db> <q> <on>` | H→D | One band's full state: `kind` is `PK`/`LSC`/`HSC`, `f0` in Hz, `db` gain, `q`, `on` as 0/1. Sent for every band on connect, and again whenever something changes the EQ on the PC. |
| `eqp <i> <param> <value>` | D→H | One EQ parameter moved. `param` ∈ `gain freq q kind on`; `value` is a float (or the kind token). The host clamps, applies and echoes nothing back — same non-echo rule as `vol`. |
| `eqflat` | D→H | Reset every band to 0 dB and off. |
| `btn <n> <0|1>` | D→H | An aux key changed state. V2 reports it **momentary** (1 on press, 0 on release), not V1's latch, because the keys it is used for are transport keys. |

**Why levels are a separate verb from `level`.** `level` is a *volume* (where
the knob sits); `meter`/`ameter` are *amplitudes* (what is coming out). They
move at completely different rates and only the first is state.

**`meter` is the only source of level, and silence is a real answer.** The V2
device does not synthesise amplitudes: with no `meter` arriving it shows flat
meters, and it returns to flat about a second after the last one. A host that
stops streaming therefore reads as "nothing is playing", not as a frozen panel.
The 8 bands per column are a *shape* invented on-device from the one amplitude
the wire carries — never a second source of level.

**Level scaling.** Values are `peak ** 0.45 × session volume × 100`.

The gamma is there because WASAPI peak is linear amplitude and speech and music
sit low enough in that range to look dead on a small panel
(`appmeter.AppMeters(curve=1.0)` disables it). The session volume is there
because **the peak is the raw stream**: measured, it does not move when either
the session or the endpoint volume changes, so a meter built on the peak alone
would sit still while the user turns the knob. Muted sessions report 0.

Note the order — curve on the peak, fader multiplying the result. The curve is
meant to lift quiet material into view, not to compress the knob; running the
fader through it too would make half volume read as 0.73 of full height.

The **system** volume is deliberately excluded: ch 3 changes how loud the
endpoint is, not how loud any one app is, so it must not move the per-app
meters.

**Spectrum is not available.** WASAPI exposes per-session *peak*
(`IAudioMeterInformation`) but no per-session tap — loopback captures the
endpoint mix. The device's 8-band display is therefore a fake spread of the one
amplitude per app, generated on-device; the wire only ever carries one number
per source.

**Channel 3 is the system endpoint, for mute as well as volume.** Clicking the
system knob sends `mute 3 <0|1>` and the host applies it to the default output
device, not to a session — symmetric with `level 3` / `vol 3`.

**Who owns the column names.** The host, except where the device pinned one
with `pick`. Without the pin, a hand-picked column would be overwritten the
next time the most-active-sessions list changed.

**A pin outlives everything.** It is stored on the host (`%LOCALAPPDATA%
\naub\pins.json`) and pushed back on every connect, so a column keeps its app
across a device reload — which happens on every deploy — across a bridge
restart, and across the app itself being closed. A pinned app that is not
running shows its column with nothing to control; relaunch it and the column
picks it straight back up. It does **not** reappear in the switcher's list
while it is gone: assigning something is a statement about that column, not a
reason to keep offering a dead app.

**One app, one column.** Assigning an app that another column already holds
swaps the two, rather than showing it twice. Two columns driving one session's
fader would fight, and whichever lost would look broken. The device does the
swap on its own display the instant you confirm, and the host does the same to
its pins; they agree because both follow the same rule, not because one waits
for the other.

### What the device offers, and what the host reports

`apps` is a statement of fact from the host: these sessions are streaming right
now. It is **not** the switcher's list. The device keeps that itself
(`device/wheel.py`), on two rules:

* an app must be audible for a few seconds **in one run** before it earns a
  place — so a notification chime, which is one to three seconds and does not
  come back, never appears as something you could assign a knob to;
* once earned, the place is held for ten minutes after the app goes quiet — so
  pausing a track, or closing the thing that was playing, does not take the
  entry away before you have finished with it.

This lives on the device on purpose. The host would have to keep the same
timers and stream the same conclusions, and the host is somebody's PC doing
other work; the panel has a whole core idle between frames. It is also why
`ameter` keeps flowing at 5 Hz with the switcher shut: those are the samples the
timers run on.

### Media transport is HID, not this protocol

The three bottom keyswitches (BTN3/4/5 → `|<<`, `>||`, `>>|`) are sent as **USB
HID Consumer Control** usages — `SCAN_PREV 0xB6`, `PLAY_PAUSE 0xCD`,
`SCAN_NEXT 0xB5` — exactly as §1 says system volume is. They therefore work
with **no host software running at all**, on the lock screen, in a browser, in
a game, and whichever app Windows currently considers the media session picks
them up. `btn 3|4|5` still goes out on the wire, but purely as telemetry: a
host that ignores it loses nothing.

CircuitPython exposes the Consumer Control endpoint by default and its report
is just the usage ID as a little-endian `uint16`, `0` meaning released, so
`firmware/v2/device/media.py` needs no HID library. Verified end to end with a
`WH_KEYBOARD_LL` hook: all three arrive as `MEDIA_PREV_TRACK` /
`MEDIA_PLAY_PAUSE` / `MEDIA_NEXT_TRACK`.

### EQ: why the parameters go over the wire and the filtering does not

naub implements no DSP. Equalizer APO is already a parametric EQ inside the
Windows audio pipeline, so the host writes it a config file and the device is a
control surface for it. `eqp` carries a parameter change; the host renders it
into a filter line; Equalizer APO does the arithmetic on the audio.

The device draws the curve itself, from the same RBJ cookbook coefficients
Equalizer APO implements — so the plot is the response the audio actually gets,
not an illustration of one. That is also why `eqcaps` carries the **sample
rate**: the curve is evaluated on the unit circle, so it is only truthful at
the rate the endpoint is running.

Values are sent in natural units (Hz, dB, Q), not as 0..100 knob positions like
`vol`. A knob position would force both ends to agree on a mapping curve, and
frequency and Q are log-mapped, so the device owns that mapping and the wire
stays unambiguous.

### What V2 does *not* carry over from V1

Two V1 verbs are deliberately unimplemented on V2, because the new UI does the
same job better:

- **`icon` / `iconclear`.** V1 pushed a 32×32 1-bpp icon per channel over the
  wire. V2 bakes every app's icon into `demos/switcher_data.py` at build time,
  FRC-encoded per size tier, so the switcher can blit it as a byte-slice copy.
  Pushing the same data over CDC would be ~3 kB per app and slower to draw.
- **`cycle <ch> <dir>`.** V1's hold-and-turn source reassignment is replaced by
  the app switcher: click a knob, scroll the shelf, click to assign, and the
  device reports the result as `pick`. The host no longer has to own a source
  list and drive the choice blind.

Everything else V1 spoke — `hello`, `ping`/`pong`, `label`, `level`, `mute`,
`vol`, `btn`, the banner — V2 speaks. `eq`/`eqset` (§10) are defined and the
mode bar has a slot for them; the V2 EQ screen itself is not built yet.
