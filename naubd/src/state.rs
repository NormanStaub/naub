//! Sticky channel selections persisted beside the config (naub_state.json).
//! Keys are channel indices as strings ("0".."3"), matching the Python host.

use std::collections::BTreeMap;
use std::path::Path;

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Default, Clone)]
pub struct Saved {
    pub key: Option<String>,
    #[serde(default)]
    pub label: String,
    pub exe_path: Option<String>,
    pub level: Option<i32>,
    pub mute: Option<bool>,
}

pub type StateMap = BTreeMap<String, Saved>;

pub fn load(path: &Path) -> StateMap {
    match std::fs::read_to_string(path) {
        Ok(text) => serde_json::from_str(&text).unwrap_or_default(),
        Err(_) => StateMap::new(),
    }
}

/// Serialize to the canonical pretty JSON (so the daemon can dedupe writes by
/// comparing strings). Returns the JSON it wrote.
pub fn to_json(map: &StateMap) -> String {
    serde_json::to_string_pretty(map).unwrap_or_else(|_| "{}".into())
}

pub fn save(path: &Path, json: &str) -> std::io::Result<()> {
    std::fs::write(path, json)
}
