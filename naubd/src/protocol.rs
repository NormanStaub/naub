//! naub wire protocol (proto=2). Line-based, `\n`-terminated, UTF-8.
//! See ../../PROTOCOL.md §§3, 4, 10, 11. Host->device encoders + device->host parser.
//!
//! V1's `cycle` / `icon` / `iconclear` are gone: the V2 panel picks its own
//! source from the switcher (`pick`), and an app's art arrives as `aicon`, in
//! the form the shelf blits. Unknown verbs still parse to `None` --
//! §1's forward-compat rule is what lets a device and host of different
//! vintages share a link.

/// An event the device emits. Everything here is either physical user input or
/// a statement about which screen is up; the device never echoes host state.
#[derive(Debug, Clone, PartialEq)]
pub enum DeviceEvent {
    /// Banner / handshake, e.g. "naub proto=2 channels=4 fw=v2-mixer".
    Banner(String),
    /// Knob moved: channel (3 == system endpoint), absolute 0..100.
    Vol(u8, i32),
    /// Encoder clicked: channel (3 == system endpoint), new mute state.
    Mute(u8, bool),
    /// The switcher assigned an app to a column: channel, app key (lowercased).
    Pick(u8, String),
    /// The mode bar was flipped, and once on connect. Indexes the device's list.
    Mode(i32),
    /// The switcher opened (true) or closed (false), and once on connect.
    Wheel(bool),
    /// Aux button: index, pressed. V2 reports momentary, not V1's latch.
    Btn(u8, bool),
    /// One EQ parameter moved: band index, param name, raw value token.
    EqParam(usize, String, String),
    /// Reset every band to 0 dB and the default shapes.
    EqFlat,
    /// Reply to ping.
    Pong,
}

/// Parse one device line, or None for blank/unknown verbs.
pub fn parse(line: &str) -> Option<DeviceEvent> {
    let line = line.trim();
    if line.is_empty() {
        return None;
    }
    let mut it = line.split_whitespace();
    let verb = it.next()?;
    match verb {
        "naub" => Some(DeviceEvent::Banner(line.to_string())),
        "vol" => Some(DeviceEvent::Vol(it.next()?.parse().ok()?, it.next()?.parse().ok()?)),
        "mute" => Some(DeviceEvent::Mute(it.next()?.parse().ok()?, it.next()? == "1")),
        // The app key is the REST of the line, lowercased: process names can
        // contain spaces, so it must not be taken as a single whitespace token.
        "pick" => {
            let ch = it.next()?.parse().ok()?;
            let rest: Vec<&str> = it.collect();
            if rest.is_empty() {
                return None;
            }
            Some(DeviceEvent::Pick(ch, rest.join(" ").to_ascii_lowercase()))
        }
        "mode" => Some(DeviceEvent::Mode(it.next()?.parse().ok()?)),
        "wheel" => Some(DeviceEvent::Wheel(it.next()? == "1")),
        "btn" => Some(DeviceEvent::Btn(it.next()?.parse().ok()?, it.next()? == "1")),
        "eqp" => {
            let i: usize = it.next()?.parse().ok()?;
            let param = it.next()?.to_string();
            let value = it.next()?.to_string();
            Some(DeviceEvent::EqParam(i, param, value))
        }
        "eqflat" => Some(DeviceEvent::EqFlat),
        "pong" => Some(DeviceEvent::Pong),
        _ => None,
    }
}

// ---- host -> device encoders (each returns a single line, no trailing \n) ----

pub fn hello() -> String {
    "hello".into()
}

pub fn label(ch: u8, text: &str) -> String {
    format!("label {ch} {text}")
}

pub fn level(ch: u8, v: i32) -> String {
    format!("level {ch} {}", v.clamp(0, 100))
}

pub fn mute(ch: u8, muted: bool) -> String {
    format!("mute {ch} {}", if muted { 1 } else { 0 })
}

/// Column meters: one 0..100 per column, pushed continuously.
pub fn meter(values: &[i32]) -> String {
    join_ints("meter", values)
}

/// One level per entry of the last `apps` list.
pub fn ameter(values: &[i32]) -> String {
    join_ints("ameter", values)
}

/// The system output's real spectrum, for the EQ screen (21 bands).
pub fn smeter(values: &[i32]) -> String {
    join_ints("smeter", values)
}

fn join_ints(verb: &str, values: &[i32]) -> String {
    let mut s = String::from(verb);
    for v in values {
        s.push(' ');
        s.push_str(&(*v).clamp(0, 100).to_string());
    }
    s
}

/// The switcher's candidate list. COMMA separated -- process names can contain
/// spaces, so whitespace cannot be the delimiter. An empty list is legal and
/// means nothing is playing.
pub fn apps(keys: &[String]) -> String {
    let up: Vec<String> = keys.iter().map(|k| k.to_uppercase()).collect();
    format!("apps {}", up.join(","))
}

/// One app's icon, as the shelf blits it. The name goes LAST because it can
/// contain spaces (§1), and is cased as `apps` cases it so the two match up.
pub fn aicon(key: &str, blob: &[u8]) -> String {
    format!("aicon {} {}", base64(blob), key.to_uppercase())
}

fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut s = String::with_capacity((data.len() + 2) / 3 * 4);
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16
            | (*c.get(1).unwrap_or(&0) as u32) << 8
            | *c.get(2).unwrap_or(&0) as u32;
        s.push(T[(n >> 18) as usize & 63] as char);
        s.push(T[(n >> 12) as usize & 63] as char);
        s.push(if c.len() > 1 { T[(n >> 6) as usize & 63] as char } else { '=' });
        s.push(if c.len() > 2 { T[n as usize & 63] as char } else { '=' });
    }
    s
}

/// What the host's EQ engine can do, and whether it would actually be heard.
/// `ok == false` is normal, not an error: Equalizer APO attaches per endpoint.
pub fn eqcaps(nband: usize, fmin: f64, fmax: f64, maxdb: f64, rate: u32, ok: bool) -> String {
    format!(
        "eqcaps {nband} {} {} {} {rate} {}",
        fmt_g(fmin),
        fmt_g(fmax),
        fmt_g(maxdb),
        if ok { 1 } else { 0 }
    )
}

pub fn eqband(i: usize, wire: &str) -> String {
    format!("eqband {i} {wire}")
}

/// Shortest round-tripping form, never scientific notation. The device parses
/// these as floats, and `2e+04` for 20000 is exactly the shape that made
/// Equalizer APO silently drop a filter (see eqapo::fmt_hz).
fn fmt_g(v: f64) -> String {
    if v == v.trunc() && v.abs() < 1e15 {
        format!("{}", v as i64)
    } else {
        format!("{v}")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_events() {
        assert_eq!(parse("vol 0 63"), Some(DeviceEvent::Vol(0, 63)));
        assert_eq!(parse("vol 3 10"), Some(DeviceEvent::Vol(3, 10)));
        assert_eq!(parse("mute 1 1"), Some(DeviceEvent::Mute(1, true)));
        assert_eq!(parse("mode 1"), Some(DeviceEvent::Mode(1)));
        assert_eq!(parse("wheel 1"), Some(DeviceEvent::Wheel(true)));
        assert_eq!(parse("wheel 0"), Some(DeviceEvent::Wheel(false)));
        assert_eq!(parse("btn 2 1"), Some(DeviceEvent::Btn(2, true)));
        assert_eq!(parse("eqflat"), Some(DeviceEvent::EqFlat));
        assert_eq!(parse("pong"), Some(DeviceEvent::Pong));
        assert_eq!(
            parse("eqp 0 gain -3.5"),
            Some(DeviceEvent::EqParam(0, "gain".into(), "-3.5".into()))
        );
        assert_eq!(
            parse("naub proto=2 channels=4 fw=v2-mixer"),
            Some(DeviceEvent::Banner("naub proto=2 channels=4 fw=v2-mixer".into()))
        );
    }

    #[test]
    fn pick_keeps_spaces_and_lowercases() {
        assert_eq!(parse("pick 1 CHROME"), Some(DeviceEvent::Pick(1, "chrome".into())));
        // A process name with a space must survive as one key.
        assert_eq!(
            parse("pick 0 RED ALERT"),
            Some(DeviceEvent::Pick(0, "red alert".into()))
        );
    }

    #[test]
    fn parse_rejects() {
        assert_eq!(parse(""), None);
        assert_eq!(parse("   "), None);
        assert_eq!(parse("bogus 1 2"), None);
        assert_eq!(parse("vol 0"), None); // missing arg
        assert_eq!(parse("pick 0"), None); // no app
        // V1 verbs are gone; they must read as unknown, not panic.
        assert_eq!(parse("cycle 2 -1"), None);
    }

    #[test]
    fn encoders() {
        assert_eq!(label(0, "SPOTIFY"), "label 0 SPOTIFY");
        assert_eq!(level(0, 150), "level 0 100"); // clamped
        assert_eq!(mute(3, true), "mute 3 1");
        assert_eq!(meter(&[0, 50, 100]), "meter 0 50 100");
        assert_eq!(ameter(&[7]), "ameter 7");
        assert_eq!(smeter(&[]), "smeter");
        assert_eq!(apps(&["spotify".into(), "chrome".into()]), "apps SPOTIFY,CHROME");
        assert_eq!(apps(&[]), "apps ");
        assert_eq!(aicon("nzxt cam", b"foob"), "aicon Zm9vYg== NZXT CAM");
        assert_eq!(aicon("a", b"foo"), "aicon Zm9v A");
    }

    #[test]
    fn eqcaps_never_uses_scientific_notation() {
        let s = eqcaps(3, 20.0, 20000.0, 12.0, 48000, true);
        assert_eq!(s, "eqcaps 3 20 20000 12 48000 1");
        // The verb itself has an 'e'; it is the VALUES that must never carry
        // one, since the device parses them as floats and `2e+04` is the shape
        // that turns a band wound to the top of its range into a dead filter.
        for field in s.split_whitespace().skip(1) {
            assert!(!field.contains('e'), "{field} went exponential");
        }
        assert!(!eqcaps(3, 20.0, 20000.0, 12.0, 48000, false).ends_with('1'));
    }
}
