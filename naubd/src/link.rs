//! USB CDC serial transport: line framing + naub data-port autodetect.
//!
//! Discovery is by USB VID/PID (not COM-port probing of every device), so
//! Bluetooth/PCI ports are never opened -- that class of hang simply can't happen.
//! The Pico exposes two CDC ports (console + data); we still send `hello` and keep
//! whichever answers the `naub ` banner, since only the data port replies.

use std::io::{ErrorKind, Read, Write};
use std::time::{Duration, Instant};

use serialport::{SerialPort, SerialPortType};

/// USB vendor IDs we treat as candidate naub devices, probed first.
const NAUB_VIDS: [u16; 2] = [0x2E8A, 0x239A]; // Raspberry Pi (Pico), Adafruit

pub struct SerialLink {
    port: Box<dyn SerialPort>,
    buf: Vec<u8>,
}

impl SerialLink {
    pub fn open(name: &str) -> serialport::Result<Self> {
        // Baud is irrelevant for USB CDC but required by the API. Short read
        // timeout so read_lines() polls without blocking the event loop.
        let port = serialport::new(name, 115_200)
            .timeout(Duration::from_millis(10))
            .open()?;
        Ok(Self { port, buf: Vec::with_capacity(512) })
    }

    pub fn write_line(&mut self, s: &str) -> std::io::Result<()> {
        self.port.write_all(s.as_bytes())?;
        self.port.write_all(b"\n")
    }

    /// Drain available bytes and return any complete lines. `Err` means the link
    /// dropped (device unplugged) -- the caller should reconnect. A read timeout
    /// is normal (no data) and yields `Ok` with whatever was already buffered.
    pub fn read_lines(&mut self) -> std::io::Result<Vec<String>> {
        let mut tmp = [0u8; 256];
        loop {
            match self.port.read(&mut tmp) {
                Ok(0) => break,
                Ok(n) => self.buf.extend_from_slice(&tmp[..n]),
                Err(ref e) if e.kind() == ErrorKind::TimedOut => break,
                Err(ref e) if e.kind() == ErrorKind::WouldBlock => break,
                Err(e) => return Err(e),
            }
        }
        Ok(self.take_lines())
    }

    fn take_lines(&mut self) -> Vec<String> {
        let mut out = Vec::new();
        while let Some(pos) = self.buf.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = self.buf.drain(..=pos).collect();
            let s = String::from_utf8_lossy(&line[..line.len() - 1])
                .trim_end_matches('\r')
                .trim()
                .to_string();
            if !s.is_empty() {
                out.push(s);
            }
        }
        out
    }
}

/// Human-readable port list for `--list-ports`.
pub fn list_ports() -> Vec<(String, String)> {
    serialport::available_ports()
        .unwrap_or_default()
        .into_iter()
        .map(|p| {
            let desc = match &p.port_type {
                SerialPortType::UsbPort(i) => format!(
                    "USB {:04x}:{:04x} {}",
                    i.vid,
                    i.pid,
                    i.product.clone().unwrap_or_default()
                ),
                SerialPortType::BluetoothPort => "Bluetooth".into(),
                SerialPortType::PciPort => "PCI".into(),
                SerialPortType::Unknown => "Unknown".into(),
            };
            (p.port_name, desc)
        })
        .collect()
}

/// Find the naub data port: probe only USB ports with a known VID, keep the one
/// that answers the banner.
pub fn autodetect() -> Option<String> {
    let ports = serialport::available_ports().ok()?;
    let mut candidates: Vec<String> = Vec::new();
    for p in &ports {
        if let SerialPortType::UsbPort(info) = &p.port_type {
            if NAUB_VIDS.contains(&info.vid) {
                candidates.push(p.port_name.clone());
            }
        }
    }
    candidates.into_iter().find(|name| probe(name))
}

fn probe(name: &str) -> bool {
    let mut link = match SerialLink::open(name) {
        Ok(l) => l,
        Err(_) => return false,
    };
    if link.write_line(&crate::protocol::hello()).is_err() {
        return false;
    }
    let deadline = Instant::now() + Duration::from_millis(800);
    while Instant::now() < deadline {
        if let Ok(lines) = link.read_lines() {
            if lines.iter().any(|l| l.starts_with("naub ")) {
                return true;
            }
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    false
}
