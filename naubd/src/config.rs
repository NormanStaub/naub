//! Host config: channel -> process mapping (deej-style), loaded from JSON.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;

pub struct ChannelCfg {
    pub label: String,
    pub matches: Vec<String>, // lowercased process names, or "__system__"
}

pub struct Config {
    pub port: String,
    pub refresh_seconds: f64,
    pub channels: BTreeMap<u8, ChannelCfg>,
    pub path: PathBuf,
}

#[derive(Deserialize, Default)]
struct RawChannel {
    #[serde(default)]
    label: String,
    #[serde(rename = "match", default)]
    matches: Vec<String>,
}

#[derive(Deserialize)]
struct RawConfig {
    #[serde(default = "default_port")]
    port: String,
    #[serde(default = "default_refresh")]
    refresh_seconds: f64,
    #[serde(default)]
    channels: BTreeMap<String, RawChannel>,
}

fn default_port() -> String {
    "auto".into()
}
fn default_refresh() -> f64 {
    2.0
}

pub fn load(path: &Path) -> std::io::Result<Config> {
    let text = std::fs::read_to_string(path)?;
    let raw: RawConfig = serde_json::from_str(&text)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let mut channels = BTreeMap::new();
    for (k, v) in raw.channels {
        if let Ok(ch) = k.parse::<u8>() {
            channels.insert(
                ch,
                ChannelCfg {
                    label: v.label,
                    matches: v.matches.iter().map(|m| m.to_ascii_lowercase()).collect(),
                },
            );
        }
    }
    Ok(Config {
        port: raw.port,
        refresh_seconds: raw.refresh_seconds,
        channels,
        path: path.to_path_buf(),
    })
}
