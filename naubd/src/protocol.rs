//! naub wire protocol (proto=1). Line-based, `\n`-terminated, UTF-8.
//! See ../../PROTOCOL.md. Host->device encoders + device->host parser.

use base64::Engine;

/// An event the device emits in response to physical input.
#[derive(Debug, Clone, PartialEq)]
pub enum DeviceEvent {
    /// Banner / handshake line, e.g. "naub proto=1 channels=4 fw=v1.0".
    Banner(String),
    /// Knob moved: channel, absolute 0..100.
    Vol(u8, i32),
    /// Encoder clicked: channel, new mute state.
    Mute(u8, bool),
    /// Hold-and-turn: channel, direction (+1 / -1) per detent.
    Cycle(u8, i32),
    /// Aux button toggled: button index, latch state.
    Btn(u8, bool),
    /// Reply to ping.
    Pong,
}

/// Parse one device line into an event, or None for blank/unknown verbs
/// (unknown verbs are ignored for forward-compat, same as the firmware).
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
        "cycle" => {
            let ch = it.next()?.parse().ok()?;
            let d: i32 = it.next()?.parse().ok()?;
            Some(DeviceEvent::Cycle(ch, if d >= 0 { 1 } else { -1 }))
        }
        "btn" => Some(DeviceEvent::Btn(it.next()?.parse().ok()?, it.next()? == "1")),
        "pong" => Some(DeviceEvent::Pong),
        _ => None,
    }
}

// ---- host -> device encoders (each returns a single line, no trailing \n) ----

pub fn hello() -> String {
    "hello".into()
}

pub fn ping() -> String {
    "ping".into()
}

pub fn label(ch: u8, text: &str) -> String {
    format!("label {ch} {text}")
}

pub fn icon(ch: u8, w: u32, h: u32, packed: &[u8]) -> String {
    let b64 = base64::engine::general_purpose::STANDARD.encode(packed);
    format!("icon {ch} {w} {h} {b64}")
}

pub fn iconclear(ch: u8) -> String {
    format!("iconclear {ch}")
}

pub fn level(ch: u8, v: i32) -> String {
    format!("level {ch} {}", v.clamp(0, 100))
}

pub fn mute(ch: u8, muted: bool) -> String {
    format!("mute {ch} {}", if muted { 1 } else { 0 })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_events() {
        assert_eq!(parse("vol 0 63"), Some(DeviceEvent::Vol(0, 63)));
        assert_eq!(parse("mute 1 1"), Some(DeviceEvent::Mute(1, true)));
        assert_eq!(parse("cycle 2 -1"), Some(DeviceEvent::Cycle(2, -1)));
        assert_eq!(parse("cycle 2 5"), Some(DeviceEvent::Cycle(2, 1)));
        assert_eq!(parse("btn 0 1"), Some(DeviceEvent::Btn(0, true)));
        assert_eq!(parse("pong"), Some(DeviceEvent::Pong));
        assert_eq!(
            parse("naub proto=1 channels=4 fw=v1.0"),
            Some(DeviceEvent::Banner("naub proto=1 channels=4 fw=v1.0".into()))
        );
    }

    #[test]
    fn parse_rejects() {
        assert_eq!(parse(""), None);
        assert_eq!(parse("   "), None);
        assert_eq!(parse("bogus 1 2"), None);
        assert_eq!(parse("vol 0"), None); // missing arg
    }

    #[test]
    fn encoders() {
        assert_eq!(label(0, "Spotify"), "label 0 Spotify");
        assert_eq!(label(0, "Two Words"), "label 0 Two Words");
        assert_eq!(level(0, 150), "level 0 100"); // clamped
        assert_eq!(mute(3, true), "mute 3 1");
        assert_eq!(iconclear(2), "iconclear 2");
        assert_eq!(icon(0, 1, 1, &[0xFF]), "icon 0 1 1 /w==");
    }
}
