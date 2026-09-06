# Porting the naub host to macOS — feasibility and requirements

Written 2026-09-06 against `firmware/v2/host/` at `feat/v1-firmware-host` (6c3956e).
This is a study, not a plan of record, and **no port code was written**.

Version baseline: the shipping release is **macOS 26 Tahoe**; macOS 27 is expected
within weeks. Everything below assumes Apple silicon. Where a claim depends on a
specific macOS version I say so, because this area changed materially at 14.2
(process taps), 14.4 (the audio-capture TCC category), 15.0 (Sequoia's recurring
screen-capture consent; ACE→ARK) and 15.2 (small-FAT write performance).

---

## 1. Executive summary

**Partly.** The transport, the deploy path, the bridge loop, the system/master
volume and the build-time asset tooling all port cleanly — about a week of work,
no external software. Everything that makes naub *a mixer* does not port cleanly,
and three findings decide the whole thing.

**1. The free per-app peak meter does not exist on macOS, and its absence
inverts the project's core design principle.** Windows hands you
`IAudioMeterInformation::GetPeakValue()` for ~0.05 ms per session — a real
amplitude trace of one app, free, because the OS mixer already computed it. macOS
has no equivalent at any level. I checked the SDK headers rather than the blogs:
a Core Audio **tap object** exposes exactly three properties (`UID`,
`Description`, `Format`) and a **process object** exposes exactly five (`PID`,
`BundleID`, `Devices`, `IsRunning`, `IsRunningOutput`). None of them is a level.
The only way to meter an app on macOS is to **capture its PCM and compute the
peak yourself** — one Core Audio process tap per app, an aggregate device, and a
real-time IOProc. `README.md`'s "What runs where" table says the host is
"somebody's PC doing other work" and pushes work onto the device wherever it can.
The macOS meter forces the opposite trade, and it is not a trade you can decline.

**2. Per-app volume and mute — what naub's knobs are *for* — has no macOS API,
and never has.** There is no `ISimpleAudioVolume`. The only mechanism that works
in 2026 is: tap the process with `CATapMutedWhenTapped` so its direct path to the
hardware is silenced, then re-render your own gain-scaled copy out to the real
device. That is not a control API; **that is writing an audio mixer**, with a
real-time callback that must not allocate, must not lock, and must not touch the
GIL. It cannot be CPython. A macOS naub that keeps its volume knobs must ship a
signed native (Swift or C) helper that Python talks to over a pipe. This single
item dominates the total.

**3. There is no Equalizer APO, so the file-writing simplicity that keeps
`eqapo.py` small has no direct equivalent — but there is one unexpectedly good
lead.** macOS provides no system-wide insertion point in the audio path at all
(which is *why* APO has no port). The best option found is **FineTune** (GPL-3,
macOS 15+, actively released): a free tap-based mixer whose AutoEQ loader already
**parses Equalizer APO `ParametricEQ.txt` files** — `Preamp:` lines,
`Filter: … Fc … Gain … Q …` lines, PK/LSC/HSC — the exact format
`eqapo.render()` emits. It has no hot-reload watcher and no EQ action in its URL
scheme, but both are small upstreamable patches to a GPL project. If that pans
out, `eqapo.py` becomes a path change plus a reload trigger rather than a
rewrite. If it does not, every remaining option makes naub a DSP vendor.

There is a fourth thing worth naming: **consent**. On Windows naub asks the user
for nothing. On macOS, metering one app requires a TCC grant under a pane Apple
labels **"Screen & System Audio Recording"** — and because a Terminal-run Python
script has no code identity of its own, the grant is attributed to
**Terminal.app**, not to naub. You would be asking a user to give their shell
blanket system-audio-recording rights in order to turn a volume knob. That is
materially worse than the Windows story, and it pushes hard toward shipping a
signed `.app` ($99/yr Apple Developer Program).

**Bottom line.** A macOS naub that does everything the Windows one does is
**8-14 weeks of work, and requires a native signed helper component, an Apple
Developer membership, a TCC audio-capture grant, and a third-party app for EQ.**
A macOS naub that does the *display*, the *transport*, the *system volume*, the
*deploy* and the *spectrum* is **1-2 weeks and needs none of that**. The gap
between those two is almost entirely items 1 and 2 — and they are the same
engine, built once.

---

## 2. Capability matrix

Effort assumes one developer already fluent in this codebase but **not** in Core
Audio. "External software" means something the *end user* must obtain.

| # | Capability | Windows today | macOS route | External software the user needs | Effort | Confidence |
|---|---|---|---|---|---|---|
| 1a | **Per-app peak metering** | `IAudioMeterInformation::GetPeakValue()`, free, ~0.05 ms/session | **No cheap peak exists.** One process tap per app (macOS 14.4+) in one private aggregate device; peaks computed in a native IOProc | None as such, **but** a TCC "System Audio Recording" grant; realistically a signed `.app` | **1-2 wk** on `catap`/`audiotee`; **3-4 wk** own helper | High that no cheap peak exists; medium that taps scale to ~30 apps |
| 1b | Grouping sessions per program (the Discord fix) | pid → `psutil` name → group, max peak | `kAudioProcessPropertyBundleID` straight off the process object — **better than Windows**; browsers/Electron still render from helper processes | None | 0.5 d | High |
| 2 | **Per-app volume + mute** | `ISimpleAudioVolume` on every session in the group | **No API.** Tap with `CATapMutedWhenTapped` + aggregate + re-render with gain and a ~30 ms ramp. Real-time callback ⇒ **cannot be CPython** | None if self-hosted (signed helper); or drive **FineTune** (free) / **SoundSource** ($49) instead | **+2-3 wk** on top of 1a; or **3-5 d** to drive FineTune's URL scheme | High that no API exists; medium that a self-built tap mixer is robust |
| 3 | **System/master volume + mute** | WASAPI endpoint volume via pycaw | `AudioObjectSetPropertyData` + `kAudioDevicePropertyVolumeScalar` / `…Mute`, fallback `kAudioHardwareServiceDeviceProperty_VirtualMainVolume`, via ctypes on CoreAudio.framework. **No TCC, no deps** | None | **1 d** | High |
| 4 | **Global parametric EQ** | Write `naub-eq.txt` + one guarded `Include:` in Equalizer APO's `config.txt` | (a) **FineTune** — already parses Equalizer APO `ParametricEQ.txt`; needs an upstream patch for hot-reload / an EQ URL action. (b) **SoundSource** `Set Effect Parameter` via `shortcuts run`. (c) ship a HAL plugin with our own DSP | (a) FineTune, free GPL-3; (b) SoundSource, **$49**; (c) none, but we become a DSP vendor | (a) **1 wk** incl. upstream patch; (b) **3-5 d**; (c) **4-8 wk** | Medium — (a) rests on one unrun experiment |
| 5 | **Spectrum analyser input** | `pyaudiowpatch` WASAPI loopback, started on demand | (a) global tap (`initStereoGlobalTapButExcludeProcesses:`) — no extra install, but needs the TCC grant and a native helper; (b) **BlackHole** + `sounddevice` — trivial code, but the user installs a driver *and* re-points their default output | (a) none; (b) BlackHole (free, GPL-3, signed pkg) | (a) **free** if 1a is built; (b) **2 d** | High |
| 6 | **App enumeration + icons** (build-time) | PowerShell `Get-Process` + `ExtractAssociatedIcon` | `NSWorkspace.runningApplications()`, filter `activationPolicy == regular`, `NSRunningApplication.icon()` → TIFF → PNG. `pyobjc-framework-Cocoa` only | None | **1 d** | High |
| 7 | **Font rasterisation** (build-time) | GDI, to reach `.FON` **raster** faces | Not needed — `device/ccfont.py` is generated and committed. If wanted: **`freetype-py`, not CoreText** (see §3.7; I found and verified a wrong premise in `gdifont.py`) | None | **0 d** as-is; **2-3 d** for a `freetype-py` rewrite | High (re-tested first-hand) |
| 8 | **Serial transport** | pyserial, VID 2E8A, sort by `MI_xx` / `location` | pyserial works; `/dev/cu.*` not `/dev/tty.*`; **`find_ports()` is silently broken on macOS** — §3.8. Match `port.interface` containing `" CDC2 "` | None (CDC-ACM is a class driver). MacBooks prompt once to allow the accessory | **0.5-1 d** | High |
| 9 | **Firmware deploy** | `shutil.copy` to `K:\`, code.py last | `/Volumes/CIRCUITPY` + `boot_out.txt` probe. **But BRINGUP_LOG.md §2 records this board never producing a block device on macOS** — the repo already ships `bench/deploy_repl.py` as the REPL fallback | None | **1 d** (probe + REPL fallback + autoreload guard) | High |
| 10 | **State/config file** | `%LOCALAPPDATA%\naub\pins.json` | `~/Library/Application Support/naub/pins.json` | None | **1 h** | High |
| 11 | **Packaging / distribution** | `pip install` + `python naub_bridge.py` | Same *only* for the reduced-scope build. Anything touching audio capture realistically needs a signed, notarised `.app` | Apple Developer Program, **$99/yr** — ours, not the user's | **1-2 wk** first time | Medium-high |
| 12 | The bridge loop itself | `naub_bridge.py`, 615 lines | Portable as-is; 3 Windows-ish lines. **But** §3.12 — the loop runs *faster* on macOS, and App Nap will throttle it | None | **0.5 d** | High |

**The two items that dominate are 1a and 2, and they are one piece of work:** a
signed native process-tap helper. Everything else in this table adds up to
roughly two weeks.

---

## 3. Detailed findings

### 3.0 What actually has to change (grounding)

`naub_bridge.py` never imports a Windows module. The platform surface is four
files and two constants:

| file | lines | Windows dependency | verdict |
|---|---|---|---|
| `naub_bridge.py` | 615 | `%LOCALAPPDATA%` (1 line); `MI_xx` regex in `_iface()` | shim |
| `appmeter.py` | 402 | comtypes/pycaw/WASAPI; hand-declared `IAudioMeterInformation` | **rewrite, plus a native helper** |
| `eqapo.py` | 466 | `winreg` + Equalizer APO's config format | rewrite or retarget |
| `sysmeter.py` | 203 | `pyaudiowpatch`, only in `start()`/`_loop()` — the FFT and band math are numpy | ~40 lines swapped |
| `appscan.py` | 43 | PowerShell ×2 | small rewrite, build-time |
| `gdifont.py` | 92 | win32ui/win32gui | build-time only; not needed to run |
| `deploy.py` | 116 | drive `K:`, `COM9` | trivial |

Two pieces of prior art in the repo matter. `host/naub_host/__init__.py` (the V1
host) already says the OS audio and icon code is isolated "so Linux (PipeWire)
and macOS adapters can slot in alongside later" — the seam exists and was
designed for. And `WRITEUP.md` §4 already called it: *"macOS is the hard one —
per-app output volume needs a virtual audio driver, which is the bulk of the
macOS effort."* That judgement was right in substance. Process taps have changed
the *mechanism* (no driver needed any more) without changing the *cost*.

### 3.1 Per-application audio metering — the crux

**Core Audio process taps.** `AudioHardwareCreateProcessTap` /
`AudioHardwareDestroyProcessTap` are annotated `API_AVAILABLE(macos(14.2))` in
`CoreAudio.framework/Headers/AudioHardwareTapping.h`
([SDK mirror](https://raw.githubusercontent.com/alexey-lysiuk/macos-sdk/main/MacOSX26.5.sdk/System/Library/Frameworks/CoreAudio.framework/Versions/A/Headers/AudioHardwareTapping.h),
[Apple](https://developer.apple.com/documentation/coreaudio/audiohardwarecreateprocesstap(_:_:))).
`CATapDescription` is annotated `macos(12.0)` because the *class* predates the
feature — ignore that; the function is 14.2.

- **Per-process: yes**, but you target `AudioObjectID`s, not PIDs directly.
  `kAudioHardwarePropertyTranslatePIDToProcessObject` converts.
- **One tap = one *mixed* stream.** The header's own class comment: *"The input
  stream is a mix of all of the specified processes output audio."* You cannot
  de-interleave 30 apps from one tap. Setting `mixdown = false` does not split
  per process — it requires a `deviceUID` + `stream` and gives you the *device's*
  channel layout. So **30 apps means 30 taps.**
- The saving grace: one private aggregate device can host all of them and
  delivers *"exactly one IOProc buffer per tap, in tap-list order"* (confirmed
  empirically on macOS 26.2 by
  [catap's core-audio-notes](https://github.com/sbetko/catap/blob/main/docs/core-audio-notes.md)).
  One callback, N buffers — the right architecture.
- **Mute behaviour** (`CATapMuteBehavior`): `CATapUnmuted` (0 — captured *and*
  still heard, which is what metering wants), `CATapMuted` (1), and
  `CATapMutedWhenTapped` (2), which is the key to §3.2.
- **macOS 26 additions**: `CATapDescription.bundleIDs` and `processRestoreEnabled`
  — a tap that re-attaches by bundle ID across an app restart. Genuinely useful
  for naub's pinned columns, which are explicitly designed to survive an app
  closing and relaunching.

**Permissions.** `NSAudioCaptureUsageDescription` in Info.plist, macOS 14.2+
([Apple](https://developer.apple.com/documentation/bundleresources/information-property-list/nsaudiocaptureusagedescription)).
Apple: *"The first time you start recording from an aggregate device that
contains a tap, the system prompts you to grant the app system audio recording
permission"*
([Capturing system audio with Core Audio taps](https://developer.apple.com/documentation/CoreAudio/capturing-system-audio-with-core-audio-taps)).
The TCC service is `kTCCServiceAudioCapture` — **not** screen recording — but it
surfaces in the pane Apple renamed to **"Screen & System Audio Recording"**, and
the "System Audio Recording Only" sub-permission arrived in **macOS 14.4**
([OBS #10401, 2024-03-18](https://github.com/obsproject/obs-studio/issues/10401)).
The API is 14.2; the sane target is **14.4+**. There is **no public API to query
or request** the grant — Apple's own sample,
[AudioCap](https://github.com/insidegui/AudioCap), resorts to private TCC SPI
just to check it.

Two failure modes you will hit, both silent:
- **Missing usage string ⇒ digital silence, not an error.** `noErr` and all-zero
  buffers ([DGR Labs, 2026-04-25](https://dgrlabs.co/blog/2026-04-25-capturing-system-audio-on-macos-in-2026.html);
  [Thunder Kitty](https://www.thunderkitty.app/learn/2000-buffers-of-nothing/)).
  This codebase already has a name for that class of bug — STATUS.md's *"Test the
  sound, not the drawing. The EQ drew correct curves for days while writing
  `Filter: OFF` for every band."*
- The prompt fires on **`AudioDeviceStart`**, not on tap creation. Reset with
  `tccutil reset SystemAudioCaptureRequests <bundle-id>` — note it takes a bundle
  ID, which a bare script does not have.

**No entitlement is required.** I found no `com.apple.developer.*` entitlement
gating taps, and `catap`'s README states it outright. Signing is a separate
question — §3.11.

**Can `python naub_bridge.py` do it?** Documented, and this is the one genuinely
encouraging permission finding. `catap`'s README: *"When you run from a terminal
… macOS attributes capture to the terminal app, so grant permission to Terminal,
iTerm, or whichever host is launching catap."* The Terminal-hosted CLI path
**works**, because Terminal is signed and holds the grant and your unsigned
script inherits it through TCC's attribution chain
([Eclectic Light](https://eclecticlight.co/2025/11/08/explainer-permissions-privacy-and-tcc/),
[Qt on the responsible process](https://www.qt.io/blog/the-curious-case-of-the-responsible-process)).
The case that fails is a *standalone unsigned `.app`*: two independent reports
say ad-hoc-signed apps with no Team ID are silently refused
([Cap #1722](https://github.com/CapSoftware/Cap/issues/1722),
[Apple forums 807323](https://developer.apple.com/forums/thread/807323)). Both
are about ScreenCaptureKit and are community reports, not Apple documentation —
treat as strong signal, not fact.

**Python bindings — better than expected.** `pyobjc-framework-CoreAudio` 12.2.2
(2026-08-11) has test coverage for `AudioHardwareTapping`, `CATapDescription`
(including macOS 26's `processRestoreEnabled`), `kAudioProcessProperty*`,
`AudioHardwareCreateAggregateDevice` and `AudioDeviceCreateIOProcIDWithBlock`
with a bound block signature. PyObjC's own apinotes still carry a stale *"I'm not
yet convinced that the API actually works correctly from Python"* caveat
([apinotes](https://pyobjc.readthedocs.io/en/latest/apinotes/CoreAudio.html));
the test suite contradicts it, but see §7.

Two off-the-shelf starting points:
- **[`catap`](https://github.com/sbetko/catap)** (PyPI, MIT, v0.6.0, 2026-08-25)
  — PyObjC for the control plane plus a small bundled ObjC dylib for the
  real-time IOProc, with multitrack N-taps-one-aggregate, live retargeting and
  drop reporting. Exactly the architecture naub needs. ~0 stars and unvetted, but
  its engineering notes are the best public documentation of tap behaviour found.
- **[`audiotee`](https://github.com/makeusabrew/audiotee)** (MIT, Swift) — a CLI
  writing PCM to stdout with `--include-processes` by PID. Usable as a helper
  binary from Python with no linking at all, which suits this project's taste.

**CPU cost.** The arithmetic is free: 30 taps at 48 kHz stereo float32 is
~11.5 MB/s and 2.88 M samples/s, which `vDSP_maxmgv` handles at roughly 0.1% of
one P-core. The cost is `coreaudiod` maintaining N real-time mix paths —
estimated at **0.2-1% of a core per active tap, so 5-30% of one core for 30
taps**. That is an engineering estimate; **no published measurement exists**, and
it is the number most worth measuring (§7). Two big mitigations: gate tap
creation on `kAudioProcessPropertyIsRunningOutput` (in practice 2-5 apps are
producing output, not 30), and compute the peaks *inside* the native callback so
Python reads N floats per 33 ms tick and does essentially nothing. Compare
honestly against Windows' ~0.05 ms per session for the same information.

**Known bugs, all unanswered by Apple, all relevant to a meter:**
- **Intermittent all-zeros after long uptime.** The IOProc keeps firing with
  correct timestamps and every sample is `0.0f`, for 60 s to 16 minutes,
  correlated with 44.1↔48 kHz renegotiation and Bluetooth state changes; the only
  cure is a full teardown of *both* tap and aggregate
  ([forum 825780, May 2026, macOS 26.5 beta](https://developer.apple.com/forums/thread/825780)).
  For naub this presents as **thirty dead meters and a panel that looks broken** —
  precisely the failure mode `Dropped` and `LIVE_S` were designed to make
  visible. Any port needs a watchdog: if every tap reads exactly zero while
  `IsRunningOutput` is set, rebuild.
- **Level attenuation scaling with output channel count** — −12.04 dB measured on
  a 4-stereo-pair interface, ~0 dB on built-in speakers
  ([forum 806799](https://developer.apple.com/forums/thread/806799)).
  Disqualifying for calibrated meters on multi-output interfaces without
  compensation.
- Default-device switches that change *format* kill the capture and it does not
  reconnect. `sysmeter.restart_if_dead()` already has the right shape for this.
- **`isExclusive` is a direction flag, not a lock** — flipping it inverts
  exclude-list into include-list and yields silence.
- **Maximum simultaneous taps: undocumented and unreported.** The biggest unknown
  for a design that meters every app in the switcher list.

**ScreenCaptureKit is the wrong tool.** Per-app audio has been possible since
**macOS 13** via an app-scoped `SCContentFilter` (macOS 15 added *microphone*
capture, not per-app audio — the brief's premise is slightly off here). But it is
one `SCStream` per app; **audio-only is not supported** (you must attach a video
output or the stream errors — the standard hack is 2×2-pixel frames at 0.1 fps,
[forum 718279](https://developer.apple.com/forums/thread/718279)); it demands the
full **Screen & System Audio Recording** grant; and it is subject to Sequoia's
recurring re-authorisation — weekly in beta, softened to **monthly** before
release, with no developer opt-out
([9to5Mac, 2024-08-14](https://9to5mac.com/2024/08/14/macos-sequoia-screen-recording-prompt-monthly/)).
Thirty video pipelines and a monthly nag, to move a meter. No.

**Is there a cheap peak anywhere?** No, and I want to be unambiguous because this
is the finding that shapes the product. Checked and ruled out: tap object
properties (UID/Description/Format only); process object properties
(PID/BundleID/Devices/IsRunning/IsRunningOutput only); `kAudioLevelControlProperty*`
and `kAudioDevicePropertyVolumeDecibels` (these are *gain controls* — the volume
knob — not meters); `kAudioQueueProperty_EnableLevelMetering` (meters *your own*
AudioQueue only). Every tool that shows per-app meters, SoundSource included, is
capturing PCM first. SonicFlow's TODO reads literally *"Not yet: real RMS meters
from the IOProc"* — i.e. it is DSP work, not an API call.

**One near-miss worth having.** `kAudioProcessPropertyIsRunningOutput` is a free
boolean: "this process has active output streams". It is not a level — a
playing-but-silent app reads 1 — but it is exactly the gate `appmeter.active()`
needs for the switcher list, and it costs nothing. Caveat: property *listeners*
for it reportedly never fire
([forum 770348](https://developer.apple.com/forums/thread/770348)), so poll it;
polling 30 objects at a few Hz is trivial.

**Virtual-device alternatives, for completeness:**

| Product | Type | Per-app separation | User must install | Licence / price | Automation |
|---|---|---|---|---|---|
| **BlackHole** | AudioServerPlugIn, no kext | **No** — it is a loopback cable | signed pkg, or `brew install blackhole-2ch` | GPL-3.0 | none |
| **Background Music** | BGMDriver HAL plugin + BGMApp | Yes — per-app volume applied inside the driver | driver install script, no restart | GPL-2.0+, 19.3k★, last push 2026-06-10, self-described alpha | **no IPC** — you would fork, not integrate |
| **eqMac** | own loopback driver + privileged helper | Yes, **Pro only** | driver install | Apache-2.0 but **public repo frozen at v1.3.2**; current work on a private branch. $3/mo, $30/yr, $40 lifetime | HTTP API listed "in development", not shipped |
| **Loopback** (Rogue Amoeba) | ARK | Yes | app + ARK, admin password | **$99** | none found |
| **Audio Hijack** | ARK | Yes | app + ARK, admin password | **$69** | JS API — but a block exposes only `name`/`type`/`disabled`, **not EQ parameters** |
| **SoundSource** | ARK | Yes, **plus per-app level meters** | app + ARK, admin password, two TCC grants | **$49**, macOS 15+ | Shortcuts, incl. `Set Effect Parameter` |

Rogue Amoeba's ACE system extension — the one with two admin prompts and a
restart — is **historical**. Their own KB: *"As of MacOS 15 (Sequoia), ACE is
fully deprecated… On MacOS 14 and higher, our newer Audio Routing Kit (ARK)
technology is used"*
([ACE legacy](https://www.rogueamoeba.com/support/knowledgebase/?showArticle=ACE-Legacy)).
ARK still needs an admin password plus System Audio and Microphone grants, and
shows the capture indicator dot
([ARK details](https://rogueamoeba.com/support/knowledgebase/?showArticle=Misc-ARK-Plugin-Audio-Capture-Details&product=SoundSource)).

**SoundSource is worth twenty minutes of anyone's time before designing this.**
It is per-app volume + EQ + per-app meters — commercial naub-without-the-hardware
— and its behaviour tells you what is actually achievable and what it costs the
user in prompts.

### 3.2 Per-application volume and mute

**Confirmed: no public API, in current macOS.** macOS 26 Tahoe did not add one;
Control Center's Sound module remains system-wide.

One trap, because it looks exactly like the API you want.
`kAudioHardwarePropertyProcessIsAudible` ('pmut') is settable and reads like
per-process mute. The `AudioHardware.h` comment: *"a non-zero value indicates
that the audio of the process will be heard"* — **applied to the calling process
only**. SFBAudioEngine's binding confirms the shape: `setProcessIsAudible(_:)`
takes no PID. It lets an app mute itself. It cannot mute Spotify. Similarly
`kAudioProcessPropertyDevices` is read-only, and there is no settable "put this
PID on that device" — with the additional wrinkle that it reportedly returns
wrong IDs when a process switches devices mid-stream
([forum 748257](https://developer.apple.com/forums/thread/748257)).

**What the real apps do.** Two architectures, and the industry has switched.

*(a) The old way — virtual HAL device that steals the default output.*
Background Music is the canonical open implementation: BGMDriver is a userspace
`AudioServerPlugIn` publishing a device pair; BGMApp **sets it as the system
default output**, so everything lands in the driver, which sees each client's
audio *before* Core Audio mixes it and applies per-app gain there; BGMApp then
plays the result out the real device
([DEVELOPING.md](https://github.com/kyleneideck/BackgroundMusic/blob/master/DEVELOPING.md)).
eqMac is the same pattern plus a `/Library/PrivilegedHelperTools` daemon. The key
point, easily missed: nobody moves individual apps to individual devices,
**because there is no API to do that**. They capture everything into one device
and attenuate per-client inside it.

*(b) The current way — process taps + aggregate + re-render.* Set
`muteBehavior = CATapMutedWhenTapped` so the app's direct path to hardware is
silenced, attach the tap as a *sub-tap* of a private aggregate whose main
sub-device is the real output, register an IOProc with
`AudioDeviceCreateIOProcIDWithBlock`, and multiply the samples by your gain
before rendering them out. Three independent projects do exactly this:
[thalesbmc's mixer](https://dev.to/thalesbmc/macos-still-has-no-volume-mixer-so-i-built-one-53lp)
(with a ~30 ms exponential ramp to avoid clicks),
[sonicflow](https://github.com/altuzar/sonicflow) (MIT, per-app gain in the
IOProc, lock-free ring buffer, **~10 ms latency**), and
[FineTune](https://github.com/ronitsingh10/FineTune) (GPL-3, macOS 15+, v1.9.0 on
2026-07-09, with gain ramping, crossfade on device switch, orphaned-tap cleanup
and a soft limiter).

**What this costs naub, honestly:**
- **It cannot be CPython.** A Core Audio IOProc is a real-time callback: no
  allocation, no locks, no Objective-C calls, no logging. Even `np.frombuffer`
  allocates. This is a signed Swift or C helper, with Python talking to it over a
  pipe or socket — a new build artefact, a new language in the project, and a new
  release process.
- **You are asking for a system-audio-recording permission in order to turn a
  volume knob.** There is no way to separate the two: the mute-and-re-render
  trick *is* capture.
- **~10 ms added latency** on everything the user hears.
- **Aggregate devices do not support volume**, so hardware volume keys and the
  system fader interact awkwardly with the re-rendered path — which matters here,
  because naub's channel 3 *is* the system fader.
- **Teardown and rebuild on every default-device change**, with a crossfade if you
  do not want an audible glitch. Both FineTune and thalesbmc carry explicit logic
  for this.
- **Browsers and Electron apps render from helper processes**, so "per app" means
  "per process tree". `appmeter.py`'s docstring already documents the Windows
  form of this problem (Discord holding six sessions) and its grouping fix
  transfers — but the grouping key becomes bundle ID, and BGM's release notes
  show per-browser special cases for Safari/Chrome/Edge/Brave/Arc/Whale.

**The cheaper alternative: don't build it — drive someone else's.** FineTune is
free, GPL-3, actively developed, and exposes a URL scheme covering
`set-volumes`, `step-volume`, `set-mute`, `toggle-mute`, `set-device`, `reset`
([url-schemes.md](https://github.com/ronitsingh10/FineTune/blob/main/guide/url-schemes.md)).
That moves the TCC grant, the signing burden and the real-time DSP onto an app
the user installs, and naub stays what it already is on Windows: a control
surface for someone else's engine. SoundSource is the paid equivalent
(`Set Source Volume` / `Set Source Mute` / `Set Source Device` via
`shortcuts run`). **This is the option I would price first**, because it preserves
the project's actual architecture instead of inverting it.

### 3.3 System/master volume and mute — the easy case

Get the default output with `kAudioHardwarePropertyDefaultOutputDevice`, then
`AudioObjectGetPropertyData` / `SetPropertyData` with
`kAudioDevicePropertyVolumeScalar` (scope `kAudioDevicePropertyScopeOutput`,
element `kAudioObjectPropertyElementMain`) and `kAudioDevicePropertyMute`.
**Gate every call on `AudioObjectIsPropertySettable`** — aggregates and plenty of
USB/Bluetooth interfaces expose no hardware volume. Fall back to
`kAudioHardwareServiceDeviceProperty_VirtualMainVolume`, which synthesises a
master from per-channel volumes
([Apple](https://developer.apple.com/documentation/audiotoolbox/kaudiohardwareservicedeviceproperty_virtualmainvolume?language=objc));
use the property selector with `AudioObject*PropertyData`, not the
`AudioHardwareService*` wrapper functions, which have been deprecated since 10.9
([TN2223](https://developer.apple.com/library/archive/technotes/tn2223/_index.html)).
Note `kAudioObjectPropertyElementMaster` was renamed `…Main` in macOS 12.

**Route: plain `ctypes` on
`/System/Library/Frameworks/CoreAudio.framework/CoreAudio`.** No third-party
dependency, no PyObjC needed for this one, no TCC prompt. A worked Python ctypes
CLI doing exactly this is at
[akrabat.com](https://akrabat.com/mac-volume-a-cli-to-control-device-volume/).

`osascript -e 'set volume output volume 50'` is the one-line fallback and almost
certainly triggers no TCC prompt (`set volume` is a Standard Additions command
executed in-process, not an Apple Event to another app; the Automation gate fires
only on a source→target app pair —
[Scripting OS X](https://scriptingosx.com/2020/09/avoiding-applescript-security-and-privacy-requests/)).
But it forks a process per call, which is wrong at encoder rates, and it addresses
the "system volume" abstraction rather than a chosen device. Avoid the PyPI
packages here: `mac-volume` last released in 2020, and I found no maintained
`osx-audio` / `pycoreaudio` worth depending on.

### 3.4 Global parametric EQ

**Why there is no Equalizer APO for macOS.** APO works because Windows publishes a
documented insertion point in the system audio engine. **macOS has none.** Every
option below is a workaround for that single fact, and it is worth stating
plainly because it explains why this is disproportionately hard relative to how
small `eqapo.py` is.

**Audio Units do not solve it, and it is worth knowing why.** An AUv3 is
*hosted* — it only runs inside something that loads it and pumps audio through
it. macOS ships no host sitting between all applications and the output device.
So writing an AUv3 gets you a component other people's hosts can load, not a
system EQ; you still need a virtual device or a tap to create the insertion
point, and the AU is merely the DSP inside it. It cannot bootstrap itself either:
an out-of-process AUv3 on macOS enumerating available units only sees v2 units
([forum 728404](https://developer.apple.com/forums/thread/728404)).

**Option A — FineTune. The one to investigate first.** GPL-3, free, macOS 15+,
actively released (v1.9.0, 2026-07-09). Its EQ is `vDSP_biquad`-based, and its
AutoEQ loader's header comment reads literally *"Parses EqualizerAPO
ParametricEQ.txt files into AutoEQ profiles"* — reading `Preamp:` and `Filter …`
lines with peaking, low-shelf and high-shelf sections, from
`~/Library/Application Support/FineTune/AutoEQ/`
([AutoEQParser.swift](https://github.com/ronitsingh10/FineTune/blob/main/FineTune/Audio/AutoEQ/AutoEQParser.swift)).
**That is the exact format `eqapo.render()` already emits**, PK/LSC/HSC included.

Two caveats, both real and both fixable:
1. The URL scheme has **no EQ action** — volume/mute/device only.
2. `AutoEQProfileManager` loads at startup and on explicit import; I found **no
   filesystem watcher**, so rewriting the file will not hot-reload the way APO
   does. `eqapo.py`'s whole debounce design (`WRITE_MS = 50`) exists *because*
   APO hot-reloads. Without a watcher you need a different trigger, and the
   debounce budget changes shape entirely.

Both are small patches to a GPL project — an `eq` URL-scheme action plus a
directory watcher — and upstreaming them is plausibly a week including review.
**Unverified**: whether FineTune's EQ can apply to *all* audio or only per-app.
Its architecture is per-app taps, so a genuine global bus is not guaranteed.

**Option B — SoundSource, $49, macOS 15+.** The strongest *automation* story of
the commercial options. Effects apply at two tiers and the manual is explicit:
*"Audio first flows through any per-app adjustments which are configured, and then
through any system output device adjustments"* — so **device-level effects are
real system-wide EQ**
([Effects Overview](https://rogueamoeba.com/support/manuals/soundsource/?page=effects-overview)).
SoundSource 6 added Shortcuts actions including **`Set Effect Parameter`**
("Sets a specific parameter on a chosen audio effect")
([Using Shortcuts with SoundSource](https://www.rogueamoeba.com/support/manuals/soundsource/?page=shortcuts)),
and shortcuts are invocable as `shortcuts run "Name"`. So a Python host *can*
drive it — by shelling out, not by writing a file. Caveats: no AppleScript
dictionary, no URL scheme, Shortcuts is the entire surface; its own EQ is a
10-band *graphic* (fixed-band) EQ, so a true 3-band parametric means loading an
AU; and `shortcuts run` process-spawn latency is very likely too slow for a knob
at one detent per 10 ms, so `eqapo.tick()`'s 50 ms debounce would have to become
much coarser — which the panel would show as the curve leading the sound.

**Option C — Audio Hijack, $69.** It *can* be a system EQ (System Audio block →
EQ/AU block → Output Device block — the documented room-correction recipe). Its
scripting is the richest here: JavaScript with session events, external
triggering via `open -b com.rogueamoeba.audiohijack Some.ahcommand`, Shortcuts
actions
([AH Scripting API](https://rogueamoeba.com/support/knowledgebase/?showArticle=AH-Scripting-API)).
**But the API is the wrong shape**: a block object exposes `name`, `type`,
`disabled` — there is no documented way to read or set band frequencies, gains or
Qs. You could pre-build N fixed presets and toggle between them, which is not
what naub's three continuous knobs do. External script execution is also off by
default.

**Option D — eqMac. A dead end today.** Apache-2.0, but the public repo is
**frozen at v1.3.2 with all newer work on a private fork**
([README](https://github.com/bitgapp/eqMac)) while binaries keep shipping
(v1.8.15, 2026-04-24) — so effectively closed-source. Freemium; the app volume
mixer and AU hosting are Pro ($3/mo, $30/yr, $40 lifetime). **No scriptable
interface**: an HTTP/WebSocket control API is listed as *"in development"*, not
shipped. Building on this means betting on a roadmap item in a closed fork.

**Option E — ship our own HAL plugin with EQ DSP inside.** Technically the
cleanest, and the only option that restores APO-style drivability (it is our
code, so a file-watched text config is trivial). Importantly this is **not** a
kext and **not** a System Extension: a plain `.driver` bundle in
`/Library/Audio/Plug-Ins/HAL` loaded by `coreaudiod`, no reboot, no Reduced
Security, and **no `com.apple.developer.driverkit` entitlement** (which would
otherwise be an Apple-approved request with a 4-8 week turnaround —
[Apple](https://developer.apple.com/documentation/DriverKit/requesting-entitlements-for-driverkit-development)).
`AudioServerPlugIn` is not deprecated on macOS 26. BlackHole proves the shape
ships: signed, notarised `.pkg`, `chown root:wheel`, restart `coreaudiod`. Costs:
$99/yr, Developer ID signing with `--timestamp`, hardened runtime, notarising the
`.pkg` as a container ([Apple DTS, forum 116003](https://developer.apple.com/forums/thread/116003)),
an admin password at install, and a permanent maintenance burden across every
macOS release. **And it flatly violates "naub writes no DSP."**

**Option F — BlackHole + our own numpy DSP. Don't.** The user installs a driver
*and* must re-point their default output, at which point their real device is
downstream and the volume keys control the wrong thing; device changes and
sleep/wake break it; and the DSP itself cannot be a Python callback for the
real-time reasons in §3.2. You would end up writing C anyway, at which point
Option E is strictly better.

**Ranking for naub: A, then B, then E.** A is the only one that preserves
`eqapo.py`'s essential character — a small module that writes a text file and
gets out of the way.

### 3.5 Spectrum analyser input

`sysmeter.py` is the most portable of the audio modules: `pyaudiowpatch` appears
only in `start()` and `_loop()`; `_band_edges()`, `bands()` and `peak()` are pure
numpy and need no change at all. Two routes:

**(a) A global process tap** — `CATapDescription`'s
`initStereoGlobalTapButExcludeProcesses:` with naub's own render process
excluded (otherwise you feed back). No extra install for the user, and it comes
free once §3.1's helper exists. It needs the audio-capture TCC grant, so on a
Mac the EQ screen would be the thing that triggers the permission prompt — worth
knowing, since `want_spectrum()` already starts the capture on demand and would
now be starting a *consent dialog* on demand. That is a UX wrinkle: move the
prompt to startup.

**(b) BlackHole + `sounddevice`** — about two days' work and no native code, but
the user must install a driver *and* set their system output to BlackHole, at
which point their speakers are downstream of naub and the hardware volume keys
control the wrong device. Only sane if naub is already re-rendering.

`sounddevice` is the right PortAudio binding: it ships a `universal2` wheel with
PortAudio bundled, so no `brew install portaudio`
([python-sounddevice](https://github.com/spatialaudio/python-sounddevice)).
PyAudio has no official macOS arm64 wheels. Note that PortAudio only ever sees
Core Audio *devices* — it cannot do process taps, so route (a) is not reachable
through it.

One nice knock-on: `appmeter.refresh()`'s "skip our own PID" logic exists because
the loopback capture registered the host as an audio session. On macOS the
equivalent is the tap's own exclude-list, which is cleaner — but note
`isExclusive` is a direction flag, not a lock (§3.1), so getting it backwards
gives silence rather than an error.

### 3.6 App enumeration and icon extraction (build-time)

Straightforward, and cleaner than the PowerShell version. `NSWorkspace` +
`NSRunningApplication` via `pyobjc-framework-Cocoa` alone (~390 KB wheel,
universal2, Python ≥3.10, **no Xcode or Command Line Tools needed**).

- Enumerate `NSWorkspace.sharedWorkspace().runningApplications()`, filter
  `app.activationPolicy() == NSApplicationActivationPolicyRegular` (0; Accessory
  is 1, Prohibited 2 —
  [Apple](https://developer.apple.com/documentation/appkit/nsapplication/activationpolicy-swift.enum)).
  Not deprecated, not TCC-gated, no prompt.
- **Semantic difference worth stating**: `regular` means "appears in the Dock",
  which *includes launched-but-windowless apps* that `appscan.py`'s
  `MainWindowTitle -ne ''` filter deliberately excludes. Expect a slightly
  different, slightly larger set. The `SKIP` list will need macOS equivalents.
- **Icons: use `NSRunningApplication.icon()` directly** — you already hold the
  object, no path round-trip: `icon().TIFFRepresentation()` →
  `NSBitmapImageRep.imageRepWithData_` →
  `representationUsingType_properties_(NSBitmapImageFileTypePNG, {})`. For pixel
  size, don't fight Cocoa's points-vs-pixels: iterate `img.representations()`,
  take the largest `pixelsWide()` (app icons routinely carry 512 or 1024), and do
  all downsampling in Pillow — which is what `gen_switcher.py` / `frc.py` want
  anyway.
- **Do not use the `CFBundleIconFile` + `.icns` + `sips`/`iconutil` route.**
  Modern apps ship `Assets.car` with no loose `.icns` and use `CFBundleIconName`;
  macOS 26 Tahoe's new `.icon` / Icon Composer format makes bundle contents
  heterogeneous; and icons can be set at runtime via `NSApplication` or
  `NSDockTilePlugin`, so only LaunchServices resolves them correctly
  ([forum 760999](https://developer.apple.com/forums/thread/760999)).
- **Do not use `CGWindowListCopyWindowInfo`** as a `MainWindowTitle` substitute.
  The received wisdom (you lose `kCGWindowName` but keep `kCGWindowOwnerName`) no
  longer holds — on Tahoe `kCGWindowOwnerName` is reportedly `nil` too without
  Screen Recording. Apple DTS's own line is the citable one: *"which keys are
  populated without permission is undocumented and may change at any time"*
  ([forum 839069](https://developer.apple.com/forums/thread/839069)).
- **Permissions**: no prompt for enumeration; `/Applications` is not
  TCC-protected (only `~/Desktop`, `~/Documents`, `~/Downloads` are). One
  constraint: `NSWorkspace` is **not daemon-safe** — fine from a logged-in shell,
  broken in CI with no GUI session. That matters if you ever want to regenerate
  `switcher_data.py` in a build pipeline.

### 3.7 Font rasterisation (build-time) — and a correction to the repo

**First, the thing that makes this a non-issue:** `device/ccfont.py` is generated
*and committed* ("Auto-generated by `host/gen_font.py` from Small Fonts"), as are
`demos/appicons.py` and `demos/switcher_data.py`. **A Mac never needs to
rasterise a font to run or deploy naub.** This is a developer convenience, not
part of the deploy path.

**Second, a load-bearing premise in `gdifont.py` is wrong, and I verified this
first-hand on this machine rather than taking it from a source.** The docstring
says:

> *"PIL/FreeType cannot open a Windows .FON at all, so the whole family of raster
> faces (Small Fonts, Terminal, Fixedsys, MS Sans Serif, ...) is invisible to
> it."*

FreeType's `winfonts` driver is a standard entry in its `modules.cfg` and is
compiled into the FreeType that Pillow ships. Measured just now:

```
ImageFont.truetype('C:/Windows/Fonts/smalle.fon', 8)          -> OSError: invalid pixel size
ImageFont.truetype('C:/Windows/Fonts/smalle.fon', 8, index=4) -> ('Small Fonts', 'Regular')   OK
```

Each FNT strike inside a `.FON` is a **separate FreeType face index**, and you
must ask for that index at exactly its native pixel size. Probing
`smalle.fon` enumerated real strikes at indices 1-5 (4, 5, 7, 8, 9 px) and
`MS Serif` at 6-7. So the file opens fine; the docstring's diagnosis is wrong.

**But the conclusion — "use GDI, not Pillow" — is right, for a different
reason.** Rendering through the opened face gives `.notdef` boxes:

```
PIL/FreeType, smalle.fon idx=4 size=8, "HEG"      GDI, Small Fonts h=8, "HEG"
 .#.#.#..................                          .#..#.###..###
 .#.#.#..................                          .#..#.#...#...
 .#.#.#..................                          .####.##..#.##
 .#.#.#..................                          .#..#.#...#..#
 .#.#.#..................                          .#..#.###..###
```

FNT faces expose one charmap with encoding `FT_ENCODING_NONE`, and Pillow's
`_imagingft.c` can only select a charmap by a four-character tag passed to
`FT_Select_Charmap`. `FT_ENCODING_NONE` is therefore unreachable, no charmap is
active, and every character maps to glyph 0. This is the same bug pygame
documented in [pygame#4377](https://github.com/pygame/pygame/issues/4377).

**This changes the recommendation for item 7.** The right platform-neutral
replacement is **`freetype-py`, not CoreText and not Pillow** — because
`freetype-py` can call `FT_Set_Charmap` / `face.set_charmap(face.charmaps[0])`
and `select_size(index)` directly, and exposes `face.num_faces` /
`face.available_sizes`, which would replace `gdifont.strikes()`' render-and-probe
heuristic with a direct enumeration. A supporting measurement was made during
this study (GDI `NONANTIALIASED_QUALITY` vs `freetype-py`
`FT_LOAD_TARGET_MONO` came out **byte-identical across 12 glyphs on Small Fonts
5/6/8 px, MS Sans Serif and MS Serif 13/16 px, and Fixedsys 15 px**) — **I did
not re-run that one myself, so treat it as strong but unconfirmed.**

**CoreText is not a viable translation, for three separate reasons:**
1. No CoreText-level antialiasing control (Apple's own
   [forum answer](https://developer.apple.com/forums/thread/770940)); grayscale AA
   is the post-Mojave default.
2. `CTFontGetBoundingRectsForGlyphs` returns *antialiased* boxes even with AA
   off, which cropped vertical stems out entirely for Alacritty
   ([crossfont#73](https://github.com/alacritty/crossfont/issues/73)).
3. **macOS ships essentially no bitmap-strike faces** — Monaco 9 is the lone
   survivor, against the Windows inventory of Small Fonts / Terminal / Fixedsys /
   MS Sans Serif / MS Serif at 3-32 px. And separately, 5 of the 8 entries in
   `gen_font.FACES` point at `%WINDIR%\Fonts` for Tahoma, Consolas and Franklin
   Gothic, none of which exist on macOS.

Also relevant: on *outline* faces the two rasterisers genuinely diverge at small
sizes — converging at ≥12 px but disagreeing badly at 8-10 px on heavily
delta-hinted UI faces, and naub's TINY size is **exactly 8 px**. (The TrueType
interpreter version v35/v38/v40 is a red herring: they produce identical output
under `FT_LOAD_TARGET_MONO`, and stem darkening is a CFF/Type1 grayscale effect
that does not apply.)

**Recommendation, in order:**
- **(A) Keep `ccfont.py` committed and make regeneration Windows-only.** Already
  true. The only option that guarantees identical bitmaps. **Zero work.** This is
  what I would do.
- **(B) If you want it platform-neutral, swap GDI for `freetype-py`** — not
  CoreText. You would lose nothing on the strikes, gain a real strike enumeration,
  and drop GDI's font-mapper nondeterminism (a latent Windows hazard: the
  `tmHeight == requested_height` probe in `strikes()` passes even when GDI has
  quietly substituted a Greek or Turkish variant `.FON`). Blockers: the `.FON`
  files would have to be vendored, and **redistributing Microsoft system fonts is
  probably not permitted — check the licence**; and outline faces would still
  differ per platform.
- **(C) Long-term clean answer: vendor a BDF.** Pillow's `BdfFontFile` →
  `.save()` → `ImageFont.load()` is fully deterministic (256-char limit, fine
  here). Terminus, Spleen, Tom Thumb and Cozette are permissively licensed and
  designed at exact pixel sizes. Cost: the panel stops looking like Windows,
  which given README's care about the TINY face being "a Windows *raster* face,
  drawn by a designer at that size" is a real aesthetic loss.

**Whichever you pick, fix the `gdifont.py` docstring** — otherwise the next
person re-derives the GDI dependency from the same wrong reasoning.

### 3.8 Serial transport — `find_ports()` is silently broken on macOS

**`/dev/cu.*` vs `/dev/tty.*`.** `tty.*` is the *callin* device: `open()` blocks
*inside the syscall* until DCD is asserted, and a CDC-ACM gadget never asserts a
real carrier. `/dev/cu.*` ("calling unit") is the *callout* device and opens
immediately
([pbxbook](https://pbxbook.com/other/mac-tty.html),
[Apple forums 117778](https://developer.apple.com/forums/thread/117778)). They are
also mutually exclusive — a blocked `open()` on `tty.*` cannot complete while the
`cu.*` side is open. Opening `tty.usbmodem*` from pyserial is a **hang, not an
error**. The repo's own `bench/deploy_repl.py` already globs `/dev/cu.usbmodem*`,
so this is settled practice here.

**The real finding: `naub_bridge.find_ports()` cannot work on macOS, and it will
fail silently rather than loudly.** From pyserial 3.5's `list_ports_osx.py` (3.5
is the current *and only* release; nothing has shipped since 2020):

1. **There is no `MI_xx`.** macOS `hwid` is assembled by `usb_info()` from
   VID/PID/SER/LOCATION only, so `_iface()`'s first branch never fires.
2. **`location` is identical for both CDC ports** — it comes from the *parent USB
   device's* `locationID`, one IOKit object for both interfaces. And
   `location_to_string(0x14200000)` yields `"20-2"`, while `_iface()`'s regex
   `[:.](\d+)$` requires a `:` or `.` before the digits; a hyphen does not match,
   so both ports return 0 anyway.
3. **`interface` is a name string, not a number, and stock pyserial fills it
   wrongly.** `scan_interfaces()` looks only for an `IOUSBInterface` parent; on
   Apple silicon that class was replaced by `IOUSBHostInterface`, so the scan list
   is empty and `interface` is `None` for every port. Adafruit states this
   plainly in `Adafruit_Board_Toolkit`: *"pyserial 3.5 and below have a bug on
   MacOS that returns an identical interface name for a composite USB device with
   multiple device names."*

Net effect: `cands.sort(key=_iface)` is an all-ties sort, `sorted()` is stable,
and `find_ports()` returns whatever order IOKit happened to enumerate — **so it
picks the console as the data port roughly half the time**. Given README's note
that host→device writes on the console port *hang* on this board, that failure
mode is a silent lockup, not an error message. The upstream fix
([pyserial PR #566](https://github.com/pyserial/pyserial/pull/566)) has been open
since Feb 2021 and unmerged as of Dec 2025. Do not wait for it.

**The correct macOS discriminator is the interface *name*.** CircuitPython sets
distinct USB interface string descriptors in
`shared-module/usb_cdc/__init__.c`:

```c
static const char console_cdc_comm_interface_name[] = USB_INTERFACE_NAME " CDC control";
static const char data_cdc_comm_interface_name[]    = USB_INTERFACE_NAME " CDC2 control";
static const char console_cdc_data_interface_name[] = USB_INTERFACE_NAME " CDC data";
static const char data_cdc_data_interface_name[]    = USB_INTERFACE_NAME " CDC2 data";
```

`" CDC2 "` is the data port; `" CDC "` is the REPL. That is exactly what
`adafruit_board_toolkit.circuitpython_serial.repl_comports()` / `data_comports()`
test, with **no platform branches** — the platform-specific part is a vendored,
patched `_list_ports_osx.py` that reads `kUSBString` / `"USB Interface Name"` and,
failing those, a real `bInterfaceNumber` from IOKit. So **the interface number
does exist**; stock pyserial simply does not surface it.

Recommendation: depend on
[`adafruit-board-toolkit`](https://pypi.org/project/adafruit-board-toolkit/)
(v1.1.2) rather than reimplementing, but match on the `" CDC2 "` *substring*
rather than its `INTERFACE_PREFIXES = ("CircuitPython", …)`, since that prefix
comes from a build-time `USB_INTERFACE_NAME` macro a board port could override.
Keep `_iface()` for `sys.platform == "win32"` and branch on Darwin. Its last
release was Dec 2022, so verify it against current macOS.

Device names: reported dual-CDC pairs are `…usbmodem1101` / `…1103` and
`…myproj1` / `…3` — the lower suffix is the console. **I found no authoritative
documentation that the trailing digit is the interface number**; Apple documents
neither `AppleUSBCDCACM`'s naming scheme nor its stability. Treat "sort by name,
lowest is console" as a *fallback*, not the mechanism. Ironically macOS ordering
is reported to be *more* stable than Windows' here, just undocumented.

**No driver needed.** CDC-ACM is a USB class; macOS binds `AppleUSBCDCACM`
automatically and creates the `tty.*`/`cu.*` pair. No kext, no Zadig equivalent.
**No TCC prompt** for `/dev/cu.*` — serial nodes are not in a protected class,
and macOS has no `dialout` group. **But there is one prompt**: on Apple silicon
*laptops* (Ventura and later), USB-C/Thunderbolt accessories need a one-time
"Allow accessory to connect?" approval, default "Ask for new accessories"
([Apple](https://support.apple.com/en-ng/102282)). Not a blocker, but the first
plug-in experience differs from Windows and belongs in the onboarding doc. It
does *not* apply to iMac / Mac mini / Studio / Pro.

### 3.9 Firmware deployment

**Start from the repo's own measured history, which is worse than the generic
answer.** `BRINGUP_LOG.md` §2 records that on the Mac used earlier in this
project, **the CIRCUITPY drive never mounted at all**: macOS enumerated the
mass-storage interface and bound `IOUSBMassStorageDriverNub` but never produced a
block device, while CircuitPython simultaneously refused `storage.remount()`
("Cannot remount path when visible via USB"). Neither side could write it. That
was called unresolved at the hardware level, with re-flashing the UF2 as the
likely fix. **The repo already ships the workaround**: `bench/deploy_repl.py`
deploys over the REPL by base64 + `storage.remount()` + soft reboot, finding the
board via `glob.glob("/dev/cu.usbmodem*")`. So a working macOS deploy path exists
today; a macOS `deploy.py` should probe for `/Volumes/CIRCUITPY` and **fall back
to the REPL path**, not assume the drive.

If the drive *does* mount:

- **Path** is `/Volumes/CIRCUITPY`. Detect it rather than hardcoding: glob
  `/Volumes/*`, keep those with a readable `boot_out.txt`, parse the version —
  which is what [discotool](https://github.com/Neradoc/discotool) does. Note
  discotool's own docstring flags that **Apple removed `SPUSBDataType` in macOS
  26 Tahoe**, so anything shelling out to `system_profiler SPUSBDataType` is
  already dead; use `ioreg`.
- **The `._` AppleDouble problem is real and current.** macOS `msdosfs`
  implements extended attributes on FAT by synthesising a `._name` shadow file,
  so *any* xattr write creates one; plus `.fseventsd`, `.Spotlight-V100`,
  `.Trashes`, `.DS_Store`. Adafruit's
  [troubleshooting guide](https://learn.adafruit.com/welcome-to-circuitpython/troubleshooting)
  documents both the problem and the remedies: `mdutil -i off /Volumes/CIRCUITPY`,
  `touch .metadata_never_index`, `mkdir .fseventsd && touch .fseventsd/no_log`,
  `cp -X` (don't copy xattrs), `dot_clean /Volumes/CIRCUITPY`, `xattr -cr` on the
  source before copying, and `storage.erase_filesystem()` on the board. Adafruit's
  blunt version: *"do not use the Finder to copy the files. Or to look at the
  directory those files are in."*
- **Good news specific to `deploy.py`:** it uses `shutil.copy`, and Python's
  `os.getxattr`/`setxattr`/`listxattr` are documented **Linux-only**, with
  `shutil._copyxattr` guarded by `hasattr(os, 'listxattr')`. `shutil.copy` copies
  data and mode only. **So `deploy.py` should not create `._` files at all** — the
  pollution comes from Finder, from `cp` (which copies xattrs by default), and
  from Spotlight. This is the cheapest experiment in the whole report: one
  `ls -la /Volumes/CIRCUITPY` after a deploy settles it. (`COPYFILE_DISABLE` is
  irrelevant here — it only affects `tar`/libarchive.)
- **Auto-reload history matters for the "code.py last" invariant.** macOS
  14.0-14.3 replaced the msdosfs kext with a userspace driver that delayed
  metadata writes by tens of seconds, producing `OSError: [Errno 5]`
  ([circuitpython#8449](https://github.com/adafruit/circuitpython/issues/8449));
  Apple's 14.4 "fix" traded correctness for speed and made writes to any FAT
  ≤1 GB **400-800× slower** — one report measured 16 KB/s, 38 seconds for 24 files
  ([#8918](https://github.com/adafruit/circuitpython/issues/8918)). **Fixed in
  macOS 15.2** (issue closed 2025-01-14). Since CircuitPython's default autoreload
  delay is **0.75 s after the most recent write**, a slow filesystem makes each
  of `deploy.py`'s 16 `LIBS` copies trigger its own reload and breaks the
  code.py-last invariant outright. Robust fix: set
  `supervisor.runtime.autoreload = False` during deploy, or `sync` after the copy
  loop. **Set a hard floor of macOS 15.2 for the port.**
- **`/Volumes` *is* TCC-protected.** Reading or writing a volume under
  `/Volumes/*` triggers the "would like to access files on a removable volume"
  prompt, attributed to the responsible process — Terminal or iTerm2, not your
  Python binary. Granted once per terminal app and inherited thereafter. Eclectic
  Light's Tahoe testing found the classification depends on mount timing, and a
  volume mounted well after login is treated as removable — always the case for
  CIRCUITPY
  ([which folders are protected in Tahoe](https://eclecticlight.co/2026/04/15/privacy-which-folders-are-protected-in-tahoe/)).
  **Guard against silent denial**: Apple DTS reports CLI tools frequently get a
  silent denial rather than a dialog, so catch `PermissionError` around
  `shutil.copy` and print the System Settings path rather than letting a bare
  traceback out.
- Two boards at once mount the second under a suffixed name, and on macOS 13.1+
  the third-and-later same-type board can fail to mount or mount with broken
  permissions ([circuitpython#7409](https://github.com/adafruit/circuitpython/issues/7409)).
  Irrelevant for one board.
- **Do not build on `rsync` flags**: macOS 15 replaced GNU rsync with openrsync,
  which accepts only a subset of the arguments.

### 3.10 State and config file location

`~/Library/Application Support/naub/pins.json`. Apple's
[File System Programming Guide](https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/FileSystemProgrammingGuide/FileSystemOverview/FileSystemOverview.html)
explicitly names *configuration files* as belonging there. `~/Library/Preferences`
is explicitly forbidden (*"You should never create files in this directory
yourself"*) and mechanically hazardous, since `cfprefsd` caches domains in memory
and can clobber direct writes. `~/Library/Caches` is wrong (purgeable, not backed
up). Application Support is backed up by Time Machine and **not** synced by
iCloud — which matches `%LOCALAPPDATA%`'s non-roaming semantics exactly, and
matters because `PIN_PATH` holds column assignments that should not follow the
user to another machine with different apps installed.

`~/.config` is defensible for a CLI tool and Apple offers no guidance against
dotfiles. Pragmatic compromise: default to Application Support, honour `XDG_*`
when the user has explicitly set it.

`platformdirs` (4.11.7, pure-Python, zero deps) works, with **two traps**:
`appauthor=False` is mandatory or `user_data_dir("naub")` returns
`AppData\Local\naub\naub` on Windows — *not* the existing path, so every current
install would silently lose its pins; and since 4.6.0 platformdirs honours XDG
vars on macOS, so under launchd (which does not inherit shell env) the same
machine resolves differently than from Terminal. Given `naub_bridge.py` has
exactly one call site, a ten-line `state_dir()` helper is probably better than a
dependency.

**Unrelated but worth fixing while you are in there:** `save_pins()` writes in
place. A bridge killed mid-write corrupts `pins.json` — and `load_pins()`
swallows the exception, so the symptom is columns silently going unpinned.
`eqapo._write()` already does the temp-file-then-`os.replace()` dance and even
documents why; `save_pins()` should do the same.

### 3.11 Packaging and distribution

**Dependencies are the easy part.** `numpy`, `Pillow`, `psutil`, `pyserial` and
`pyobjc` all ship arm64/universal2 wheels; **nothing needs compilation**.
`pywin32`, `pycaw`, `comtypes` and `PyAudioWPatch` all drop out.
`pyaudiowpatch`'s replacement for device-level capture is `sounddevice`, which
bundles PortAudio in its wheel.

The **only** Python package attempting per-app capture via taps is `flexaudio`
(PyO3/Rust, macOS 14.4+, v0.2.0 released 2026-07-04) — and per libraries.io it has
0 dependents, 0 stars, 0 forks, and a 0.x "public API is not yet stable" notice.
Evaluate it; do not depend on it. `catap` (MIT, v0.6.0) is the better-documented
option but is equally unvetted.

**Three possible shapes:**

**(a) `pip install` + `python naub_bridge.py`, user grants Terminal.**
Zero dollars, days of work. Works today for serial, system volume, display,
`psutil`, deploy. Per §3.1 it *also* works for taps, because Terminal holds the
grant and your script inherits it. The costs are real though: the grant is
attributed to *Terminal*, blanket for every script the user ever runs there; it
goes stale on every Python upgrade because Homebrew's Cellar path changes (and
note a symlinked `python3` dropped into a Privacy pane appears greyed out — you
must add the real Cellar binary); the process must be fully restarted after
granting; and if Screen-Recording-family grants are subject to Sequoia's monthly
re-authorisation, the recurring prompt will name **Terminal**, at semi-random
moments, which reads as phishing.

Note the one asymmetry in your favour: **Gatekeeper does not gate
`python somefile.py`.** The interpreter is already trusted and the script is data.
Command-line `tar`/`unzip` also do not propagate the quarantine xattr, so
`curl … | tar xz && python naub_bridge.py` sails through with no
`xattr -d com.apple.quarantine` dance. That only becomes an issue the moment you
ship a `.app`, `.dmg` or `.pkg`.

**(b) A signed, notarised `.app`. $99/yr, 1-2 weeks the first time.**
Requires Apple Developer Program membership, a Developer ID Application
certificate, **Hardened Runtime** (mandatory for notarisation; every nested
Mach-O must be signed), notarisation, and stapling. Buys you a stable Team ID so
grants stick and are attributed to *naub*; a real bundle ID for `tccutil`; a real
Info.plist for `NSAudioCaptureUsageDescription`; a clean install; and a plausible
autostart story via `SMAppService`.

**The Python-specific signing gotcha**: Hardened Runtime's library validation
breaks PyInstaller/py2app bundles, which load `.so` extension modules not signed
by your team. You need `com.apple.security.cs.disable-library-validation` and
often `com.apple.security.cs.allow-unsigned-executable-memory`
([PyInstaller #4629](https://github.com/pyinstaller/pyinstaller/issues/4629)).
Note the tension: `disable-library-validation` weakens precisely the code-identity
guarantee TCC leans on, and **whether it interacts badly with capture grants is
untested** — flag it and test it early, because discovering it late invalidates
the whole packaging plan.

Without signing, a quarantined download is refused, and **macOS 15 removed the
Control-click → Open bypass**: the user must launch, dismiss, go to System
Settings → Privacy & Security → Security → "Open Anyway", and authenticate with
an admin password. That flow persists in macOS 26.

**(c) (b) plus a signed/notarised `.pkg` installing a HAL driver.** Same
certificate, no extra money, but **+4-8 weeks** of Core Audio work that cannot be
done in Python (PyObjC explicitly does not support authoring CoreAudio plugins),
plus a root installer, a `coreaudiod` restart, an admin password, and a permanent
maintenance burden. **Don't** — process taps obsolete this for naub's use case,
and if you truly need a virtual device, depend on BlackHole rather than shipping
your own.

**Recommendation: (a) first, then (b).** Ship (a) as a developer preview scoped
to serial + system volume + deploy, to validate the device story on a Mac for
zero spend. Then, if per-app metering and volume are non-negotiable, buy the $99
membership, build (b), and **write the per-app engine as a small signed Swift or
Rust helper that talks to the Python bridge over a pipe**. Do not attempt the
real-time IOProc in CPython.

### 3.12 Things not on the brief that will bite

**App Nap and timer coalescing will throttle the 30 Hz loop.** macOS throttles
background processes and *"timers are no longer timely"*. Opt out with
`NSProcessInfo.beginActivityWithOptions:reason:` — and **keep a strong reference
to the returned token**, because App Nap resumes when it is deallocated
([Lapcat](https://lapcatsoftware.com/articles/prevent-app-nap.html)). In Python
the `appnope` package does exactly this via ctypes and is a one-line fix. Use
`NSActivityUserInitiated`; `NSActivityLatencyCritical` also raises timer
precision. Separately, on Apple silicon, background/utility-QoS work is scheduled
onto **efficiency cores** — set a QoS class explicitly, not just the App Nap
opt-out. `caffeinate -i` is the blunt alternative but also prevents idle sleep,
which users notice.

**The loop will run *faster* on macOS, and that is a behaviour change.**
`naub_bridge.py`'s docstring and STATUS.md both record that `--rate 50` measures
**31.5 Hz** on Windows, because the 2 ms sleep rounds up to the 15.6 ms timer
granularity — and that this was left alone deliberately, since fixing it means
`timeBeginPeriod(1)` and more load on the user's PC. macOS has ~1 ms sleep
granularity, so **the same code will hit or approach the requested 50 Hz** —
roughly 60% more `meter` frames, 60% more per-app peak polls, and on macOS each
of those polls costs a PCM buffer rather than a 0.05 ms COM call. The device's
ballistics were tuned against 46 Hz on the panel side, not the host side, so
nothing visibly breaks — but **the macOS port should probably default `--rate` to
~32** to preserve the measured trade rather than silently accepting a 60% CPU
increase in the one place the codebase least wants it.

**Sleep/wake teardown.** Code defensively:
- After wake, an app's HAL device reference goes stale and capture fails silently.
- **`AudioDeviceID` is not stable** across disconnect/reconnect. The stable
  identifier is `kAudioDevicePropertyDeviceUID` (a string) — key everything off
  the UID, never the numeric ID.
- Register listeners on `kAudioHardwarePropertyDevices` and
  `kAudioHardwarePropertyDefaultOutputDevice` and rebuild taps and aggregates on
  change — but **watch for the feedback loop**: *creating* a tap's aggregate
  device itself fires device-change notifications, so a naive listener restarts
  the streams, which fires more notifications, forever. Debounce and suppress
  self-induced changes.
- On sleep, Macs drop primary USB bus power — **the serial device will disappear
  and come back, possibly on a different `/dev/cu.*` node.** `naub_bridge`'s
  reconnect loop already re-finds the port rather than trusting the old name
  (a deliberate choice documented in `main()`), so this is already handled — one
  of the few places the existing design is *more* macOS-ready than Windows-ready.

**LaunchAgent autostart is the riskiest combination in this report.** A
`~/Library/LaunchAgents/*.plist` works, but since Ventura every third-party agent
appears in System Settings → Login Items & Extensions and firing one produces a
"Background item added" notification the user can toggle off. Worse: a
launchd-started process has **no responsible app at the head of the TCC
attribution chain**, and Howard Oakley says outright *"I don't know how the
Attribution Chain works when there is no app at its head."* Combined with a report
that background Unix executables stopped appearing in the Screen Recording pane
at all in macOS 26.1
([forum 807323](https://developer.apple.com/forums/thread/807323) — note that
poster's *cited cause* is a mis-attribution; the symptom is credible, the
explanation is not), **autostart plus capture may simply not work.** Test before
promising it. If you ship an `.app`, register via `SMAppService`, not a raw plist.

**The `--sessions` diagnostic changes shape.** `show_sessions()` exists because
"that app is in the list but has no level" is a WASAPI question, not a panel
question. On macOS the equivalent question is "did the tap get permission" —
which fails as *digital silence, not an error*. A macOS `--sessions` should
report, per app: process object ID, bundle ID, `IsRunningOutput`, whether a tap
was created, and **whether the tap has ever delivered a non-zero sample**. That
last one is the single most valuable diagnostic on the platform.

---

## 4. What is not possible without external software

Split three ways, since this is the part that most needs to be unambiguous.

### 4.1 Nothing extra needed — works with `pip install` alone

- Serial transport, port discovery, the whole bridge loop and protocol.
- System/master volume and mute (ctypes on CoreAudio; no TCC, no deps).
- Firmware deploy (drive or REPL fallback).
- State/config persistence.
- App enumeration and icon extraction (build-time; `pyobjc-framework-Cocoa`).
- Font data — already committed; regeneration stays Windows-only.
- **Per-app metering and per-app volume, *if* you accept: a native helper binary
  that we build and the user does not install separately, plus a TCC
  "System Audio Recording" grant, plus (in practice) shipping a signed `.app`.**
  Strictly speaking no *third-party* software is required for these — but they
  are not free either, and the cost is ours in engineering and the user's in
  consent.

### 4.2 User must install a free, open-source thing

- **A global EQ, via FineTune** (GPL-3, free). This is the recommended EQ path and
  the only one that keeps `eqapo.py` roughly the shape it is. It would need a
  small upstream patch (hot-reload watcher and/or an `eq` URL action).
- **Per-app volume and mute without us shipping an audio engine** — also FineTune,
  driven through its existing `finetune://` URL scheme. This is the single
  biggest effort saving available in the whole port.
- **The spectrum analyser, if we do not build the tap helper** — BlackHole
  (GPL-3, free, signed pkg or `brew install blackhole-2ch`) plus `sounddevice`.
  Cheap in code, expensive in user experience: they must also re-point their
  default output device.

### 4.3 User must buy something

- **SoundSource, $49** — the paid EQ and per-app-volume path. Its
  `Set Effect Parameter` Shortcuts action is the only *commercial* automation
  surface that can actually move an EQ band, and its device-level effects are
  genuinely system-wide. Driven by shelling out to `shortcuts run`, which is
  almost certainly too slow for per-detent updates.
- **Audio Hijack, $69** — can be a system EQ and has the richest scripting API,
  but that API cannot set EQ band parameters. Only useful for toggling between
  pre-built presets.
- **Loopback, $99** — per-app routing, but no automation API found. Not useful.
- **eqMac Pro, $40 lifetime** — its per-app mixer is behind the paywall, and it
  has no shipped control API regardless. Not useful.

### 4.4 We must ship a signed system-level component

- **Nothing requires a System Extension, a kext, or DriverKit.** This is worth
  stating clearly because it is the assumption `WRITEUP.md` §4 was written under
  and it is now out of date. Process taps need no driver, and even a HAL plugin
  is a plain userland `AudioServerPlugIn`, not a system extension — so **no
  Apple-approved entitlement, no Reduced Security boot, no reboot.**
- **But we almost certainly must ship a signed, notarised `.app`** — $99/yr —
  for anything touching audio capture, and **a native (Swift/C/Rust) helper
  binary** for the real-time path. Neither is a *system* extension; both are real
  costs the Windows build does not have.
- A HAL plugin (signed + notarised `.pkg`, admin password, `coreaudiod` restart)
  is only needed for the EQ Option E, which is the one I recommend against.

---

## 5. What is not possible at all

Short list, and I want to be careful to distinguish "impossible" from "expensive".

1. **A cheap per-app peak level.** There is no macOS API that returns a level for
   another process without capturing and analysing its PCM. Not "hard" — absent.
   Every meter you have ever seen on macOS is computed from captured audio. This
   is the one genuine capability gap, and it is the one that changes naub's
   architecture.
2. **Per-app metering or volume without a TCC audio-capture consent.** There is no
   entitlement, private-API-free workaround, or alternate mechanism. If the user
   declines, those features are gone — not degraded, gone (and they fail as
   *silence*, not as an error).
3. **Reproducing the current font exactly on a Mac.** `ccfont.TINY` is Small
   Fonts at height 8 — a Windows raster face drawn by a designer at that size.
   macOS ships essentially no bitmap-strike faces, and outline rasterisers
   diverge at exactly 8 px. The generated `ccfont.py` transfers fine; the
   *ability to regenerate it from a system font* does not.
4. **Per-app *spectrum*.** Unchanged from Windows — README already documents that
   the band shape is faked because there is no per-session spectrum. On macOS you
   could in principle FFT each tap's PCM and get a real per-app spectrum, so this
   is technically *more* possible than on Windows — but it would mean N FFTs on
   the user's machine for a shape the device currently invents for free, which is
   the wrong trade for this project and I would not do it.
5. **`--rate 50` meaning 31.5 Hz.** Not a limitation, an inversion: see §3.12.

Everything else on the brief is possible. The question is only what it costs.

---

## 6. Recommended porting strategy

### Phase 0 — decide the EQ and per-app question first (2-3 days, on a Mac)

Do not write a line of port code until experiments 1-3 in §7 are run. They cost a
day and they determine whether the port is two weeks or three months. Specifically:
if FineTune can be driven for both per-app volume *and* EQ, the whole native-helper
workstream disappears and naub stays a control surface. That is a 10× swing.

### Phase 1 — "naub on macOS, honestly reduced" (1-2 weeks, $0, no external software)

Ship this first regardless of what Phase 0 says. It validates the hardware,
the protocol and the deploy story on real Mac hardware with zero spend.

**Does:** serial transport with correct dual-CDC discovery; the full protocol and
bridge loop; system/master volume and mute on encoder 3; firmware deploy
(`/Volumes/CIRCUITPY` with the REPL fallback the repo already has); pins
persistence; the mode bar, the CONFIG screen, media keys (already device-side and
driverless); build-time app enumeration and icons.

**Does not:** per-app meters (columns read flat), per-app volume (knobs 0-2 do
nothing), the app switcher (the `apps` list would be empty), the EQ screen's
spectrum, the EQ itself.

**And says so.** This project has a strong existing principle here — README:
*"With no host talking, the meters sit flat rather than inventing a signal: a
meter that moves when nothing is playing is a liar."* Apply exactly that on
macOS. The columns should read flat and the EQ screen should say `INERT` with a
reason, the way `eqapo.status()` already does. `push_eq()` already sends an
`ok/why` pair over the wire; the macOS build should use the same channel to say
"no engine on this platform" rather than drawing a curve nothing applies.

The honest framing for a user: **on macOS, naub is a system volume knob, a media
controller and a display — until you install FineTune.**

### Phase 2 — per-app and EQ via FineTune (1-2 weeks, $0, user installs FineTune)

If Phase 0 confirms it: `appmeter.py` becomes a thin adapter over FineTune's URL
scheme for volume/mute, `eqapo.py` retargets its existing writer at
`~/Library/Application Support/FineTune/AutoEQ/`, and you upstream a watcher plus
an `eq` action. Metering may still be missing here — FineTune's URL scheme is
write-only — so columns might show correct labels and faders with flat meters.
That is an odd but defensible state, and better than a wrong meter.

### Phase 3 — the native tap helper (6-10 weeks, $99/yr, signed .app)

Only if Phase 2's metering gap is unacceptable. Build a small signed Swift or
Rust helper that: enumerates process objects, gates on `IsRunningOutput`, creates
N taps in one private aggregate, computes peaks with `vDSP_maxmgv` inside the
IOProc, and publishes N floats over a pipe at 30 Hz. Start from `catap` or
`audiotee` rather than from scratch. Add the mute-and-re-render gain path only if
you are also abandoning FineTune. Ship as a signed, notarised `.app`.

### What I would actually do

**Phase 0, then Phase 1, then stop and reassess.** The reduced-scope build is
genuinely useful — media keys and system volume are what most people use a
control surface for most of the time, and they cost nothing on macOS — and it
puts real hardware in front of a real Mac, which is where the remaining unknowns
in §7 get answered anyway. Phase 3 is a different project: it makes naub an audio
engine rather than a control surface, and that is a product decision, not a
porting task.

One structural note: whatever happens, **do the adapter split first**. Rename
`appmeter`/`eqapo`/`sysmeter` to platform-suffixed modules behind a tiny factory,
the way `host/naub_host/` already anticipated. `naub_bridge.py` needs three
changes to be fully platform-neutral (the `PIN_PATH`, the `_iface()` branch, and
the `import appmeter` line becoming a dispatch), and doing that on Windows first
means the macOS work is additive rather than a fork.

---

## 7. Open questions requiring a real Mac

Ordered by how much they change the plan. The first three are the ones that
decide the shape of the port and cost about a day together.

1. **Does FineTune hot-reload an Equalizer APO `ParametricEQ.txt`?**
   *Experiment:* install FineTune, drop a `ParametricEQ.txt` in
   `~/Library/Application Support/FineTune/AutoEQ/`, restart the app, confirm the
   filters land. Then rewrite the file *with the app running* and see whether
   anything reloads. **This single test decides the entire EQ recommendation**,
   and by extension whether `eqapo.py` is a port or a rewrite.
2. **Can FineTune's EQ apply to *all* audio, or only per-app?** Its architecture
   is per-app taps, so a global bus is not guaranteed. naub's EQ is explicitly
   global-only by prior decision, so if FineTune cannot do global, Option A dies.
3. **Does FineTune's `finetune://` URL scheme actually move per-app volume fast
   enough for a knob?** *Experiment:* time `open finetune://set-volumes?...`
   end-to-end. If it is hundreds of milliseconds, the knob will feel broken and
   Phase 2 needs a different transport (or dies).
4. **Does `pyobjc-framework-CoreAudio` 12.2 actually expose the tap symbols?**
   *Experiment:* `python3 -c "import CoreAudio; print(CoreAudio.CATapDescription,
   CoreAudio.AudioHardwareCreateProcessTap)"`. Five seconds. The test suite says
   yes; the apinotes say maybe; and one project
   ([ProcTap's archived Nov 2025 investigation](https://github.com/m96-chan/ProcTap/tree/main/archive/apple-silicon-investigation-20251120))
   concluded PyObjC block callbacks were unusable on Apple silicon — which
   `catap` contradicts a year later. Settle it.
5. **How many simultaneous taps work, and what do they cost?** *Experiment:*
   create 1, 5, 10, 20, 30, 50 taps in one aggregate; record the `OSStatus` at
   which creation starts failing, and `powermetrics` CPU for `coreaudiod` plus
   your process at each step, silent vs. all playing. **No data exists anywhere**,
   and naub's switcher list is unbounded by design.
6. **Does an unsigned `python foo.py` from Terminal really get the tap grant?**
   *Experiment:* `tccutil reset SystemAudioCaptureRequests com.apple.Terminal`,
   then `pip install catap && catap record Music -d 5 -o t.wav` from stock
   Terminal. Confirm the prompt names *Terminal*, that the grant appears under
   Screen & System Audio Recording, and that the WAV is non-silent. Repeat under
   iTerm2 and from a venv.
7. **Are audio-capture grants subject to Sequoia/Tahoe's monthly
   re-authorisation?** No source confirms or denies it. *Experiment:* grant, then
   set the clock forward 35 days and re-run. **If yes, it probably disqualifies
   tap-based approaches for an always-on hardware controller** — a monthly modal
   dialog naming "Terminal" is not something you ship.
8. **Does the CIRCUITPY drive mount on the current board at all?**
   `BRINGUP_LOG.md` §2 says it did not, and blamed the UF2. *Experiment:* re-flash
   CircuitPython via BOOTSEL and re-test. If it still fails, `deploy.py` on macOS
   must be REPL-only and that should be documented, not discovered.
9. **Does `shutil.copy` leave `._` files on CIRCUITPY?** Reasoning says no
   (Python's xattr APIs are Linux-only). *Experiment:* one `ls -la
   /Volumes/CIRCUITPY` after a deploy. Cheapest test in this document.
10. **What does `python -m serial.tools.list_ports -v` actually print for both
    CDC ports?** Confirms the §3.8 analysis and tells you whether
    `adafruit-board-toolkit` (last released Dec 2022) still works on current
    macOS. Print `p.interface` for both ports and check for `" CDC2 "`.
11. **Does the intermittent all-zeros tap bug reproduce?** *Experiment:* a 6-hour
    capture with Bluetooth headphones sleeping/waking and 44.1/48 kHz content
    alternating; log any window where every tap reads exactly zero while audio is
    audible. Design the watchdog either way.
12. **Does the multi-pair −12 dB attenuation reproduce?** *Experiment:* play a
    known 0 dBFS tone; compare tap peak on built-in speakers vs. a multi-channel
    interface. If it does, meters need per-device compensation.
13. **Does `com.apple.security.cs.disable-library-validation` break capture
    grants?** Needed for a PyInstaller/py2app bundle, and it weakens exactly the
    code identity TCC relies on. Untested anywhere I could find, and discovering
    it late invalidates the packaging plan.
14. **Does a launchd-started bridge get any TCC grant?** See §3.12. Test before
    promising autostart.

---

## Appendix: sourcing notes

Where a claim is load-bearing I preferred SDK headers, Apple documentation, or
running code over prose. Three specific cautions on the material above:

- Apple's `developer.apple.com/documentation/...` pages render client-side, and
  several fetches during this study returned unreliable summaries. Core Audio
  semantics above are taken from the SDK headers
  ([macos-sdk mirror](https://github.com/alexey-lysiuk/macos-sdk)), from three
  independent open-source implementations, and from Rogue Amoeba's own knowledge
  base — not from those summaries.
- Search results in this area are heavily polluted by AI-generated affiliate
  content. Several such pages surfaced (`cplofmngs.com` and similar) and were used
  only as leads; nothing above rests on them. Two EQ apps that surfaced only
  through such content — "SOUND" and "Equaliser" — are **deliberately excluded**
  from the recommendations as unverified.
- Dates for eqMac, FineTune and Background Music come from the GitHub REST API,
  not from prose claims of activity.
- Deliberately-stale sources leaned on because the mechanism is unchanged, but
  worth re-verifying if a detail becomes load-bearing: Apple QA1016 (archived),
  Apple's App Nap / power-efficiency guides (archived), Eclectic Light's 2017-2018
  quarantine and TCC articles, and Apple forum thread 116003 (~2019).
- The `gdifont.py` correction in §3.7 was verified first-hand on the Windows
  machine during this study (opening `.FON` files through Pillow at explicit face
  indices, and rendering "HEG" through both Pillow and GDI). The claim that
  `freetype-py` mono output is *byte-identical* to GDI came from a separate
  measurement I did not re-run — treat it as strong but unconfirmed.
