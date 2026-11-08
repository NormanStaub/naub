//! N-band parametric EQ, driven into Equalizer APO on Windows.
//!
//! macOS has no such engine and no insertion point for one to use
//! (`firmware/v2/MACOS_PORT.md` §3.4). There, the band state and the file it is
//! saved in are the same, but the filters run inside `audio_mac`'s re-render
//! path (`dsp.rs`): see the `cfg(target_os = "macos")` items below.
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
//!   matters, and [`attach`] fixes the answer without the Configurator --
//!   because Windows re-creates endpoints under new GUIDs (a driver update
//!   did exactly that), and every re-creation silently drops the APO.
//! * **Writes are debounced.** A write costs us a fraction of a millisecond,
//!   but it makes Equalizer APO re-parse the whole config -- the user's filters
//!   included -- and rebuild its graph. A knob at one detent per 10 ms would do
//!   that 100 times a second, so [`ParametricEq::set_band`] only marks dirty
//!   and [`ParametricEq::tick`] writes at most every `WRITE_MS`.

use std::path::PathBuf;
use std::time::{Duration, Instant};

#[cfg(windows)]
use winreg::enums::{HKEY_LOCAL_MACHINE, KEY_READ, KEY_SET_VALUE};
#[cfg(windows)]
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

#[cfg(windows)]
const RENDER: &str = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render";
#[cfg(windows)]
const CHILD_APOS: &str = r"SOFTWARE\EqualizerAPO\Child APOs";
#[cfg(windows)]
const NAME_VALUE: &str = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6";
/// Equalizer APO's own COM classes, as they appear in an endpoint's
/// FxProperties: pre-mix (the one that does the EQ) and post-mix.
#[cfg(windows)]
const APO_CLSID: &str = "{EACD2258-FCAC-4FF4-B36D-419E924A6D79}";
#[cfg(windows)]
const POST_CLSID: &str = "{EC1CC9CE-FAED-4822-828A-82A81A6F018F}";
/// PKEY_FX_* (audioenginebaseapo.h): `,5` stream, `,6` mode, `,7` endpoint
/// effect; `,1`/`,2` are the older pre-mix/post-mix slots.
#[cfg(windows)]
const FX: &str = "{d04e05a6-594b-4fb6-a80d-01af5eed7d1d}";
/// PKEY_*FX_ProcessingModes_Supported_For_Streaming, same numbering.
#[cfg(windows)]
const MODES: &str = "{d3993a3f-99c2-4402-b5ec-a92a0367664b}";
#[cfg(windows)]
const MODE_DEFAULT: &str = "{C18E2F7E-933D-4965-B7D1-1EEF228D2AF3}";
/// How `Child APOs` writes down "this slot was empty".
#[cfg(windows)]
const ABSENT: &str = "!VALUE";

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
        #[cfg(target_os = "macos")]
        eq.publish();
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
    #[cfg(windows)]
    pub fn status(&self) -> (bool, String) {
        if !self.available() {
            return (false, "Equalizer APO not installed".into());
        }
        let (guid, cur) = match crate::audio::default_endpoint() {
            Some(d) => d,
            None => return (false, "no default output device".into()),
        };
        if hosts_apo(&guid) {
            return (true, format!("active on {cur}"));
        }
        let others = attached_endpoints();
        let list = if others.is_empty() {
            "no device".to_string()
        } else {
            others.join(", ")
        };
        (
            false,
            format!(
                "not attached to {cur} {guid} (only to {list}) -- tray menu: Attach EQ"
            ),
        )
    }

    /// `(ok, why)`, on a Mac: a filter is only heard once audio can be tapped
    /// at all, and a denied grant does not fail, it delivers silence.
    #[cfg(target_os = "macos")]
    pub fn status(&self) -> (bool, String) {
        let cur = crate::audio::default_endpoint_name().unwrap_or_else(|| "no output".into());
        if crate::audio::capture_ok() {
            (true, format!("active on {cur} (naub's own filters)"))
        } else {
            (
                false,
                "no audio has come through a tap yet: play something, and check \
                 Privacy & Security > Screen & System Audio Recording"
                    .into(),
            )
        }
    }

    /// Hand the current bands to the running IOProcs.
    #[cfg(target_os = "macos")]
    fn publish(&self) {
        let mut bands = [(0u32, false, 1000.0f32, 0.0f32, 1.0f32); crate::dsp::NBAND];
        for (o, b) in bands.iter_mut().zip(&self.bands) {
            *o = (crate::dsp::kind_code(&b.kind), b.on, b.f0 as f32, b.db as f32, b.q as f32);
        }
        crate::dsp::EQ.publish(&bands);
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
        #[cfg(target_os = "macos")]
        self.publish();
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

    /// Nobody else's config to join on a Mac: the file is just where the bands
    /// are kept between runs.
    #[cfg(target_os = "macos")]
    fn ensure_include(&self) -> std::io::Result<()> {
        Ok(())
    }

    /// Add our one line to the user's config.txt, once.
    ///
    /// Appended verbatim, with no decorative blank line, so `remove` can take
    /// back exactly what was added. An earlier version added a leading blank
    /// line and removed only the two content lines, so every install/uninstall
    /// cycle grew the user's file by one newline.
    #[cfg(windows)]
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

/// Where the bands are kept on a Mac, next to the column pins.
#[cfg(target_os = "macos")]
fn config_path() -> Option<PathBuf> {
    let dir = crate::pins::path().parent()?.to_path_buf();
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir)
}

/// The Mac's analogue of "is this endpoint hosting the APO": the engine is
/// always there, and the only thing that can be missing is the grant.
#[cfg(target_os = "macos")]
pub fn hosts_apo(_uid: &str) -> bool {
    crate::audio::capture_ok()
}

#[cfg(windows)]
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

/// Is Equalizer APO in this endpoint's effect chain right now?
///
/// Read from the endpoint's own `FxProperties`, which is what the audio engine
/// loads. `Child APOs` is NOT the answer: it is Equalizer APO's bookkeeping,
/// and it keeps entries for endpoints Windows has since deleted -- three of the
/// four on the machine where this was found.
#[cfg(windows)]
pub fn hosts_apo(guid: &str) -> bool {
    RegKey::predef(HKEY_LOCAL_MACHINE)
        .open_subkey(format!(r"{RENDER}\{guid}\FxProperties"))
        .map(|fx| {
            fx.enum_values()
                .flatten()
                .any(|(_, v)| v.to_string().trim().eq_ignore_ascii_case(APO_CLSID))
        })
        .unwrap_or(false)
}

#[cfg(windows)]
pub fn installed() -> bool {
    config_path().is_some()
}

/// Put Equalizer APO into one endpoint's effect chain: what its DeviceSelector
/// does when a box is ticked, so neither tool is surprised by the other's work
/// and Equalizer APO's uninstaller still restores the original effects.
///
/// The layout is the one DeviceSelector left on the endpoint it attached here
/// (read back from the registry, not taken from its source):
///
/// * `Child APOs\{guid}` records the effect slots it is about to change
///   (`!VALUE` for an empty one), and names the original stream effect as
///   `PreMixChild` -- Equalizer APO loads that itself and runs it, so the
///   driver's own processing is kept, not replaced.
/// * the endpoint's stream-effect slot (`,5`) gets the pre-mix class and the
///   endpoint-effect slot (`,7`) the post-mix class; the mode effect (`,6`) is
///   left alone. If the post-mix slot declares no processing modes, it is
///   given the default one, as DeviceSelector's install had.
///
/// The bookkeeping is written first, so a failure part-way leaves the endpoint
/// untouched rather than attached with nothing to restore.
///
/// Needs Administrator; takes effect when the audio service next starts.
#[cfg(windows)]
pub fn attach(guid: &str) -> Result<&'static str, String> {
    if !installed() {
        return Err("Equalizer APO is not installed".into());
    }
    if hosts_apo(guid) {
        return Ok("already attached");
    }
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    // Administrators may set values on an endpoint's FxProperties but not
    // create the key: an endpoint without one is out of reach from here.
    let fx = hklm
        .open_subkey_with_flags(format!(r"{RENDER}\{guid}\FxProperties"), KEY_READ | KEY_SET_VALUE)
        .map_err(|e| format!("cannot write {guid}'s effect list: {e}"))?;
    let slot = |n: u32| fx.get_value::<String, _>(format!("{FX},{n}")).ok();
    // ,1/,2 are the pre-Windows-8.1 LFX/GFX slots. On an endpoint that still
    // uses them, which slot Equalizer APO belongs in is a judgement call
    // DeviceSelector makes with options of its own; leave it to it.
    if slot(5).is_none() && (slot(1).is_some() || slot(2).is_some()) {
        return Err(format!("{guid} uses the legacy LFX/GFX effect layout -- use DeviceSelector"));
    }
    let (child, _) = hklm
        .create_subkey(format!(r"{CHILD_APOS}\{guid}"))
        .map_err(|e| format!("cannot record {guid}'s original effects: {e}"))?;
    let record = || -> std::io::Result<()> {
        for n in [1, 2, 5, 6, 7] {
            child.set_value(format!("{FX},{n}"), &slot(n).unwrap_or_else(|| ABSENT.into()))?;
        }
        child.set_value("PreMixChild", &slot(5).unwrap_or_default())?;
        child.set_value("PostMixChild", &slot(7).unwrap_or_default())?;
        child.set_value("AllowSilentBufferModification", &"false")?;
        child.set_value("Version", &"2")
    };
    record().map_err(|e| format!("cannot record {guid}'s original effects: {e}"))?;
    let swap = || -> std::io::Result<()> {
        if fx.get_raw_value(format!("{MODES},7")).is_err() {
            fx.set_value(format!("{MODES},7"), &vec![MODE_DEFAULT])?;
        }
        fx.set_value(format!("{FX},5"), &APO_CLSID)?;
        fx.set_value(format!("{FX},7"), &POST_CLSID)
    };
    swap().map_err(|e| format!("cannot write {guid}'s effect list: {e}"))?;
    Ok("attached")
}

/// Names of the endpoints that really do host Equalizer APO, present or not,
/// for the message that says where it went instead.
#[cfg(windows)]
fn attached_endpoints() -> Vec<String> {
    let hklm = RegKey::predef(HKEY_LOCAL_MACHINE);
    let child = match hklm.open_subkey(CHILD_APOS) {
        Ok(k) => k,
        Err(_) => return Vec::new(),
    };
    let mut out: Vec<String> = child
        .enum_keys()
        .flatten()
        .filter(|guid| hosts_apo(guid))
        .filter_map(|guid| {
            hklm.open_subkey(format!(r"{RENDER}\{guid}\Properties"))
                .ok()
                .and_then(|p| p.get_value(NAME_VALUE).ok())
        })
        .collect();
    out.sort();
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
