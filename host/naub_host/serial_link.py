"""USB CDC link to the naub device: line framing + data-port autodetection."""
import time

import serial
import serial.tools.list_ports

BAUD = 115200  # nominal; USB CDC ignores the rate


class SerialLink:
    def __init__(self, port, timeout=0.05):
        self.port = port
        self.ser = serial.Serial(port, BAUD, timeout=timeout)
        self._buf = bytearray()

    def write_line(self, s):
        self.ser.write((s + "\n").encode("utf-8"))

    def read_lines(self):
        """Non-blocking: return whatever complete lines are currently available."""
        n = self.ser.in_waiting
        if n:
            self._buf.extend(self.ser.read(n))
        out = []
        while True:
            i = self._buf.find(b"\n")
            if i < 0:
                break
            raw = bytes(self._buf[:i]).rstrip(b"\r")
            del self._buf[:i + 1]
            try:
                out.append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                pass
        return out

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass


# USB vendor IDs that ship CircuitPython on RP2 boards (Raspberry Pi, Adafruit).
PICO_VIDS = (0x2E8A, 0x239A)


def list_ports():
    return [(p.device, p.description) for p in serial.tools.list_ports.comports()]


def _candidates():
    """Serial ports worth probing, best-first, with Bluetooth excluded.

    Opening a Bluetooth SPP port can block for seconds, so we skip those
    outright and try the Pico's USB ports first -- in practice we hit the data
    port immediately and never touch anything slow.
    """
    ports = list(serial.tools.list_ports.comports())

    def is_bluetooth(p):
        blob = ("%s %s" % (p.description or "", p.hwid or "")).lower()
        return "bluetooth" in blob or "bthenum" in blob

    def rank(p):
        if p.vid in PICO_VIDS:
            return 0
        if "usb" in (p.description or "").lower() or (p.hwid or "").startswith("USB"):
            return 1
        return 2

    usable = [p for p in ports if not is_bluetooth(p)]
    usable.sort(key=rank)
    return [p.device for p in usable]


def autodetect(probe_timeout=0.6):
    """Probe serial ports (best-first) for the naub data interface.

    Open a candidate, send `hello`, and watch for a `naub ` banner. Only the
    firmware's data port answers; the REPL/console port stays silent. Returns the
    port name, or None.
    """
    for dev in _candidates():
        try:
            ser = serial.Serial(dev, BAUD, timeout=0.2)
        except (serial.SerialException, OSError):
            continue
        try:
            ser.reset_input_buffer()
            ser.write(b"hello\n")
            deadline = time.time() + probe_timeout
            while time.time() < deadline:
                line = ser.readline()
                if line.startswith(b"naub "):
                    return dev
        except (serial.SerialException, OSError):
            pass
        finally:
            ser.close()
    return None
