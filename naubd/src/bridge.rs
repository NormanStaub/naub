//! Orchestrator: drives the V2 mixer screen from this PC's real audio.
//!
//! A port of `firmware/v2/host/naub_bridge.py`. Without it the device has no
//! audio to show and no way to apply a knob: the panel is a display, the host
//! is the thing that talks to WASAPI. It reconnects by itself when the board
//! reloads (every `deploy.py` does) or is replugged.
//!
//! Two ownership rules from PROTOCOL.md §11 that shape the whole file:
//!
//! * **Column assignments are OURS to keep.** The device is a display; it
//!   loses everything on a reload. So the pins live in [`crate::pins`] and are
//!   pushed back on every connect.
//! * **Which apps are OFFERED in the switcher is the device's business.** We
//!   say what is streaming (`apps` / `ameter`); `device/wheel.py` decides what
//!   is worth showing and for how long. The accounting is per-app timers at a
//!   few hertz, and this is the user's PC -- the panel has a whole core idle
//!   between frames.

use std::time::{Duration, Instant};

use crate::appicon;
use crate::audio::{self, Audio};
use crate::eqapo::{self, ParametricEq};
use crate::link::{autodetect, SerialLink};
use crate::pins::{self, NCOL};
use crate::protocol::{self, DeviceEvent};
use crate::sysmeter::SysMeter;

/// Must match `device/eqscreen.NMETER`.
const SBANDS: usize = 21;
/// ... and `demos/demo_mixer.EQ_MODE`.
const EQ_MODE: i32 = 1;
/// Channel 3 is the system endpoint, for mute as well as volume.
const SYS_CH: u8 = audio::SYSTEM_CH;

const RESCAN: Duration = Duration::from_secs(1);
const RECONNECT: Duration = Duration::from_secs(1);
/// Switcher shut: the device only needs enough to time how long each app has
/// been playing (`device/wheel.py`).
const AMETER_IDLE: Duration = Duration::from_millis(200);
/// The longest this loop will ever sleep with a link open, and therefore the
/// worst-case latency on a knob move. Reads do not block (see
/// `SerialLink::read_lines`), so this cap is the only thing keeping the loop
/// responsive between meter deadlines -- and the only thing keeping it from
/// spinning: ~100 wake-ups a second, against the Python bridge's measured 408.
const MAX_WAIT: Duration = Duration::from_millis(10);

pub struct Bridge {
    audio: Audio,
    link: Option<SerialLink>,
    port: String,
    rate: f64,
    verbose: bool,
    /// What each column shows.
    keys: Vec<String>,
    /// Every app with a live session -- the switcher's candidate list.
    apps: Vec<String>,
    /// Their icons, made and sent as they turn up.
    icons: appicon::Pusher,
    /// Columns the user assigned by hand, which auto-assignment must not touch.
    pinned: Vec<Option<String>>,
    /// Is the switcher up? It meters faster while it is.
    wheel_open: bool,
    /// Last pushed level/mute per column, plus the system endpoint at NCOL.
    vol: Vec<Option<i32>>,
    muted: Vec<Option<bool>>,
    eq: ParametricEq,
    /// What the panel was last told about the EQ engine, so a change -- the
    /// default output moving, or Equalizer APO being attached or lost -- is
    /// pushed when it happens rather than at the next reconnect.
    eq_status: Option<(bool, String)>,
    /// The cheap half of that: which endpoint is default, and whether it hosts
    /// the APO. The full status (an enumerator, a registry walk for the "only
    /// attached to ..." message) is rebuilt only when this moves.
    eq_key: Option<(Option<String>, bool)>,
    sys: SysMeter,
    sys_ok: bool,
    mode: i32,
}

fn log(msg: &str) {
    crate::logging::line(msg);
}

impl Bridge {
    pub fn new(port: String, rate: f64, verbose: bool) -> Result<Self, String> {
        let pinned = pins::load();
        if pinned.iter().any(|p| p.is_some()) {
            log(&format!(
                "columns restored: {}",
                pinned
                    .iter()
                    .map(|p| p.clone().unwrap_or_else(|| "-".into()))
                    .collect::<Vec<_>>()
                    .join(" | ")
            ));
        }
        Ok(Self {
            audio: Audio::new().map_err(|e| e.to_string())?,
            link: None,
            port,
            rate: rate.clamp(1.0, 200.0),
            verbose,
            keys: Vec::new(),
            apps: Vec::new(),
            icons: appicon::Pusher::new(),
            pinned,
            wheel_open: false,
            vol: vec![None; NCOL + 1],
            muted: vec![None; NCOL + 1],
            eq: ParametricEq::new(),
            eq_status: None,
            eq_key: None,
            // The EQ screen's meter is a REAL analysis of the system output.
            // Started on demand -- see want_spectrum().
            sys: SysMeter::new(SBANDS),
            sys_ok: false,
            mode: 0,
        })
    }

    pub fn run_forever(&mut self) {
        // Logged on the way in and out of "waiting", never once per attempt.
        // The V1 daemon logged every retry and left a 2 MB file behind; a
        // daemon that says nothing at all is just as bad to debug, so it says
        // it once and then again when something changes.
        let mut waiting = false;
        loop {
            match self.connect() {
                Ok(()) => {
                    waiting = false;
                    self.resync();
                    self.session_loop();
                    // A dropped link is the NORMAL case, not an error: every
                    // deploy.py reloads the board and drops the CDC link.
                    log("device went away; waiting for it to come back");
                }
                Err(e) => {
                    if !waiting {
                        waiting = true;
                        log(&format!("waiting for the naub data port ({e})"));
                    }
                }
            }
            self.link = None;
            // Nothing to draw and nothing to read: let the capture go rather
            // than hold an audio client open for a device that is not there.
            self.want_spectrum(false);
            std::thread::sleep(RECONNECT);
        }
    }

    fn connect(&mut self) -> std::io::Result<()> {
        let name = if self.port == "auto" {
            match autodetect() {
                Some(p) => p,
                None => {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::NotFound,
                        "no naub data port",
                    ))
                }
            }
        } else {
            self.port.clone()
        };
        let mut link = SerialLink::open(&name)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))?;
        // Close off anything a previous host left half-written before our own
        // first command can be appended to it.
        let _ = link.flush_partial_line();
        log(&format!("data port: {name} at {:.0} Hz", self.rate));
        self.link = Some(link);
        Ok(())
    }

    // ---- sending ---------------------------------------------------------

    /// Any write failure drops the link, timeouts included.
    ///
    /// The tempting alternative -- treat a timeout as "device busy, skip this
    /// line, carry on" -- is what killed the firmware. A write that times out
    /// has usually already put part of the line on the wire, so carrying on
    /// appends the next line to an unterminated fragment and hands the device
    /// a token like `5hello`, which `int()` refuses and code.py dies on.
    ///
    /// There is no way to ask how many bytes actually landed, so the only
    /// honest response to a failed write is to stop trusting the stream.
    /// Reconnecting costs a quarter second and `resync()` re-pushes every bit
    /// of state; a corrupted line costs the user their panel. With the write
    /// timeout at 500 ms rather than 10 this should be a genuine fault and not
    /// merely a busy moment.
    fn send(&mut self, line: &str) {
        let failed = match &mut self.link {
            Some(l) => l.write_line(line).is_err(),
            None => return,
        };
        if failed {
            log("write failed; dropping the link rather than desyncing it");
            self.link = None;
        }
    }

    fn send_all(&mut self, lines: Vec<String>) {
        for l in lines {
            self.send(&l);
        }
    }

    // ---- what the device shows -------------------------------------------

    /// Which app each column shows. A column the user assigned from the
    /// switcher stays put; the rest track the most active sessions. Without the
    /// pinning, a hand-picked column would be overwritten the moment the
    /// busiest three changed.
    fn columns(&self) -> Vec<String> {
        let mut auto: Vec<String> = self
            .audio
            .top(NCOL * 2)
            .into_iter()
            .filter(|k| !k.is_empty() && !self.pinned.iter().any(|p| p.as_deref() == Some(k.as_str())))
            .collect();
        auto.reverse(); // so pop() takes the busiest first
        (0..NCOL)
            .map(|i| match &self.pinned[i] {
                Some(k) => k.clone(),
                None => auto.pop().unwrap_or_default(),
            })
            .collect()
    }

    /// The switcher's list: everything making noise, in full.
    fn push_apps(&mut self, force: bool) {
        let apps = self.audio.active();
        if apps == self.apps && !force {
            return;
        }
        self.apps = apps;
        let line = protocol::apps(&self.apps);
        self.send(&line);
        if self.verbose {
            log(&format!(
                "apps: {}",
                if self.apps.is_empty() { "(none)".into() } else { self.apps.join(", ") }
            ));
        }
    }

    /// Start fetching the next icon the device lacks, and send one that is
    /// ready. Called every pass: the Pusher does its own pacing, and apps arrive
    /// ahead of their art, drawing as a placeholder until it lands.
    fn pump_icons(&mut self) {
        if let Some(line) = self.icons.poll(Instant::now()) {
            self.send(&line);
        }
        let Some(key) = self.icons.next_wanted(&self.apps).cloned() else {
            return;
        };
        match self.audio.exe_of(&key) {
            Some(exe) => self.icons.fetch(&key, exe),
            None => self.icons.mark_none(&key),
        }
    }

    fn push_names(&mut self, force: bool) {
        let keys = self.columns();
        if keys == self.keys && !force {
            return;
        }
        self.keys = keys;
        let lines: Vec<String> = self
            .keys
            .iter()
            .enumerate()
            .map(|(i, k)| protocol::label(i as u8, &k.to_uppercase()))
            .collect();
        self.send_all(lines);
        log(&format!(
            "columns: {}",
            self.keys
                .iter()
                .map(|k| if k.is_empty() { "-" } else { k.as_str() })
                .collect::<Vec<_>>()
                .join(" | ")
        ));
    }

    /// Keep the panel honest about volume/mute changed elsewhere.
    fn push_state(&mut self) {
        let mut lines = Vec::new();
        for i in 0..self.keys.len() {
            let k = self.keys[i].clone();
            let v = if k.is_empty() { 0 } else { audio::to_pct(self.audio.volume(&k)) };
            if self.vol[i] != Some(v) {
                self.vol[i] = Some(v);
                lines.push(protocol::level(i as u8, v));
            }
            let m = !k.is_empty() && self.audio.muted(&k);
            if self.muted[i] != Some(m) {
                self.muted[i] = Some(m);
                lines.push(protocol::mute(i as u8, m));
            }
        }
        let v = audio::to_pct(self.audio.system_volume());
        if self.vol[NCOL] != Some(v) {
            self.vol[NCOL] = Some(v);
            lines.push(protocol::level(SYS_CH, v));
        }
        let m = self.audio.system_muted();
        if self.muted[NCOL] != Some(m) {
            self.muted[NCOL] = Some(m);
            lines.push(protocol::mute(SYS_CH, m));
        }
        self.send_all(lines);
    }

    /// Everything the EQ screen needs: what the engine can do, whether it would
    /// be heard at all, and where every band currently sits.
    fn push_eq(&mut self) {
        self.eq_status = None;
        self.eq_key = None;
        self.check_eq();
        let lines: Vec<String> = self
            .eq
            .bands
            .iter()
            .enumerate()
            .map(|(i, b)| protocol::eqband(i, &b.wire()))
            .collect();
        self.send_all(lines);
    }

    /// Re-ask whether the EQ would be heard, and tell the panel if the answer
    /// changed. Only `eqcaps` goes out: the bands are ours and have not moved.
    ///
    /// Polled with the session rescan rather than hooked to endpoint-change
    /// notifications, because one of the two things it watches -- someone
    /// running Equalizer APO's DeviceSelector -- raises no audio event at all.
    fn check_eq(&mut self) {
        let guid = self.audio.default_guid().map(str::to_string);
        let hosts = guid.as_deref().map(eqapo::hosts_apo).unwrap_or(false);
        let key = Some((guid, hosts));
        if self.eq_key == key {
            return;
        }
        self.eq_key = key;
        let status = self.eq.status();
        if self.eq_status.as_ref() == Some(&status) {
            return;
        }
        let (ok, why) = &status;
        let caps = protocol::eqcaps(
            self.eq.bands.len(),
            eqapo::FMIN,
            eqapo::FMAX,
            eqapo::MAXDB,
            48000,
            *ok,
        );
        self.send(&caps);
        log(&format!("eq: {} -- {why}", if *ok { "ready" } else { "INERT" }));
        self.eq_status = Some(status);
    }

    /// Push everything the device needs after a (re)connect. It boots with
    /// nothing and is not the source of truth for volume -- PROTOCOL.md §3.
    fn resync(&mut self) {
        self.keys.clear();
        self.apps.clear();
        self.vol = vec![None; NCOL + 1];
        self.muted = vec![None; NCOL + 1];
        self.icons.reset();
        let hello = protocol::hello();
        self.send(&hello);
        self.push_apps(true);
        self.push_names(true);
        self.push_state();
        self.push_eq();
    }

    /// Run the loopback capture only while the EQ screen is up. It is a thread
    /// reading 48 kHz audio and an open audio client on the user's machine, and
    /// exactly one of four modes draws it.
    fn want_spectrum(&mut self, on: bool) {
        if on && !self.sys_ok {
            let t0 = Instant::now();
            self.sys_ok = self.sys.start();
            if self.sys_ok {
                log(&format!(
                    "  spectrum on ({}, {} ms)",
                    self.sys.name().unwrap_or("?"),
                    t0.elapsed().as_millis()
                ));
            } else {
                log(&format!("  spectrum unavailable: {}", self.sys.error().unwrap_or("?")));
            }
        } else if !on && self.sys_ok {
            self.sys.stop();
            self.sys_ok = false;
            log("  spectrum off");
        }
    }

    // ---- what the device says --------------------------------------------

    fn handle(&mut self, ev: DeviceEvent) {
        match ev {
            DeviceEvent::Banner(line) => {
                log(&format!("  device: {line}"));
                // Said after a reload too, and a reload empties its icon store.
                self.icons.reset();
                self.push_apps(true);
                self.push_names(true);
                self.push_state();
            }
            DeviceEvent::Vol(ch, v) => {
                if ch == SYS_CH {
                    self.audio.set_system_volume(v as f32 / 100.0);
                } else if let Some(k) = self.keys.get(ch as usize).filter(|k| !k.is_empty()) {
                    self.audio.set_volume(&k.clone(), v as f32 / 100.0);
                }
                // Ours now; do not echo it back as a `level`.
                if (ch as usize) <= NCOL {
                    self.vol[ch as usize] = Some(v);
                }
                if self.verbose {
                    log(&format!("  knob {ch} -> {v}%"));
                }
            }
            DeviceEvent::Mute(ch, m) => {
                if ch == SYS_CH {
                    self.audio.set_system_mute(m);
                } else if let Some(k) = self.keys.get(ch as usize).filter(|k| !k.is_empty()) {
                    self.audio.set_mute(&k.clone(), m);
                }
                if (ch as usize) <= NCOL {
                    self.muted[ch as usize] = Some(m);
                }
                log(&format!("  mute {ch} -> {}", if m { 1 } else { 0 }));
            }
            DeviceEvent::Pick(ch, key) => self.pick(ch, key),
            DeviceEvent::Mode(idx) => {
                self.mode = idx;
                log(&format!("  mode -> {idx}"));
                self.want_spectrum(idx == EQ_MODE);
            }
            DeviceEvent::Wheel(open) => self.wheel_open = open,
            DeviceEvent::Btn(n, down) => {
                // BTN3-5 are media transport and are ALSO sent as USB HID,
                // which is what actually moves the track; this is telemetry.
                if down && self.verbose {
                    log(&format!("  aux key {n}"));
                }
            }
            DeviceEvent::EqParam(i, param, value) => {
                if i >= self.eq.bands.len() {
                    return;
                }
                let ok = match param.as_str() {
                    "kind" => self.eq.set_kind(i, &value),
                    "on" => {
                        self.eq.set_on(i, value == "1");
                        true
                    }
                    "gain" => value.parse().map(|v| self.eq.set_gain(i, v)).is_ok(),
                    "freq" => value.parse().map(|v| self.eq.set_freq(i, v)).is_ok(),
                    "q" => value.parse().map(|v| self.eq.set_q(i, v)).is_ok(),
                    _ => false,
                };
                if ok && self.verbose {
                    log(&format!("  eq band {i} {param} -> {value}"));
                }
            }
            DeviceEvent::EqFlat => {
                self.eq.flat();
                log("  eq flat");
            }
            DeviceEvent::Pong => {}
        }
    }

    /// One app, one column. Taking an app another column is PINNED to swaps the
    /// two rather than showing it twice: two columns driving one fader would
    /// fight, and whichever lost would look broken. What this column had goes
    /// where the app came from, which is what "swap" means to the hand that did
    /// it.
    ///
    /// Only pinned collisions. A column that merely happens to be showing the
    /// app on its own has nothing to swap -- pinning it here removes the app
    /// from the auto pool, so that column picks the next busiest by itself, and
    /// forcing OUR app onto it would be pinning a column the user never chose.
    fn pick(&mut self, ch: u8, key: String) {
        let ch = ch as usize;
        if ch >= NCOL {
            return;
        }
        let prev = self.pinned[ch]
            .clone()
            .or_else(|| self.keys.get(ch).cloned())
            .filter(|s| !s.is_empty());
        for j in 0..NCOL {
            if j != ch && self.pinned[j].as_deref() == Some(key.as_str()) {
                self.pinned[j] = prev.clone();
                log(&format!(
                    "  column {j} takes {}",
                    prev.clone().unwrap_or_else(|| "(auto)".into())
                ));
            }
        }
        self.pinned[ch] = Some(key.clone());
        pins::save(&self.pinned);
        log(&format!("  column {ch} pinned to {key}"));
        self.push_names(true);
        self.push_state();
    }

    // ---- the loop --------------------------------------------------------

    fn session_loop(&mut self) {
        let period = Duration::from_secs_f64(1.0 / self.rate);
        let mut next_meter = Instant::now();
        let mut next_ameter = Instant::now();
        let mut next_scan = Instant::now() + RESCAN;

        loop {
            // Takes only what is already waiting and returns; the sleep at the
            // bottom of the loop does the pacing.
            let lines = match &mut self.link {
                Some(l) => match l.read_lines() {
                    Ok(v) => v,
                    Err(_) => return,
                },
                None => return,
            };
            for line in lines {
                if let Some(ev) = protocol::parse(&line) {
                    self.handle(ev);
                }
                if self.link.is_none() {
                    return;
                }
            }

            self.pump_icons();

            let now = Instant::now();
            if now >= next_meter {
                next_meter = now + period;
                let keys = self.keys.clone();
                let lv = self.audio.levels(&keys, audio::CURVE);
                let pct: Vec<i32> = lv.iter().map(|v| audio::to_pct(*v)).collect();
                let line = protocol::meter(&pct);
                self.send(&line);

                // Full rate while the switcher is up, because its bar has
                // ballistics; a fifth of a second otherwise, which is all the
                // device needs to tell a track from a notification chime. This
                // is a peak poll per app per send, so it is real work.
                if !self.apps.is_empty() && (self.wheel_open || now >= next_ameter) {
                    next_ameter = now + AMETER_IDLE;
                    let apps = self.apps.clone();
                    let av: Vec<i32> = self
                        .audio
                        .levels(&apps, audio::CURVE)
                        .iter()
                        .map(|v| audio::to_pct(*v))
                        .collect();
                    let line = protocol::ameter(&av);
                    self.send(&line);
                }
                if self.sys_ok && self.mode == EQ_MODE {
                    let bands: Vec<i32> =
                        self.sys.bands().iter().map(|v| (*v * 100.0 + 0.5) as i32).collect();
                    let line = protocol::smeter(&bands);
                    self.send(&line);
                }
            }

            // Debounced: a knob per detent would otherwise make Equalizer APO
            // re-parse the user's whole config a hundred times a second.
            self.eq.tick();

            if now >= next_scan {
                next_scan = now + RESCAN;
                // WASAPI can be mid-change here; ride it out rather than
                // dropping the link, because the panel going dark is a far
                // worse outcome than a second of stale levels.
                if self.audio.refresh() {
                    self.push_apps(false);
                    self.push_names(false);
                    self.push_state();
                    if self.sys_ok {
                        self.sys.restart_if_dead();
                    }
                }
                self.check_eq();
            }

            if self.link.is_none() {
                return;
            }
            // Sleep only the remainder, and only if the serial read did not
            // already absorb it.
            let now = Instant::now();
            let wait = next_meter.saturating_duration_since(now).min(MAX_WAIT);
            if wait > Duration::from_millis(1) {
                std::thread::sleep(wait - Duration::from_millis(1));
            }
        }
    }
}
