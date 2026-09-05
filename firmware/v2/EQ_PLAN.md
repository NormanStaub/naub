# Parametric EQ on naub — what it would take

Scope: turning the reserved **EQ** mode into a working parametric equaliser.
Everything below is measured on this machine and this board, not estimated;
where a number appears, the command that produced it is named.

## The short answer

**Don't write DSP.** Equalizer APO is already a parametric EQ engine running
inside the Windows audio pipeline. naub's job is to be a *control surface* for
it: the host writes a small text file, Equalizer APO hot-reloads it, and the
device draws the curve and moves the parameters.

That reduces the work to three pieces, none of them signal processing:

| piece | where | rough size |
|---|---|---|
| N-band parametric config writer | host, extend `naub_host/eq_windows.py` | ~150 lines |
| four protocol verbs | both sides | ~80 lines |
| EQ screen: curve + band editing | device, new `eq.py` | ~200 lines |

The V1 host already does the **three-band fixed** version of exactly this, so
this is an extension of working code rather than a new subsystem.

---

## 0. Status — what is built

Everything except the screen itself, which is waiting on a layout:

| piece | where | state |
|---|---|---|
| N-band parametric config writer | `host/eqapo.py` | **done**, engine-status check included |
| curve maths + knob mappings | `device/eqcurve.py` | **done**, validated to 1e-11 dB against numpy |
| protocol verbs | PROTOCOL.md §11, `link.py`, `naub_bridge.py` | **done**, pushes on connect |
| EQ screen layout and controls | `device/eqscreen.py` | **done**, from the sketch |

Remaining before it makes a sound: Equalizer APO has to be attached to the
output actually in use (§2). The screen reports itself inert until then, which
is the honest state rather than a curve that shapes nothing.

The two host-side pieces are testable without the device:
`python host/eqapo.py` reports the engine status and the config it would write;
`--check` proves an install/uninstall cycle leaves `config.txt` byte-identical.

## 1. What already exists

- **`host/naub_host/eq_windows.py`** — `GlobalEq`: writes `naub-eq.txt`, adds a
  single guarded `Include:` line to Equalizer APO's `config.txt`, restores its
  state on restart, and no-ops harmlessly when Equalizer APO is absent. Three
  fixed bands (low shelf 120 Hz, peak 1 kHz Q 0.9, high shelf 4 kHz), ±12 dB,
  gain only.
- **`host/eq_test.py`** — drives it from the CLI with no device attached, so
  the audio path can be proven on its own.
- **PROTOCOL.md §10** — `eq` (D→H) and `eqset` (H→D), already `proto=2`.
- **The V2 mode bar** already reserves an `EQ` slot; flipping to it today moves
  the ticks and leaves the mixer up.

## 2. What is actually on this machine

Probed via the registry and the config directory:

- **Equalizer APO 1.4**, installed at `C:\Program Files\EqualizerAPO`, config in
  `…\config`.
- **It is now attached to the current output, and proven to work.** It began
  attached only to *BESTISAN SR01 Stereo*, a Bluetooth speaker whose
  `DeviceState` was `NOTPRESENT`, while the actual default output had no APO at
  all — so writing filters did precisely nothing. Adding *Speakers (High
  Definition Audio Device)* in the Configurator fixed it.

  Both states were measured the same way rather than inferred: play noise,
  capture the endpoint loopback, write a −24 dB notch at 1 kHz, capture again.

  | | change at 1 kHz |
  |---|---|
  | before, APO on an absent device | **+1.02 dB** — nothing |
  | after, APO on the current output | **−17.57 dB** |

  So two things are settled that the documentation does not state outright:
  **Equalizer APO reloads a written config without any restart**, and **the
  WASAPI loopback tap sits after the APO** — which is what makes the EQ
  screen's meter genuinely *post-EQ*, as intended, rather than merely post-mix.

  `eqapo.status()` checks attachment against the endpoint *currently in use*,
  not merely whether Equalizer APO is installed, and names the device to add
  when it is not. (Matching those two names is fiddly: the registry holds the
  device name, `pycaw` reports the endpoint name, and one is embedded in the
  other at a non-zero offset — comparing truncated prefixes silently fails on
  exactly that case.)
- **`config.txt` is yours**: a 20-filter PEQ export (`Filter 1: ON PEQ Fc 22.0
  Hz …`, currently all 0.00 dB) plus an active `Include: pce_chnl.txt` holding
  your channel routing (`Copy: RF=-1*R`, …). naub must never own or rewrite
  this file — only append one line to it.
- **VB-Audio Virtual Cable and VoiceMeeter VAIO are installed.** That matters
  for the per-app question in §7.

## 3. What "parametric" adds

Today only **gain** varies, on three fixed filters. Parametric means each band
carries:

| parameter | range | notes |
|---|---|---|
| type | PK / LSC / HSC / LP / HP / NOTCH | Equalizer APO's own filter names |
| frequency | 20 Hz – 20 kHz | log-mapped to the knob |
| gain | ±12 dB (configurable) | ignored by LP/HP |
| Q | 0.3 – 10 | log-mapped |
| enabled | on/off | so a band can be parked without losing its settings |

…and the band *count* becomes a parameter too. **Five bands** is the
recommended default: it covers real tone shaping, and §6 shows why more starts
to hurt the device.

## 4. Engine choice

| option | verdict |
|---|---|
| **Equalizer APO** (recommended) | Zero DSP to write. Real biquads inside the audio engine, so no added device, no re-pointing apps, no measurable latency beyond its own processing. Config is text and hot-reloaded. Already installed and enabled here. Costs: global only, and the user must have it. |
| Write our own APO | Full control, but a C++ MMDevice APO means COM registration, driver signing, and living in the audio path where a crash kills all sound. Weeks, not days, and a support burden forever. |
| Virtual device + our own DSP | VB-Cable / VoiceMeeter are already here, so this is *possible*: route an app to a virtual device and process it. But it adds latency, changes default devices, and every app has to be pointed at the right output by hand. |
| Per-app EQ | Not natively possible — Windows exposes no per-session filter. It requires the routing above, per app. See §7. |

**Recommendation: Equalizer APO.** It is the only option where the "performant"
part is free — the filtering already happens in an optimised APO, and naub
never touches a sample.

## 5. Host side

### 5.1 The file we own

Generalise `GlobalEq` from three fixed filters to N parametric ones:

```
# naub parametric EQ (managed by the naub host) - do not hand-edit
Preamp: -6.0 dB
Filter: ON PK  Fc 60 Hz    Gain 6.0 dB  Q 0.9
Filter: ON PK  Fc 250 Hz   Gain -4.0 dB Q 1.2
Filter: OFF PK Fc 1000 Hz  Gain 0.0 dB  Q 1.4
```

Three things the current writer does not do and should:

- **Preamp.** Boosting can clip, especially stacked on your existing 20-filter
  PEQ. Write `Preamp: -(sum of positive gains)` — conservative but never
  clips. A tighter estimate is the true peak of the summed response; the
  conservative one is one line and inaudible in practice.
- **Atomic writes.** It currently writes in place, so Equalizer APO's file
  watcher can observe a half-written file. `write to .tmp` then `os.replace`
  makes the swap atomic.
- **`OFF`** for disabled bands, so a parked band keeps its settings.

### 5.2 Where the `Include:` goes — this is a real decision

Equalizer APO applies lines **in order**. The current code appends at the end
of `config.txt`, which puts naub's EQ *after* your 20 PEQ filters and after
`Include: pce_chnl.txt`. So naub shapes the final output including your channel
routing — usually what you want for a tone control.

If you would rather naub sit *before* your room correction, the include line
has to move up. Make the position a setting rather than a silent default; the
guarded marker already makes the line findable.

### 5.3 Rate limiting — the one performance trap

Writing our file costs **0.26 ms median, 4.6 ms worst** (288 bytes, measured
over 20 writes). Cheap for us — but every write makes Equalizer APO re-parse
`config.txt` *and every include*, which here means your 20 filters plus the
routing, then rebuild its filter graph.

A knob at one detent per ~10 ms would trigger that 100×/s. So:

- apply the change to in-memory state immediately (the device UI is driven from
  that, not from the file);
- write at most every ~50 ms;
- **always write the final value** after the knob stops, so a fast spin cannot
  leave the file behind the display.

## 6. Device side

### 6.1 Drawing the curve — built and measured

`device/eqcurve.py`. The curve is the summed magnitude response of N biquads at
the 128 screen columns, evaluated analytically on the unit circle with the trig
precomputed once per column — so a redraw is multiply-add per band per column,
plus one logarithm per column at the very end (magnitudes are kept *squared*
throughout so the log happens once, not once per band).

Measured with the real module on the RP2350, five bands:

| operation | cost |
|---|---|
| construct, incl. trig tables | 9.8 ms, once |
| recompute one band | 4.9 ms |
| re-multiply the bands | 5.2 ms |
| `heights(40)` | 3.1 ms |
| **one knob detent** (`set` + `heights`) | **13.0 ms** |
| rebuild all five from scratch | 29.7 ms |

So a detent costs 13 ms — about 77 updates a second, comfortably ahead of any
knob — because **only the band that moved is recomputed**. Each band keeps its
own magnitude array and the screen curve is their product; a rebuild of all
five would be 33 ms and would visibly lag a fast spin.

Two further economies are in the code: bands that are off or flat are the
identity and are skipped entirely rather than multiplied in as 128 ones (the
common case is one or two bands live), and `heights()` is separate from `set()`
so the screen can defer drawing while a knob is still moving.

Correctness matters as much as speed here — a wrong shelf looks perfectly
plausible on a 40 px plot. `tmp/check_eqcurve.py` checks the module against an
independent numpy evaluation of the same transfer function: peaking bands match
to **2e-11 dB**, shelves reach their asymptotic gain, multiple bands sum in dB,
and an incremental update is bit-identical to a from-scratch rebuild.

### 6.2 Layout

The EQ mode owns the same band the meter and the switcher use (pages 2–6,
40 px), with the mode bar and volume bars untouched, so it costs no new
geometry:

```
| ####----- | ######--- | ########## |  volume bars stay put      page 0
|  60Hz  +6.0dB  Q0.9        BAND 1  |  the selected band, in numbers  page 1
|      ___                           |
|  ___/   \___         ___           |  the curve, +/-12 dB full scale
| /            \_____/     \____     |  pages 2-6
| o o O o o                          |  which band is selected
| EQ |||             SYS. #########  |  mode bar stays put        page 7
```

The band markers reuse the drawer's counter, and the numbers row reuses the
names bar. The only genuinely new drawing is the curve itself.

### 6.3 Controls

Consistent with the mixer, and with no new gestures to learn:

| input | action |
|---|---|
| ENC1 turn | frequency (log) |
| ENC2 turn | gain |
| ENC3 turn | Q (log) |
| ENC1 click | next band |
| ENC2 click | flatten this band (gain → 0) |
| ENC3 click | toggle this band on/off |
| ENC4 | system volume / mute, unchanged everywhere |
| BTN1 / BTN2 | previous / next mode, unchanged |
| BTN3-5 | media transport, unchanged |

## 7. Per-app EQ

Not possible natively: Windows has no per-session filter, and the peak meter we
already use is the only per-session hook exposed. It *is* achievable with the
software already on this machine — route an app to VoiceMeeter or a VB-Cable
instance and give that endpoint its own Equalizer APO config — but it means
every EQ'd app must be pointed at a virtual device by hand, latency goes up, and
the default-device dance gets fragile.

**Recommendation: stay global**, which is the decision already recorded for this
project. If per-app EQ ever becomes a must, the honest design is "naub drives
VoiceMeeter", not "naub grows a DSP engine".

## 8. Protocol

`eq <band> <0..100>` carries gain only. Parametric needs more. Suggested
additions, in the same spirit as the existing verbs (additive, ignorable):

| message | dir | meaning |
|---|---|---|
| `eqcaps <nbands> <fmin> <fmax> <maxdb>` | H→D | on connect: what the engine offers, so the device hard-codes no ranges — and its absence means "no EQ engine", which the device can say on screen |
| `eqp <band> <param> <value>` | D→H | one parameter moved; `param` ∈ `gain freq q type on` |
| `eqband <band> <type> <freq> <gain> <q> <on>` | H→D | full band state, for seeding on connect and for reflecting changes made on the PC |
| `eqflat` | D→H | reset everything |

Keep `eq` / `eqset` exactly as they are for the three-band path.

**Encoding:** gain `0..100` → ±maxdb as today; frequency and Q as `0..100` on a
**log** scale, because a linear knob across 20 Hz–20 kHz is unusable — the
bottom four octaves would live in the first two detents.

## 9. Risks and prerequisites

- **Equalizer APO must be installed and attached to the endpoint.** Installed
  is not enough; the Configurator attaches it per device. Detect it (the
  registry key already gives this) and have the EQ mode say so rather than
  silently doing nothing.
- **`config.txt` belongs to the user, down to the byte.** Append one guarded
  line, never rewrite, and be able to take it back out exactly.

  Worth stating plainly because the first attempt got it wrong: the include
  block was appended with a decorative leading blank line but removed by
  filtering the two content lines, so **every install/uninstall cycle grew the
  user's file by one newline**. Six had accumulated (1537 → 1549 bytes) before
  it was caught, and the file was restored by hand. `eqapo.py` now appends a
  verbatim block, removes that exact block, preserves the file's CRLF endings,
  and `python host/eqapo.py --check` asserts three full cycles leave it
  byte-identical.
- **Boost + an existing PEQ can clip.** Hence the preamp in §5.1.
- **Re-parse cost grows with the user's own config**, not ours — 20 filters
  here. Debounce as in §5.3.
- Sample-rate and device changes are Equalizer APO's problem, not ours; the
  file is rate-independent.

## 10. Suggested order of work

1. `eq_windows.py` → N parametric bands, atomic write, preamp, debounce.
   Provable on its own with `eq_test.py`, no device needed.
2. Protocol verbs + host wiring; the device can stay a passive display.
3. Device EQ screen: curve first (static bands), then editing, then the
   per-band incremental recompute from §6.1 if it needs it.

Each step is independently testable, and step 1 is audible on its own.
