//! Minimal logging: prints to stdout in debug builds and on macOS (where a
//! release build still has a terminal when it is run from one), and always appends to a log file, so the windowless release daemon keeps a record.

use std::io::Write;
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::{SystemTime, UNIX_EPOCH};

static LOG_PATH: OnceLock<PathBuf> = OnceLock::new();

pub fn init(path: PathBuf) {
    let _ = LOG_PATH.set(path);
}

pub fn line(msg: &str) {
    let stamped = format!("[{}] {msg}", hhmmss());
    #[cfg(any(debug_assertions, not(windows)))]
    println!("{stamped}");
    if let Some(p) = LOG_PATH.get() {
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(p) {
            let _ = writeln!(f, "{stamped}");
        }
    }
}

fn hhmmss() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
        % 86_400;
    format!("{:02}:{:02}:{:02}", secs / 3600, (secs % 3600) / 60, secs % 60)
}
