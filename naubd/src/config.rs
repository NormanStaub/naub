//! Host config, loaded from JSON. Small, because V2 barely needs one.
//!
//! V1's `channels` map is gone. That was deej-style config: the user wrote down
//! which process each knob drove. The V2 panel picks its own source from the
//! switcher and the host remembers it in `pins.json`, so the mapping is made by
//! hand at the device and there is nothing left to configure but the port and
//! the meter rate.
//!
//! A missing config is therefore not an error -- [`load_or_default`] just uses
//! the defaults, which are what almost every install wants.

use std::path::{Path, PathBuf};

use serde::Deserialize;

pub struct Config {
    /// Serial port name, or "auto" to find the board by USB VID/PID.
    pub port: String,
    /// Meter frames per second, an upper bound rather than a promise.
    pub rate: f64,
    /// Where the config was found; the log is written beside it.
    pub path: PathBuf,
}

#[derive(Deserialize)]
struct RawConfig {
    #[serde(default = "default_port")]
    port: String,
    #[serde(default = "default_rate")]
    rate: f64,
}

fn default_port() -> String {
    "auto".into()
}

fn default_rate() -> f64 {
    50.0
}

impl Config {
    fn fallback(path: &Path) -> Self {
        Self {
            port: default_port(),
            rate: default_rate(),
            path: path.to_path_buf(),
        }
    }
}

/// Read the config if there is one, otherwise run on defaults. Only a config
/// that exists *and* is malformed is worth complaining about: that is a typo
/// the user wants to know about, where an absent file is just an install that
/// never needed one.
pub fn load_or_default(path: &Path) -> Config {
    let text = match std::fs::read_to_string(path) {
        Ok(t) => t,
        Err(_) => return Config::fallback(path),
    };
    match serde_json::from_str::<RawConfig>(&text) {
        Ok(raw) => Config {
            port: raw.port,
            rate: raw.rate,
            path: path.to_path_buf(),
        },
        Err(e) => {
            crate::logging::line(&format!(
                "config {} is malformed ({e}); using defaults",
                path.display()
            ));
            Config::fallback(path)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_fill_in_what_is_missing() {
        let raw: RawConfig = serde_json::from_str("{}").unwrap();
        assert_eq!(raw.port, "auto");
        assert_eq!(raw.rate, 50.0);
    }

    #[test]
    fn a_v1_config_still_loads() {
        // Leftover `channels` blocks are ignored rather than rejected, so an
        // upgrade does not have to start by deleting the old file.
        let raw: RawConfig = serde_json::from_str(
            r#"{"port": "COM10", "refresh_seconds": 2.0,
                "channels": {"0": {"label": "Spotify", "match": ["spotify.exe"]}}}"#,
        )
        .unwrap();
        assert_eq!(raw.port, "COM10");
        assert_eq!(raw.rate, 50.0);
    }
}
