// naubd - native Windows daemon for the naub USB per-app mixer.
// Release builds are windowless (tray only); debug builds keep a console for dev.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
#![allow(dead_code)] // some debug helpers are wired in incrementally.

mod app;
mod audio;
mod config;
mod icons;
mod link;
mod logging;
mod protocol;
mod state;
mod tray;

use std::path::{Path, PathBuf};

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("--list-ports") => {
            for (dev, desc) in link::list_ports() {
                println!("{dev:<10} {desc}");
            }
        }
        Some("--probe") => match link::autodetect() {
            Some(port) => println!("naub data port: {port}"),
            None => {
                println!("naub data port: not found");
                std::process::exit(1);
            }
        },
        Some("--audio") => audio_dump(),
        Some("--icon") => match args.get(1) {
            Some(path) => icon_dump(path),
            None => println!("usage: naubd --icon <exe_path>"),
        },
        Some("--install") => {
            tray::set_autostart(true);
            println!("naubd: start-with-Windows enabled");
        }
        Some("--uninstall") => {
            tray::set_autostart(false);
            println!("naubd: start-with-Windows disabled");
        }
        // Foreground (no tray) -- handy for debugging with live stdout.
        Some("--console") => run_console(default_config_path()),
        Some("--config") => match args.get(1) {
            Some(path) => run_console(PathBuf::from(path)),
            None => println!("usage: naubd --config <config.json>"),
        },
        // Default: the real app -- tray icon + background daemon thread.
        _ => run_tray(default_config_path()),
    }
}

fn run_tray(config_path: PathBuf) {
    let cfg = load_cfg_or_exit(&config_path);
    init_logging(&cfg);
    logging::line(&format!("starting (tray); config: {}", config_path.display()));
    tray::run(cfg);
}

fn run_console(config_path: PathBuf) {
    let cfg = load_cfg_or_exit(&config_path);
    init_logging(&cfg);
    logging::line(&format!("starting (console); config: {}", config_path.display()));
    match app::App::new(cfg) {
        Ok(mut app) => app.run_forever(),
        Err(e) => {
            eprintln!("audio init failed: {e}");
            std::process::exit(1);
        }
    }
}

fn load_cfg_or_exit(path: &Path) -> config::Config {
    match config::load(path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config load failed ({}): {e}", path.display());
            std::process::exit(1);
        }
    }
}

fn init_logging(cfg: &config::Config) {
    let dir = cfg.path.parent().unwrap_or(Path::new("."));
    logging::init(dir.join("naubd.log"));
}

fn default_config_path() -> PathBuf {
    let mut candidates = Vec::new();
    // 1) next to the executable (portable install)
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("config.json"));
        }
    }
    // 2) the canonical per-user location (works under autostart, cwd = system32)
    if let Some(cfgdir) = dirs::config_dir() {
        candidates.push(cfgdir.join("naub").join("config.json"));
    }
    // 3) current directory, then bundled example (first run)
    candidates.push(PathBuf::from("config.json"));
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("config.example.json"));
        }
    }
    candidates
        .into_iter()
        .find(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from("config.json"))
}

fn audio_dump() {
    let mut a = audio::Audio::new().expect("audio init failed");
    a.snapshot();
    let pool = a.active_app_keys();
    println!("active pool: {pool:?}");
    for k in &pool {
        println!("  {k}: levels={:?} exe={:?}", a.get_app(k), a.exe_path(k));
    }
    println!("system: {:?}", a.get_system());
}

fn icon_dump(path: &str) {
    match icons::icon_for(path) {
        Some((w, h, data)) => {
            let stride = ((w + 7) / 8) as usize;
            for y in 0..h {
                let mut line = String::new();
                for x in 0..w {
                    let bit = (data[y as usize * stride + (x / 8) as usize] >> (7 - (x % 8))) & 1;
                    line.push(if bit == 1 { '#' } else { ' ' });
                }
                println!("{line}");
            }
            println!("{} bytes", data.len());
        }
        None => println!("(no usable icon)"),
    }
}
