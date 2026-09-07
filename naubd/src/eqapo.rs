//! N-band parametric EQ, driven into Equalizer APO.
//!
//! naub writes no DSP. Equalizer APO is already a parametric EQ inside the
//! Windows audio pipeline; this writes it a config file and gets out of the
//! way. Filter syntax follows its Configuration reference: PK / LSC / HSC take
//! `Fc <hz> Hz Gain <db> dB Q <q>`.
//!
//! A port of `firmware/v2/host/eqapo.py`. Three things it is careful about,
//! each learned the hard way:
//!
//! * **config.txt belongs to the user.** It may hold their own PEQ export and
//!   Includes for their channel routing. We own exactly one file,
//!   `naub-eq.txt`, and add exactly one guarded Include line to theirs.
//!   Nothing else is ever rewritten, and [`ParametricEq::remove`] takes our
//!   line back out byte for byte.
//! * **Installed is not enabled.** Equalizer APO attaches per ENDPOINT, via its
//!   Configurator. Measured: it was attached only to a Bluetooth speaker that
//!   was not even present, while the default output had no APO at all -- so a
//!   config write did precisely nothing, and no amount of checking
//!   `registry -> installed?` would have revealed it. [`ParametricEq::status`]
//!   asks about the *current default endpoint*, which is the question that
//!   matters.
//! * **Writes are debounced.** A write costs us a fraction of a millisecond,
//!   but it makes Equalizer APO re-parse the whole config -- the user's filters
//!   included -- and rebuild its graph. A knob at one detent per 10 ms would do
//!   that 100 times a second, so [`ParametricEq::set_band`] only marks dirty
//!   and [`ParametricEq::tick`] writes at most every `WRITE_MS`.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use winreg::enums::HKEY_LOCAL_MACHINE;
use winreg::RegKey;

pub const INCLUDE_NAME: &str = "naub-eq.txt";
pub const INCLUDE_LINE: &str = "Include: naub-eq.txt";
pub const MARKER: &str = "# naub parametric EQ (managed by the naub host)";

pub const MAXDB: f64 = 12.0; // knob full scale, +/-
pub const FMIN: f64 = 20.0;
pub const FMAX: f64 = 20000.0;
const QMIN: f64 = 0.3;
const QMAX: f64 = 10.0;
const WRITE_MS: u64 = 50;

/// Three bands, because there are three top knobs -- and these defaults MUST
/// match `device/eqscreen`'s LOW / MID / HIGH, or the panel draws a curve the
/// host never applies. Low shelf, peak, high shelf: the tone-control split.
const DEFAULTS: [(&str, f64, f64); 3] = [("LSC", 120.0, 0.7), ("PK", 1000.0, 0.9), ("HSC", 4000.0, 0.7)];

/// Preamp policy. `-(sum of positive gains)` can never clip, but measured on
/// real audio it also means maxing one band drops everything else by 12.3 dB:
/// the boosted region barely moves and it just sounds like the volume went
/// down. A hardware mixer's EQ boosts, and this one has meters and a volume
/// knob right next to it, so boost is boost. Set true to trade that for
/// guaranteed headroom.
const AUTO_PREAMP: bool = false;

const RENDER: &str = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render";
const CHILD_APOS: &str = r"SOFTWARE\EqualizerAPO\Child APOs";
const NAME_VALUE: &str = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6";

/// Frequency as Equalizer APO will accept it.
///
/// NOT the shortest float form: Python's `%g` switches to scientific notation
/// at five significant digits, so 20000 came out as `2e+04` and Equalizer APO
/// silently dropped that filter -- i.e. winding a band to the top of its range
/// turned it off. Rust's `{}` does not do that, but the integer form is pinned
/// here anyway so the output cannot drift back.
pub fn fmt_hz(hz: f64) -> String {
    if hz >= 1000.0 {
        format!("{:.0}", hz)
    } else {
        fmt_sig4(hz)
    }
}

/// Python `%.4g` for the sub-1 kHz range: 4 significant digits, no trailing
/// zeros, never exponential (values here are 20..999).
fn fmt_sig4(v: f64) -> String {
    let s = format!("{:.*}", 4usize.saturating_sub(digits_before_point(v)), v);
    let s = if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s
    };
    s
}

fn digits_before_point(v: f64) -> usize {
    let a = v.abs();
    if a < 1.0 {
        1
    } else {
        (a.log10().floor() as usize) + 1
    }
}

/// Trim a float the way `%.3g` would, for Q on the wire and in the file.
fn fmt_sig3(v: f64) -> String {
    let s = format!("{:.*}", 3usize.saturating_sub(digits_before_point(v)), v);
    if s.contains('.') {
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    } else {
        s
    }
}

#[derive(Clone, PartialEq, Debug)]
pub struct Band {
    pub kind: String,
    pub f0: f64,
    pub db: f64,
    pub q: f64,
    pub on: bool,
}

impl Band {
    pub fn line(&self) -> String {
        format!(
            "Filter: {} {} Fc {} Hz Gain {:.2} dB Q {}",
            if self.on { "ON" } else { "OFF" },
            self.kind,
            fmt_hz(self.f0),
            self.db,
            fmt_sig3(self.q)
        )
    }

    /// The compact form the protocol carries (`eqband <i> <this>`).
    pub fn wire(&self) -> String {
        format!(
            "{} {} {:.2} {} {}",
            self.kind,
            fmt_hz(self.f0),
            self.db,
            fmt_sig3(self.q),
            if self.on { 1 } else { 0 }
        )
    }
}

pub struct ParametricEq {
    config_path: Option<PathBuf>,
    pub bands: Vec<Band>,
    dirty: bool,
    last_write: Instant,
}

/// A band at 0 dB is already a no-op, so bands are ON by default and `on` is
/// reserved for deliberately parking one. They used to default to OFF, which
/// meant every filter was written `Filter: OFF ...`: Equalizer APO ignored the
/// lot and the EQ silently did nothing while the panel drew the curve.
fn default_bands() -> Vec<Band> {
    DEFAULTS
        .iter()
        .map(|(kind, f0, q)| Band {
            kind: (*kind).to_string(),
            f0: *f0,
            db: 0.0,
            q: *q,
            on: true,
        })
        .collect()
}

impl ParametricEq {
    pub fn new() -> Self {
        let mut eq = Self {
            config_path: config_path(),
            bands: default_bands(),
            dirty: false,
            last_write: Instant::now() - Duration::from_millis(WRITE_MS * 2),
        };
        if eq.available() {
            eq.restore();
        }
        eq
    }

    pub fn available(&self) -> bool {
        self.config_path.is_some()
    }

    fn eq_file(&self) -> Option<PathBuf> {
        self.config_path.as_ref().map(|p| p.join(INCLUDE_NAME))
    }

    fn config_file(&self) -> Option<PathBuf> {
        self.config_path.as_ref().map(|p| p.join("config.txt"))
    }

    /// `(ok, why)`. `ok` means a filter written now would actually be heard:
    /// Equalizer APO installed AND attached to the endpoint currently in use.
    /// The second half is the one that bites -- see the module docs.
    pub fn status(&self) -> (bool, String) {
        if !self.available() {
            return (false, "Equalizer APO not installed".into());
        }
        let attached = attached_endpoints();
        if attached.is_empty() {
            return (false, "Equalizer APO installed but attached to no device".into());
        }
        let mut names: Vec<&String> = attached.iter().map(|(n, _)| n).collect();
        names.sort();
        let list = names.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ");
        let cur = match crate::audio::default_endpoint_name() {
            Some(c) => c,
            None => return (true, format!("attached to {list}; current device unknown")),
        };
        for (name, active) in &attached {
            // The registry holds the DEVICE name ("High Definition Audio
            // Device") while the endpoint reports "Speakers (High Definition
            // Audio Device)", so one is embedded in the other at a non-zero
            // offset. Comparing truncated prefixes fails on exactly that case:
            // substring, both ways, full strings.
            let a = name.trim().to_lowercase();
            let b = cur.trim().to_lowercase();
            if a.len() >= 6 && b.len() >= 6 && (b.contains(&a) || a.contains(&b)) {
                return if *active {
                    (true, format!("active on {cur}"))
                } else {
                    (false, format!("{name} is attached but not present"))
                };
            }
        }
        (
            false,
            format!("not attached to {cur} (only to {list}) -- add it in the Equalizer APO Configurator"),
        )
    }

    pub fn set_gain(&mut self, i: usize, db: f64) {
        if let Some(b) = self.bands.get_mut(i) {
            b.db = db.clamp(-MAXDB, MAXDB);
            self.dirty = true;
        }
    }

    pub fn set_freq(&mut self, i: usize, f0: f64) {
        if let Some(b) = self.bands.get_mut(i) {
            b.f0 = f0.clamp(FMIN, FMAX);
            self.dirty = true;
        }
    }

    pub fn set_q(&mut self, i: usize, q: f64) {
        if let Some(b) = self.bands.get_mut(i) {
            b.q = q.clamp(QMIN, QMAX);
            self.dirty = true;
        }
    }

    pub fn set_kind(&mut self, i: usize, kind: &str) -> bool {
        if !matches!(kind, "PK" | "LSC" | "HSC") {
            return false;
        }
        if let Some(b) = self.bands.get_mut(i) {
            b.kind = kind.to_string();
            self.dirty = true;
        }
        true
    }

    pub fn set_on(&mut self, i: usize, on: bool) {
        if let Some(b) = self.bands.get_mut(i) {
            b.on = on;
            self.dirty = true;
        }
    }

    /// Back to the default flat profile: gains to 0 AND the shapes back to
    /// DEFAULTS, matching what the device's reset key does, so the two cannot
    /// end up disagreeing about what "flat" means.
    ///
    /// Bands stay ON: a 0 dB filter is already a no-op, and switching them off
    /// would make the next gain change do nothing until something turned them
    /// back on -- which is exactly how the EQ came to be silently dead.
    pub fn flat(&mut self) {
        for (i, b) in self.bands.iter_mut().enumerate() {
            if let Some((kind, f0, q)) = DEFAULTS.get(i) {
                b.kind = (*kind).to_string();
                b.f0 = *f0;
                b.q = *q;
            }
            b.db = 0.0;
            b.on = true;
        }
        self.dirty = true;
    }

    /// Call from the host loop; writes at most every `WRITE_MS`.
    pub fn tick(&mut self) -> bool {
        if !self.dirty || self.last_write.elapsed() < Duration::from_millis(WRITE_MS) {
            return false;
        }
        self.flush()
    }

    /// Write immediately. Never fails outward: this runs inside the serial
    /// loop, and an EQ that fails to write is a far smaller problem than a
    /// panel that stops updating.
    pub fn flush(&mut self) -> bool {
        if !self.available() {
            self.dirty = false;
            return false;
        }
        if let Err(e) = self.write() {
            crate::logging::line(&format!("EQ write failed: {e}"));
        }
        if let Err(e) = self.ensure_include() {
            crate::logging::line(&format!("EQ include failed: {e}"));
        }
        self.dirty = false;
        self.last_write = Instant::now();
        true
    }

    /// Headroom, in dB. Zero unless AUTO_PREAMP -- see the note there.
    fn preamp(&self) -> f64 {
        if !AUTO_PREAMP {
            return 0.0;
        }
        -self.bands.iter().filter(|b| b.on && b.db > 0.0).map(|b| b.db).sum::<f64>()
    }

    pub fn render(&self) -> String {
        let mut lines = vec![
            MARKER.to_string(),
            format!(
                "# {} parametric bands driven by the device; do not hand-edit.",
                self.bands.len()
            ),
            format!("Preamp: {:.2} dB", self.preamp()),
        ];
        lines.extend(self.bands.iter().map(|b| b.line()));
        lines.join("\n") + "\n"
    }

    /// Write the config, atomically when Windows lets us.
    ///
    /// Atomic is preferable because Equalizer APO watches this file and a
    /// half-written one is a config it will try to parse. But it watches it by
    /// holding it OPEN, so the rename intermittently fails with a sharing
    /// violation -- which, unhandled, would take the whole link down over a
    /// cosmetic nicety. So: try the swap, retry briefly, then fall back to
    /// writing in place. A torn read self-corrects on the next write, which is
    /// at most WRITE_MS away.
    fn write(&self) -> std::io::Result<()> {
        let path = match self.eq_file() {
            Some(p) => p,
            None => return Ok(()),
        };
        let text = self.render();
        let tmp = path.with_extension("txt.tmp");
        if std::fs::write(&tmp, &text).is_ok() {
            for _ in 0..3 {
                if std::fs::rename(&tmp, &path).is_ok() {
                    return Ok(());
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            let _ = std::fs::remove_file(&tmp); // never leave litter in their config
        }
        std::fs::write(&path, text)
    }

    /// Add our one line to the user's config.txt, once.
    ///
    /// Appended verbatim, with no decorative blank line, so `remove` can take
    /// back exactly what was added. An earlier version added a leading blank
    /// line and removed only the two content lines, so every install/uninstall
    /// cycle grew the user's file by one newline.
    fn ensure_include(&self) -> std::io::Result<()> {
        let path = match self.config_file() {
            Some(p) => p,
            None => return Ok(()),
        };
        let text = read_raw(&path);
        for raw in text.lines() {
            let t = raw.trim();
            if t.starts_with('#') {
                continue;
            }
            if squash(t) == squash(INCLUDE_LINE) {
                return Ok(());
            }
        }
        // config.txt here is CRLF, and silently rewriting it LF would show up
        // as a diff across the user's whole file.
        let nl = if text.contains("\r\n") { "\r\n" } else { "\n" };
        let mut add = String::new();
        if !text.is_empty() && !text.ends_with('\n') && !text.ends_with('\r') {
            add.push_str(nl);
        }
        add.push_str(MARKER);
        add.push_str(nl);
        add.push_str(INCLUDE_LINE);
        add.push_str(nl);
        let mut out = text;
        out.push_str(&add);
        std::fs::write(&path, out)
    }

    /// Take naub back out of the user's config entirely, byte for byte.
    pub fn remove(&self) {
        let path = match self.config_file() {
            Some(p) => p,
            None => return,
        };
        let text = read_raw(&path);
        let mut out = text.clone();
        let mut done = false;
        for nl in ["\r\n", "\n"] {
            let block = format!("{MARKER}{nl}{INCLUDE_LINE}{nl}");
            if out.contains(&block) {
                out = out.replace(&block, "");
                done = true;
                break;
            }
        }
        if !done {
            // Older or hand-edited forms: drop the lines, at the cost of not
            // being able to promise byte-identity.
            let nl = if out.contains("\r\n") { "\r\n" } else { "\n" };
            let keep: Vec<&str> = out
                .lines()
                .filter(|ln| ln.trim() != MARKER && squash(ln.trim()) != squash(INCLUDE_LINE))
                .collect();
            let mut rebuilt = keep.join(nl);
            if !keep.is_empty() {
                rebuilt.push_str(nl);
            }
            out = rebuilt;
        }
        if out != text {
            let _ = std::fs::write(&path, out);
        }
        if let Some(f) = self.eq_file() {
            let _ = std::fs::remove_file(f);
        }
    }

    /// Read our own file back, so the EQ survives a host restart.
    ///
    /// A file written by a different band layout is IGNORED rather than
    /// half-applied. That is not hypothetical: a five-band file left by an
    /// older build restored `OFF` onto every band, so the EQ silently did
    /// nothing and kept doing nothing across restarts, because each new
    /// instance loaded the same dead state back in.
    fn restore(&mut self) {
        let path = match self.eq_file() {
            Some(p) => p,
            None => return,
        };
        let text = match std::fs::read_to_string(&path) {
            Ok(t) => t,
            Err(_) => return,
        };
        let n = text
            .lines()
            .filter(|ln| ln.trim().to_lowercase().starts_with("filter:"))
            .count();
        if n != self.bands.len() {
            if n > 0 {
                crate::logging::line(&format!(
                    "ignoring {INCLUDE_NAME}: {n} filters, this build has {} bands",
                    self.bands.len()
                ));
            }
            return;
        }
        let mut i = 0;
        for raw in text.lines() {
            let t: Vec<&str> = raw.split_whitespace().collect();
            if t.first() != Some(&"Filter:") || i >= self.bands.len() {
                continue;
            }
            if let Some(b) = parse_filter(&t) {
                self.bands[i] = b;
            }
            i += 1;
        }
    }
}

fn parse_filter(t: &[&str]) -> Option<Band> {
    let on = *t.get(1)? == "ON";
    let kind = (*t.get(2)?).to_string();
    let at = |k: &str| t.iter().position(|x| *x == k).and_then(|p| t.get(p + 1));
    let f0: f64 = at("Fc")?.parse().ok()?;
    let db: f64 = at("Gain")?.parse().ok()?;
    let q: f64 = at("Q").and_then(|s| s.parse().ok()).unwrap_or(1.0);
    Some(Band { kind, f0, db, q, on })
}

/// Read preserving line endings exactly.
fn read_raw(path: &std::path::Path) -> String {
    std::fs::read_to_string(path).unwrap_or_default()
}

fn squash(s: &str) -> String {
    s.replace(' ', "").to_lowercase()
}

fn config_path() -> Option<PathBuf> {
    let k = RegKey::predef(HKEY_LOCAL_MACHINE)
        .open_subkey(r"SOFTWARE\EqualizerAPO")
        .ok()?;
    let p: String = k.get_value("ConfigPath").ok()?;
    let path = PathBuf::from(p);
    if path.is_dir() {
        Some(path)
    } else {
        None
    }
}

/// Every endpoint Equalizer APO has actually been attached to, and whether it
/// is currently present.
fn attached_endpoints() -> Vec<(String, bool)> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let child = match hklm.open_subkey(CHILD_APOS) {
        Ok(k) => k,
        Err(_) => return Vec::new(),
    };
    let mut out = Vec::new();
    for guid in child.enum_keys().flatten() {
        let name: Option<String> = hklm
            .open_subkey(format!(r"{RENDER}\{guid}\Properties"))
            .ok()
            .and_then(|p| p.get_value(NAME_VALUE).ok());
        let state: Option<u32> = hklm
            .open_subkey(format!(r"{RENDER}\{guid}"))
            .ok()
            .and_then(|p| p.get_value("DeviceState").ok());
        if let Some(name) = name {
            out.push((name, state.map(|s| s & 1 == 1).unwrap_or(false))); // 1 = ACTIVE
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gain_is_clamped_to_the_knob_full_scale() {
        let mut eq = ParametricEq { config_path: None, bands: default_bands(), dirty: false, last_write: Instant::now() };
        eq.set_gain(0, 99.0);
        assert_eq!(eq.bands[0].db, MAXDB);
        eq.set_gain(0, -99.0);
        assert_eq!(eq.bands[0].db, -MAXDB);
        eq.set_q(0, 0.0);
        assert_eq!(eq.bands[0].q, QMIN);
        eq.set_freq(0, 999_999.0);
        assert_eq!(eq.bands[0].f0, FMAX);
        // An unknown filter type is refused rather than written out, since
        // Equalizer APO would drop the whole line.
        assert!(!eq.set_kind(0, "BOGUS"));
        assert!(eq.set_kind(0, "PK"));
    }

    #[test]
    fn flat_restores_the_shapes_the_device_calls_flat() {
        let mut eq = ParametricEq { config_path: None, bands: default_bands(), dirty: false, last_write: Instant::now() };
        eq.set_gain(1, 9.0);
        eq.set_on(1, false);
        eq.flat();
        assert_eq!(eq.bands[1].db, 0.0);
        // Bands stay ON: a 0 dB filter is already a no-op, and switching them
        // off is how the EQ came to be silently dead.
        assert!(eq.bands[1].on);
        assert_eq!(eq.bands[1].kind, "PK");
        assert_eq!(eq.bands[1].f0, 1000.0);
    }

    #[test]
    fn hz_never_goes_exponential() {
        // 2e+04 is the exact shape Equalizer APO silently drops, which turned
        // a band wound to the top of its range into a dead filter.
        assert_eq!(fmt_hz(20000.0), "20000");
        assert_eq!(fmt_hz(1000.0), "1000");
        assert_eq!(fmt_hz(120.0), "120");
        assert_eq!(fmt_hz(20.0), "20");
        for hz in [20.0, 120.0, 999.9, 1000.0, 4000.0, 20000.0] {
            assert!(!fmt_hz(hz).contains('e'), "{hz} went exponential");
        }
    }

    #[test]
    fn filter_lines_match_the_apo_syntax() {
        let b = Band { kind: "LSC".into(), f0: 120.0, db: -3.5, q: 0.7, on: true };
        assert_eq!(b.line(), "Filter: ON LSC Fc 120 Hz Gain -3.50 dB Q 0.7");
        assert_eq!(b.wire(), "LSC 120 -3.50 0.7 1");
        let off = Band { kind: "PK".into(), f0: 1000.0, db: 0.0, q: 0.9, on: false };
        assert_eq!(off.line(), "Filter: OFF PK Fc 1000 Hz Gain 0.00 dB Q 0.9");
    }

    #[test]
    fn a_filter_line_round_trips() {
        let b = Band { kind: "HSC".into(), f0: 4000.0, db: 6.25, q: 1.4, on: true };
        let line = b.line();
        let toks: Vec<&str> = line.split_whitespace().collect();
        let back = parse_filter(&toks).expect("should parse its own output");
        assert_eq!(back, b);
    }

    #[test]
    fn include_line_matching_ignores_spacing_and_case() {
        assert_eq!(squash("Include:  naub-eq.txt"), squash(INCLUDE_LINE));
        assert_eq!(squash("include: NAUB-EQ.TXT"), squash(INCLUDE_LINE));
        assert_ne!(squash("Include: other.txt"), squash(INCLUDE_LINE));
    }
}
