// naubd - native Windows daemon for the naub USB mixer (V2, proto=2).
// Release builds are windowless (tray only); debug builds keep a console for dev.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio;
mod bridge;
mod config;
mod eqapo;
mod link;
mod logging;
mod pins;
mod protocol;
mod sysmeter;
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
        Some("--sessions") => sessions_dump(),
        Some("--eq") => eq_dump(),
        Some("--eq-remove") => {
            eqapo::ParametricEq::new().remove();
            println!("naub's Include line and naub-eq.txt removed from the Equalizer APO config");
        }
        Some("--spectrum") => spectrum_dump(),
        Some("--install") => {
            tray::set_autostart(true);
            println!("naubd: start-with-Windows enabled");
        }
        Some("--uninstall") => {
            tray::set_autostart(false);
            println!("naubd: start-with-Windows disabled");
        }
        // Foreground (no tray) -- live logs, and the per-knob/per-app chatter
        // the daemon leaves out of its file log.
        Some("--console") => run_console(default_config_path()),
        Some("--config") => match args.get(1) {
            Some(path) => run_console(PathBuf::from(path)),
            None => println!("usage: naubd --config <config.json>"),
        },
        Some(other) if other.starts_with("--") => {
            println!("naubd: unknown option {other}");
            usage();
            std::process::exit(2);
        }
        // Default: the real app -- tray icon + background daemon thread.
        _ => run_tray(default_config_path()),
    }
}

fn usage() {
    println!(
        "\nnaubd [--console] [--config PATH] [--list-ports] [--probe]\n\
         \x20     [--sessions] [--eq] [--eq-remove] [--spectrum]\n\
         \x20     [--install] [--uninstall]\n"
    );
}

fn run_tray(config_path: PathBuf) {
    let cfg = config::load_or_default(&config_path);
    init_logging(&cfg);
    logging::line(&format!("starting (tray); config: {}", config_path.display()));
    tray::run(cfg, false);
}

fn run_console(config_path: PathBuf) {
    let cfg = config::load_or_default(&config_path);
    init_logging(&cfg);
    logging::line(&format!("starting (console); config: {}", config_path.display()));
    match bridge::Bridge::new(cfg.port.clone(), cfg.rate, true) {
        Ok(mut b) => b.run_forever(),
        Err(e) => {
            eprintln!("audio init failed: {e}");
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
    // 3) current directory
    candidates.push(PathBuf::from("config.json"));
    candidates
        .into_iter()
        .find(|p| p.exists())
        .unwrap_or_else(|| PathBuf::from("config.json"))
}

/// Every audio session Windows has, grouped the way a column groups them.
///
/// Here because "that app is in the list but has no level and the knob does
/// nothing" is a question about WASAPI, not about the panel, and the answer is
/// usually that the program holds more than one session and only one of them is
/// the one you can hear.
fn sessions_dump() {
    let mut a = match audio::Audio::new() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("audio init failed: {e}");
            std::process::exit(1);
        }
    };
    a.refresh();
    let table = a.session_table();
    if table.is_empty() {
        println!("no audio sessions ({})", a.failure().unwrap_or("nothing is playing"));
        return;
    }
    println!("{:<16} {:<4} {}", "program", "n", "sessions (pid, state, peak, fader)");
    for (key, rows) in &table {
        let bits: Vec<String> = rows
            .iter()
            .map(|(pid, active, peak, gain)| {
                format!(
                    "{pid} {} peak={peak:.3} vol={gain:.2}",
                    if *active { "ACTIVE" } else { "idle  " }
                )
            })
            .collect();
        println!("{key:<16} {:<4} {}", rows.len(), bits.join(" | "));
    }
    println!();
    let active = a.active();
    println!(
        "offered to the switcher: {}",
        if active.is_empty() { "(none)".into() } else { active.join(", ") }
    );
    println!(
        "columns would be       : {}",
        a.top(pins::NCOL)
            .iter()
            .map(|k| if k.is_empty() { "-" } else { k.as_str() })
            .collect::<Vec<_>>()
            .join(" | ")
    );
}

/// Is there an EQ engine, and would anything written to it actually be heard?
fn eq_dump() {
    let eq = eqapo::ParametricEq::new();
    let (ok, why) = eq.status();
    println!("engine: {} -- {why}", if ok { "ready" } else { "INERT" });
    println!("pins:   {}", pins::path().display());
    println!();
    for (i, b) in eq.bands.iter().enumerate() {
        println!("  band {i}: {}", b.line());
    }
    println!();
    print!("{}", eq.render());
}

/// Watch the loopback capture, so "the EQ meter is flat" can be answered
/// without a device attached.
fn spectrum_dump() {
    let mut sm = sysmeter::SysMeter::new(21);
    if !sm.start() {
        eprintln!("cannot start: {}", sm.error().unwrap_or("?"));
        std::process::exit(1);
    }
    println!("capturing {} -- play something", sm.name().unwrap_or("?"));
    for _ in 0..60 {
        let bar: String = sm
            .bands()
            .iter()
            .map(|v| {
                if *v > 0.66 {
                    '#'
                } else if *v > 0.33 {
                    '+'
                } else if *v > 0.05 {
                    '.'
                } else {
                    ' '
                }
            })
            .collect();
        println!("  |{bar}|");
        std::thread::sleep(std::time::Duration::from_millis(150));
    }
    sm.stop();
}
