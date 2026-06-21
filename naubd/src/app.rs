//! Orchestrator: bridge device events <-> Windows audio + e-paper display.
//! Ports naub_host/app.py: sticky selections, active-app cycle pool, repaint dedupe.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::audio::{Audio, SYSTEM_KEY};
use crate::config::Config;
use crate::icons::icon_for;
use crate::link::{autodetect, SerialLink};
use crate::protocol::{self, DeviceEvent};
use crate::state::{self, Saved, StateMap};

const LEVEL_PUSH_THRESHOLD: i32 = 2;
const STATE_FILE: &str = "naub_state.json";

#[derive(PartialEq, Clone)]
enum Shown {
    Nothing,           // never drawn (force repaint)
    Blank,             // "--" placeholder
    Bound(String, bool), // (key, active) currently on the panel
}

struct Channel {
    matches: Vec<String>,
    cfg_label: String,
    is_system: bool,
    key: Option<String>,
    label: String,
    exe_path: Option<String>,
    level: Option<i32>,
    mute: Option<bool>,
    shown: Shown,
}

pub struct App {
    cfg: Config,
    audio: Audio,
    channels: BTreeMap<u8, Channel>,
    link: Option<SerialLink>,
    last_input: Instant,
    state_path: PathBuf,
    last_state_json: String,
}

fn log(msg: &str) {
    crate::logging::line(msg);
}

/// The canonical, launch-location-independent path for sticky selections:
/// `%APPDATA%\naub\naub_state.json`. On first use there, adopt a legacy state
/// file sitting beside the config so existing selections aren't lost on upgrade.
fn stable_state_path(config_path: &Path) -> PathBuf {
    let canonical = dirs::config_dir().map(|d| {
        let dir = d.join("naub");
        let _ = std::fs::create_dir_all(&dir);
        dir.join(STATE_FILE)
    });
    let canonical = match canonical {
        Some(p) => p,
        None => return PathBuf::from(STATE_FILE), // no APPDATA: fall back to cwd
    };
    if !canonical.exists() {
        let legacy = config_path
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .join(STATE_FILE);
        if legacy.exists() && legacy != canonical {
            if let Ok(data) = std::fs::read(&legacy) {
                if std::fs::write(&canonical, data).is_ok() {
                    log(&format!(
                        "migrated sticky state {} -> {}",
                        legacy.display(),
                        canonical.display()
                    ));
                }
            }
        }
    }
    canonical
}

fn pretty(key: &str) -> String {
    let base = key.strip_suffix(".exe").unwrap_or(key);
    let mut c = base.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + c.as_str(),
        None => String::new(),
    }
}

impl App {
    pub fn new(cfg: Config) -> windows::core::Result<Self> {
        let mut channels = BTreeMap::new();
        for (id, c) in &cfg.channels {
            let is_system = c.matches.iter().any(|m| m == SYSTEM_KEY);
            channels.insert(
                *id,
                Channel {
                    matches: c.matches.clone(),
                    cfg_label: c.label.clone(),
                    is_system,
                    key: None,
                    label: String::new(),
                    exe_path: None,
                    level: None,
                    mute: None,
                    shown: Shown::Nothing,
                },
            );
        }
        // Sticky selections live in a FIXED per-user location, not beside the
        // config -- so the device's memory survives no matter where the exe or
        // config is launched from (double-click, cargo run, autostart, a moved
        // install). Migrate a legacy beside-config file on first run.
        let state_path = stable_state_path(&cfg.path);

        let mut app = App {
            cfg,
            audio: Audio::new()?,
            channels,
            link: None,
            last_input: Instant::now(),
            state_path,
            last_state_json: String::new(),
        };
        app.load_state();
        Ok(app)
    }

    // ---- connection / main loop ----

    pub fn run_forever(&mut self) {
        loop {
            match self.connect() {
                Ok(()) => self.session_loop(),
                Err(e) => log(&format!("link error: {e} -- retrying in 3s")),
            }
            self.link = None;
            self.reset_channels();
            std::thread::sleep(Duration::from_secs(3));
        }
    }

    fn connect(&mut self) -> std::io::Result<()> {
        let port = if self.cfg.port.is_empty() || self.cfg.port == "auto" {
            log("probing for naub device...");
            autodetect().ok_or_else(|| {
                std::io::Error::new(std::io::ErrorKind::NotFound, "no naub device found")
            })?
        } else {
            self.cfg.port.clone()
        };
        log(&format!("opening {port}"));
        let mut link = SerialLink::open(&port)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e.to_string()))?;
        link.write_line(&protocol::hello())?;
        self.link = Some(link);
        Ok(())
    }

    /// Keep the sticky binding; only force a repaint on reconnect.
    fn reset_channels(&mut self) {
        for ch in self.channels.values_mut() {
            ch.shown = Shown::Nothing;
        }
    }

    fn session_loop(&mut self) {
        let mut last_reconcile: Option<Instant> = None; // None -> reconcile now
        loop {
            let lines = match self.link.as_mut().unwrap().read_lines() {
                Ok(l) => l,
                Err(_) => return, // disconnected -> reconnect
            };
            for line in lines {
                self.handle_event(&line);
            }
            let idle = self.last_input.elapsed() > Duration::from_millis(200);
            let due = last_reconcile
                .map_or(true, |t| t.elapsed().as_secs_f64() >= self.cfg.refresh_seconds);
            if idle && due {
                self.reconcile();
                last_reconcile = Some(Instant::now());
            }
            std::thread::sleep(Duration::from_millis(5));
        }
    }

    fn send(&mut self, line: &str) {
        if let Some(l) = self.link.as_mut() {
            let _ = l.write_line(line);
        }
        // Pace the link: the firmware stops reading its small USB-CDC RX buffer
        // while it redraws a channel, so a back-to-back burst (esp. the ~400 B
        // icon lines) overruns it and the base64 arrives truncated. A short gap
        // lets the device drain between lines. Reconnect sends only ~16 lines, so
        // the added latency is negligible.
        let pause_ms = if line.starts_with("icon ") { 15 } else { 3 };
        std::thread::sleep(std::time::Duration::from_millis(pause_ms));
    }

    fn send_all(&mut self, lines: Vec<String>) {
        for l in lines {
            self.send(&l);
        }
    }

    // ---- device -> us ----

    fn handle_event(&mut self, line: &str) {
        let ev = match protocol::parse(line) {
            Some(e) => e,
            None => return,
        };
        if let DeviceEvent::Banner(b) = &ev {
            log(&format!("connected: {b}"));
            return;
        }
        self.last_input = Instant::now();
        match ev {
            DeviceEvent::Vol(ch, v) => self.apply_volume(ch, v),
            DeviceEvent::Mute(ch, m) => self.apply_mute(ch, m),
            DeviceEvent::Btn(_n, state) => self.apply_button(state),
            DeviceEvent::Cycle(ch, dir) => self.cycle(ch, dir),
            DeviceEvent::Pong | DeviceEvent::Banner(_) => {}
        }
    }

    fn apply_volume(&mut self, id: u8, value: i32) {
        let ch = match self.channels.get_mut(&id) {
            Some(c) => c,
            None => return,
        };
        ch.level = Some(value);
        let ok = if ch.is_system {
            self.audio.set_system_volume(value)
        } else if let Some(k) = ch.key.clone() {
            self.audio.set_app_volume(&k, value)
        } else {
            false
        };
        if !ok {
            log(&format!("ch{id} vol {value} (no target)"));
        }
    }

    fn apply_mute(&mut self, id: u8, muted: bool) {
        let ch = match self.channels.get_mut(&id) {
            Some(c) => c,
            None => return,
        };
        ch.mute = Some(muted);
        if ch.is_system {
            self.audio.set_system_mute(muted);
        } else if let Some(k) = ch.key.clone() {
            self.audio.set_app_mute(&k, muted);
        }
        log(&format!("ch{id} mute={}", muted as u8));
    }

    fn apply_button(&mut self, state: bool) {
        // V1 default: Button 1 toggles system (master) mute.
        self.audio.set_system_mute(state);
        log(&format!("btn -> system mute={}", state as u8));
    }

    // ---- hold-and-turn: cycle a channel's source ----

    fn cycle(&mut self, id: u8, direction: i32) {
        let is_system = self.channels.get(&id).map(|c| c.is_system).unwrap_or(true);
        if is_system {
            return; // system channel is pinned
        }
        self.audio.snapshot();
        let cur_key = self.channels.get(&id).and_then(|c| c.key.clone());
        let taken: Vec<String> = self
            .channels
            .iter()
            .filter(|(oid, oc)| **oid != id && !oc.is_system)
            .filter_map(|(_, oc)| oc.key.clone())
            .collect();
        let candidates: Vec<String> = self
            .audio
            .active_app_keys()
            .into_iter()
            .filter(|k| !taken.contains(k))
            .collect();
        if candidates.is_empty() {
            log(&format!("ch{id} cycle: no free active sources"));
            return;
        }
        let new_key = match cur_key.as_ref().and_then(|k| candidates.iter().position(|c| c == k)) {
            Some(i) => {
                let n = candidates.len() as i32;
                let j = (i as i32 + direction).rem_euclid(n) as usize;
                candidates[j].clone()
            }
            None => {
                if direction >= 0 {
                    candidates[0].clone()
                } else {
                    candidates[candidates.len() - 1].clone()
                }
            }
        };
        if Some(&new_key) == cur_key.as_ref() {
            return; // only one option
        }
        log(&format!("ch{id} cycle -> {new_key}"));
        let mut out = Vec::new();
        if let Some(ch) = self.channels.get_mut(&id) {
            adopt(id, ch, new_key, &self.audio, &mut out);
        }
        self.send_all(out);
        self.save_state();
    }

    // ---- reconcile (us -> device) ----

    fn reconcile(&mut self) {
        self.audio.snapshot();
        let mut out = Vec::new();
        for (id, ch) in self.channels.iter_mut() {
            if ch.key.is_none() {
                // Seed from config default the first time a match is running;
                // sticky thereafter (a later close does not revert to config).
                match resolve_match(&ch.matches, &self.audio) {
                    Some(key) => adopt(*id, ch, key, &self.audio, &mut out),
                    None => show_blank(*id, ch, &mut out),
                }
            } else {
                present(*id, ch, &self.audio, &mut out);
            }
        }
        self.send_all(out);
        self.save_state();
    }

    // ---- sticky persistence ----

    fn load_state(&mut self) {
        let map = state::load(&self.state_path);
        for (k, v) in &map {
            if let Ok(id) = k.parse::<u8>() {
                if let Some(ch) = self.channels.get_mut(&id) {
                    ch.key = v.key.clone();
                    ch.label = v.label.clone();
                    ch.exe_path = v.exe_path.clone();
                    ch.level = v.level;
                    ch.mute = v.mute;
                }
            }
        }
        // Restored selections are known audio apps -> cyclable immediately.
        for ch in self.channels.values() {
            if let Some(k) = &ch.key {
                if !ch.is_system {
                    self.audio.mark_seen(k);
                }
            }
        }
        self.last_state_json = state::to_json(&self.snapshot_state());
    }

    fn snapshot_state(&self) -> StateMap {
        let mut map = StateMap::new();
        for (id, ch) in &self.channels {
            if let Some(key) = &ch.key {
                map.insert(
                    id.to_string(),
                    Saved {
                        key: Some(key.clone()),
                        label: ch.label.clone(),
                        exe_path: ch.exe_path.clone(),
                        level: ch.level,
                        mute: ch.mute,
                    },
                );
            }
        }
        map
    }

    fn save_state(&mut self) {
        let json = state::to_json(&self.snapshot_state());
        if json == self.last_state_json {
            return;
        }
        if let Err(e) = state::save(&self.state_path, &json) {
            log(&format!("state save failed: {e}"));
        } else {
            self.last_state_json = json;
        }
    }
}

// ---- free helpers (avoid &mut self borrow conflicts with audio/channels) ----

fn resolve_match(matches: &[String], audio: &Audio) -> Option<String> {
    if matches.iter().any(|m| m == SYSTEM_KEY) {
        return Some(SYSTEM_KEY.to_string());
    }
    matches.iter().find(|m| audio.has_app(m)).cloned()
}

fn label_for(ch: &Channel, key: &str) -> String {
    if !ch.cfg_label.is_empty() && ch.matches.iter().any(|m| m == key) {
        ch.cfg_label.clone()
    } else {
        pretty(key)
    }
}

fn ch_active(ch: &Channel, audio: &Audio) -> bool {
    if ch.is_system {
        true
    } else {
        ch.key.as_ref().map(|k| audio.has_app(k)).unwrap_or(false)
    }
}

fn ch_levels(ch: &Channel, audio: &Audio) -> Option<(i32, bool)> {
    if ch.is_system {
        audio.get_system()
    } else {
        ch.key.as_ref().and_then(|k| audio.get_app(k))
    }
}

fn adopt(id: u8, ch: &mut Channel, key: String, audio: &Audio, out: &mut Vec<String>) {
    ch.label = label_for(ch, &key);
    if let Some(p) = audio.exe_path(&key) {
        ch.exe_path = Some(p);
    }
    ch.key = Some(key);
    ch.shown = Shown::Nothing; // force full repaint
    present(id, ch, audio, out);
}

fn present(id: u8, ch: &mut Channel, audio: &Audio, out: &mut Vec<String>) {
    let key = match &ch.key {
        Some(k) => k.clone(),
        None => return,
    };
    let active = ch_active(ch, audio);
    let desired = Shown::Bound(key.clone(), active);
    if ch.shown != desired {
        if active {
            if let Some((lvl, mu)) = ch_levels(ch, audio) {
                ch.level = Some(lvl);
                ch.mute = Some(mu);
            }
        }
        let label = if ch.label.is_empty() { pretty(&key) } else { ch.label.clone() };
        out.push(protocol::label(id, &label));
        push_icon(id, ch, audio, out);
        out.push(protocol::level(id, ch.level.unwrap_or(0)));
        out.push(protocol::mute(id, ch.mute.unwrap_or(false)));
        ch.shown = desired;
    } else if active {
        sync_external(id, ch, audio, out);
    }
}

fn push_icon(id: u8, ch: &mut Channel, audio: &Audio, out: &mut Vec<String>) {
    // Prefer the live exe path (cache it); fall back to the remembered one so the
    // icon still renders when the app is closed.
    if let Some(k) = &ch.key {
        if let Some(p) = audio.exe_path(k) {
            ch.exe_path = Some(p);
        }
    }
    match ch.exe_path.as_deref().and_then(icon_for) {
        Some((w, h, packed)) => out.push(protocol::icon(id, w, h, &packed)),
        None => out.push(protocol::iconclear(id)),
    }
}

fn show_blank(id: u8, ch: &mut Channel, out: &mut Vec<String>) {
    if ch.shown == Shown::Blank {
        return;
    }
    out.push(protocol::iconclear(id));
    out.push(protocol::label(id, "--"));
    out.push(protocol::level(id, 0));
    ch.shown = Shown::Blank;
}

fn sync_external(id: u8, ch: &mut Channel, audio: &Audio, out: &mut Vec<String>) {
    let (lvl, mu) = match ch_levels(ch, audio) {
        Some(v) => v,
        None => return,
    };
    if ch.level.map_or(true, |l| (lvl - l).abs() >= LEVEL_PUSH_THRESHOLD) {
        out.push(protocol::level(id, lvl));
        ch.level = Some(lvl);
    }
    if ch.mute != Some(mu) {
        out.push(protocol::mute(id, mu));
        ch.mute = Some(mu);
    }
}
