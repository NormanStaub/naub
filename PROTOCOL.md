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

- Channels are indexed **0–3** (four encoders). Hardware V1 has encoders 1 and 2
  populated → channels **0** and **1** are live; 2 and 3 exist in the protocol and
  on the display but never move until wired.
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
| `vol <ch> <0..100>` | A knob moved. Emitted **live, per detent** (every value change), so host audio tracks the knob smoothly. `<ch>` 0–3, value 0–100 integer. |
| `mute <ch> <0\|1>` | An encoder was **pressed** (press = mute toggle). Reports the *new* mute state for that channel. |
| `btn <n> <0\|1>` | Aux button `n` toggled to the given latch state. V1 firmware emits `btn 0 <s>` for Button 1. |
| `pong` | Reply to `ping`. Liveness only. |

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
load → grayscale → resize to 32×32 → dither to 1-bpp (Floyd–Steinberg gives the
most legible result on e-paper) → pack → Base64.

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
  tracks the knob immediately (cheap WASAPI call). The *display* still commits
  on settle with the dithered preview (the project's e-paper UX decision) — that
  is purely device-local rendering and independent of when `vol` is sent.

---

## 9. Reserved / future

- `btn <n>` is defined for n≥0; V1 uses only `btn 0`.
- A periodic `state …` snapshot line (full vector, deej-style) is intentionally
  **not** in v1 — `hello` covers resync. Add it under `proto=2` if a heartbeat is
  ever wanted.
- HID system-volume lives on its own interface and never appears here.
