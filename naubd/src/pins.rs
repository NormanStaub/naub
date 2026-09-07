//! Column assignments, and where they live.
//!
//! A pin outlives everything: it is stored here on the host and pushed back on
//! every connect, so a column keeps its app across a device reload (which
//! happens on every deploy), across a daemon restart, and across the app itself
//! being closed and later relaunched.
//!
//! `%LOCALAPPDATA%\naub\pins.json` -- not in the repo and not next to the exe,
//! because this is user state and it has to survive a git checkout. The file is
//! byte-compatible with `firmware/v2/host/naub_bridge.py`, so a machine can
//! move between the Python bridge and this daemon without losing its columns.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};

pub const NCOL: usize = 3;

#[derive(Serialize, Deserialize, Default)]
struct PinFile {
    #[serde(default)]
    pinned: Vec<Option<String>>,
}

pub fn path() -> PathBuf {
    let base = dirs::data_local_dir()
        .or_else(dirs::home_dir)
        .unwrap_or_else(|| PathBuf::from("."));
    base.join("naub").join("pins.json")
}

/// Column assignments from the last session. Any failure means no pins, never
/// an error: this is a convenience file, and starting unpinned is a perfectly
/// good state.
pub fn load() -> Vec<Option<String>> {
    let mut out = vec![None; NCOL];
    let text = match std::fs::read_to_string(path()) {
        Ok(t) => t,
        Err(_) => return out,
    };
    let parsed: PinFile = match serde_json::from_str(&text) {
        Ok(p) => p,
        Err(_) => return out,
    };
    for (i, slot) in parsed.pinned.into_iter().take(NCOL).enumerate() {
        out[i] = slot.filter(|s| !s.is_empty());
    }
    out
}

pub fn save(pinned: &[Option<String>]) {
    let path = path();
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let body = PinFile { pinned: pinned.to_vec() };
    match serde_json::to_string(&body) {
        Ok(json) => {
            if let Err(e) = std::fs::write(&path, json) {
                crate::logging::line(&format!("could not save column pins ({e})"));
            }
        }
        Err(e) => crate::logging::line(&format!("could not encode column pins ({e})")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_python_bridges_file() {
        let f: PinFile = serde_json::from_str(r#"{"pinned": ["spotify", null, "gtaiv"]}"#).unwrap();
        assert_eq!(f.pinned.len(), 3);
        assert_eq!(f.pinned[0].as_deref(), Some("spotify"));
        assert_eq!(f.pinned[1], None);
    }

    #[test]
    fn writes_what_that_bridge_would_read() {
        let json = serde_json::to_string(&PinFile {
            pinned: vec![Some("spotify".into()), None, None],
        })
        .unwrap();
        assert_eq!(json, r#"{"pinned":["spotify",null,null]}"#);
    }

    #[test]
    fn a_missing_or_broken_file_is_simply_no_pins() {
        assert!(serde_json::from_str::<PinFile>("not json").is_err());
        let empty: PinFile = serde_json::from_str("{}").unwrap();
        assert!(empty.pinned.is_empty());
    }
}
