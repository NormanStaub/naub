# Testing naubd on a Mac

The macOS build has been **type-checked on both Mac targets from a Windows
machine and has never been linked or run.** Its DSP, app naming and port
selection have unit tests that run anywhere. Everything that touches Core Audio
is unproven, and this is the list that proves it, in the order that fails
cheapest.

**The panic button.** If an app goes silent and stays silent after naubd has
stopped:

```sh
sudo killall coreaudiod     # drops every tap and aggregate; it restarts itself
```

## 0. Set up

- macOS 26 or later. Xcode Command Line Tools (`xcode-select --install`) and
  Rust (`rustup`).
- A board running the current firmware (deploy it from Windows if in doubt).
  On a laptop, allow the accessory when macOS asks.

```sh
cd naubd
cargo build --release
```

The first thing this proves is that it links, which no one has seen yet.

## 1. Doctor: no panel needed, and on a first run it only listens

Run from **Terminal**, with something audibly playing (Music or Spotify, not a
browser yet):

```sh
./target/release/naubd --doctor
```

The grant goes to Terminal in this mode, and a prompt naming Terminal is
correct. Expect the prompt on the first run; then **quit and run it again**,
because a grant only reaches a fresh process.

| Check | Expect | If not |
|---|---|---|
| `serial:` | two USB ports on `/dev/cu.*` with different `iface=`, and the higher chosen | `iface=None` means the name fallback is deciding: check it picked the higher number |
| `audio process objects` | your player marked `OUT`, with a sensible key | `-` as the key means `appkey.rs` filtered it out |
| final lines | `capture_ok=true`, your app with `peak` moving | see below |
| `RESULT:` | "taps deliver real audio" | still silent after granting: send me the whole output |

## 2. The panel

```sh
./target/release/naubd --console
```

Plug in or reload the board. The log should end with `device: naub proto=2 ...`.

| Do | Expect |
|---|---|
| Play two apps | both reach the switcher; columns label; meters move and match what you hear |
| Turn an app's knob down | the log prints `<app>: render on <device>` once; it gets quieter **smoothly**, its meter drops, the other app is untouched |
| Turn it back to 100% | `<app>: meter on <device>`, no volume jump |
| Mute its encoder | silent at once; unmute restores the level |
| **Pause it while turned down, wait 30 s, resume** | stays quiet from the first note. **The key test** |
| Turn the system knob and click it | the real output volume and mute move; menu-bar volume agrees |
| Quit with Ctrl-C while an app is turned down | the app returns to full volume by itself |
| `kill -9` the same way, then rerun | the app recovers; rerun logs nothing bad. If still silent: panic button, and tell me |
| Restart naubd | knob positions come back (`volumes.json`), pins too |

## 3. EQ

| Do | Expect |
|---|---|
| Open the EQ screen with audio playing | spectrum moves; it is the full output, so it should match the music |
| Turn up LOW by 9 dB | audibly more bass on **every** playing app; log shows them switch to `render` |
| Reset the EQ to flat | they return to `meter`; no volume jump |
| Restart with a non-flat EQ | it comes back, once the first samples arrive |
| Notification sounds | **not** EQ'd: system sounds are not tapped. Expected |

## 4. Change things underneath it

| Do | Expect |
|---|---|
| Switch output (speakers to headphones) with an app turned down | taps rebuild within about a second; level stays down |
| Connect Bluetooth headphones with a microphone | **no** orange microphone dot, no crackle |
| Sleep and wake | the panel reconnects by itself; audio and meters come back within about 5 s |
| Play a Safari or Chrome video | a `safari` / `chrome` column; the knob moves the video |
| Close the app that was turned down | no errors; the column empties or keeps its pin |

## 5. The app, the menu bar, and login

```sh
macos/build-app.sh && open target/naub.app
```

Now the grant is naub's own, and the prompt should say so. Check for a `naub`
item in the menu bar and no Dock icon; **Start at Login** and **Quit** should
work. If the menu shows "capture not confirmed" while audio plays, that is the
reported refusal of ad-hoc signed apps: rerun with `NAUB_SIGN_ID` (see the
script). Log out and in, and confirm it comes back with audio still working.
That last step is the one the port study could not predict (§3.12).

## 6. What it costs

With nothing playing, then with two apps playing, one turned down:

```sh
top -pid $(pgrep -x naubd) -stats pid,cpu,mem -l 5 | tail -5
top -pid $(pgrep -x coreaudiod) -stats pid,cpu,mem -l 5 | tail -5
```

Measure `coreaudiod` too: most of a tap's cost lands there, and no published
figure exists.

## What to send back

The `--doctor` output and `naubd.log` (beside `config.json`, or in
`~/Library/Application Support/naub/`) for anything that misbehaves.
