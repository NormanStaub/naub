//! USB CDC serial transport: line framing + naub data-port autodetect.
//!
//! Discovery is by USB VID/PID (not COM-port probing of every device), so
//! Bluetooth/PCI ports are never opened -- that class of hang simply can't
//! happen. The Pico exposes two CDC ports (console + data) and the data one is
//! picked by interface number; see [`autodetect`] for why it is not picked by
//! asking the device to identify itself.

use std::io::{ErrorKind, Read, Write};
use std::time::Duration;

use serialport::{SerialPort, SerialPortType};

/// USB vendor IDs we treat as candidate naub devices, probed first.
const NAUB_VIDS: [u16; 2] = [0x2E8A, 0x239A]; // Raspberry Pi (Pico), Adafruit

pub struct SerialLink {
    port: Box<dyn SerialPort>,
    buf: Vec<u8>,
}

impl SerialLink {
    /// How long to let the device notice DTR before the first line goes out.
    /// See [`SerialLink::open`].
    const DTR_SETTLE: Duration = Duration::from_millis(250);

    pub fn open(name: &str) -> serialport::Result<Self> {
        // Baud is irrelevant for USB CDC but required by the API. Short read
        // timeout so read_lines() polls without blocking the event loop.
        //
        // DTR is not a formality. CircuitPython gates `usb_cdc.data` on it:
        // the device's `Link.send()` checks `port.connected`, which is false
        // until the host asserts DTR, and it silently DROPS every line until
        // then. It is set as part of opening (what pyserial's `dtr = True`
        // before `open()` does) rather than afterwards, so there is no window
        // where the port is open and the device still thinks nobody is there.
        let port = serialport::new(name, 115_200)
            .timeout(Duration::from_millis(10))
            .dtr_on_open(true)
            .open()?;
        // Even then, `connected` does not flip the instant the line does.
        // Writing `hello` immediately means the device runs `announce()` while
        // it still believes it is unconnected and throws the banner away -- so
        // the host sees a device that never identifies itself, on a link that
        // is otherwise working perfectly. Costs a quarter second, once, per
        // connect.
        std::thread::sleep(Self::DTR_SETTLE);
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

/// Find the naub **data** port among the board's two CDC interfaces.
///
/// By USB interface number, not by handshake. CircuitPython puts the REPL on
/// the first CDC interface and the data port on the second, so the lowest
/// interface number is the console and the highest is the data port -- which is
/// how `naub_bridge.py::find_ports` does it, and it is the right way round.
///
/// The obvious alternative -- send `hello` and keep whichever answers the
/// banner -- looks more rigorous and is actually wrong: the device answers only
/// while `usb_cdc.data.connected` is true, and the whole premise of the link is
/// that the **host** pushes state to a device that boots knowing nothing
/// (PROTOCOL.md §3). A device that is up but not currently talking back is a
/// normal state, and a probe that insists on a reply refuses to connect to it
/// at all -- observed: the Python bridge streaming happily to a board that
/// `--probe` reported as "not found".
///
/// Before `boot.py` takes effect there is only ONE port, which is how "not
/// power cycled yet" is detected -- there is no data port to return.
pub fn autodetect() -> Option<String> {
    let mut candidates: Vec<(u8, String)> = serialport::available_ports()
        .ok()?
        .into_iter()
        .filter_map(|p| match &p.port_type {
            SerialPortType::UsbPort(info) if NAUB_VIDS.contains(&info.vid) => {
                Some((info.interface.unwrap_or(0), p.port_name.clone()))
            }
            _ => None,
        })
        .collect();
    if candidates.len() < 2 {
        return None; // console only: boot.py has not taken effect yet
    }
    candidates.sort_by_key(|(iface, _)| *iface);
    candidates.pop().map(|(_, name)| name)
}

