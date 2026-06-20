// naubd - native Windows daemon for the naub USB per-app mixer.
// Console build first (parity with the Python host); tray + autostart layer on top.
#![allow(dead_code)] // some debug helpers are wired in incrementally.

mod app;
mod audio;
mod config;
mod icons;
mod link;
mod protocol;
mod state;

use std::path::PathBuf;

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
        Some("--config") => match args.get(1) {
            Some(path) => run(PathBuf::from(path)),
            None => println!("usage: naubd --config <config.json>"),
        },
        _ => run(default_config_path()),
    }
}

fn run(config_path: PathBuf) {
    let cfg = match config::load(&config_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("config load failed ({}): {e}", config_path.display());
            std::process::exit(1);
        }
    };
    println!("[naubd] config: {}", config_path.display());
    let mut app = match app::App::new(cfg) {
        Ok(a) => a,
        Err(e) => {
            eprintln!("audio init failed: {e}");
            std::process::exit(1);
        }
    };
    app.run_forever();
}

fn default_config_path() -> PathBuf {
    let mut candidates = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            candidates.push(dir.join("config.json"));
            candidates.push(dir.join("config.example.json"));
        }
    }
    candidates.push(PathBuf::from("config.json"));
    candidates.push(PathBuf::from("config.example.json"));
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
