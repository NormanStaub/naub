"""Drive the mixer screen from this PC's real audio.

    python naub_bridge.py [--port COMx] [--rate 50] [--wait S] [--seconds S]
    python naub_bridge.py --ports          # what serial ports are there?

Streams the live per-app WASAPI levels (appmeter.py) to the device over the USB
CDC *data* port, and applies knob moves coming back.  The device runs
demos/demo_mixer.py, which falls back to simulated levels when no host is
talking to it.

Requires device/boot.py deployed and the board POWER CYCLED once, so the data
port exists -- see that file.  Run `python naub_bridge.py --ports` to see what
was found.

Protocol (PROTOCOL.md; `meter` is the V2 addition):

    ->  label <ch> <text>     column name, ch 0-2
    ->  level <ch> <0..100>   volume; ch 3 is the system/master
    ->  mute  <ch> <0|1>
    ->  meter <v0> <v1> <v2>  column levels, sent at --rate Hz
    ->  apps  <A,B,C>         every app with a live session (the switcher list)
    ->  ameter <v0> <v1> ...  one level per app in that list, at --rate Hz
    <-  vol   <ch> <0..100>   a knob moved
    <-  mute  <ch> <0|1>      a knob was held
    <-  pick  <ch> <APP>      the switcher assigned an app to a column
"""
import re
import sys
import time

import serial
from serial.tools import list_ports

import appmeter

NCOL = 3
VID_PID = '2E8A'
RESCAN_S = 1.0            # how often to re-enumerate audio sessions


def _iface(p):
    """USB interface number behind a COM port, or 0 if it cannot be told.

    Windows reports this inconsistently.  On this board the console comes back
    with location=None and the data port with location='1-10.3:x.2' -- the
    trailing number is the interface.  Other setups tag the hwid with MI_00 /
    MI_02 instead, so try both.
    """
    m = re.search(r'MI_(\d+)', (p.hwid or '').upper())
    if m:
        return int(m.group(1))
    m = re.search(r'[:.](\d+)$', p.location or '')
    if m:
        return int(m.group(1))
    return 0


def find_ports():
    """-> (console, data) device names for the naub board.

    CircuitPython puts the REPL on the first CDC interface and the data port on
    the second, so the lowest interface number is the console and the highest
    is the data port.  Before boot.py takes effect there is only ONE port --
    which is how 'not power cycled yet' is detected.
    """
    cands = [p for p in list_ports.comports()
             if VID_PID in (p.hwid or '').upper()]
    if not cands:
        return None, None
    cands.sort(key=_iface)
    if len(cands) == 1:
        return cands[0].device, None
    return cands[0].device, cands[-1].device


def open_data(port):
    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 0
    s.write_timeout = 0.5
    s.dtr = True
    s.open()
    return s


class Bridge:
    def __init__(self, ser, rate=50.0):
        self.ser = ser
        self.rate = rate
        self.src = appmeter.AppMeters()
        self.keys = []            # what each column shows
        self.apps = []            # every app with a live session
        self.pinned = [None] * NCOL   # columns the device chose by hand
        self.vol = [None] * (NCOL + 1)
        self.muted = [None] * NCOL
        self._rx = b''

    def send(self, s):
        try:
            self.ser.write((s + '\n').encode('ascii', 'replace'))
        except serial.SerialTimeoutException:
            pass

    def columns(self):
        """Which app each column shows.  A column the user assigned from the
        switcher stays put; the rest track the most active sessions.  Without
        the pinning, a hand-picked column would be overwritten the moment the
        busiest three changed."""
        auto = [k for k in self.src.top(NCOL * 2)
                if k and k not in self.pinned]
        out = []
        for i in range(NCOL):
            if self.pinned[i]:
                out.append(self.pinned[i])
            else:
                out.append(auto.pop(0) if auto else '')
        return out

    def push_apps(self, force=False):
        """The switcher's list: everything making noise, in full.  Sent as one
        comma-separated line because process names can contain spaces."""
        apps = self.src.active()
        if apps == self.apps and not force:
            return False
        self.apps = apps
        self.send('apps %s' % ','.join(k.upper() for k in apps))
        print('apps: %s' % (', '.join(apps) or '(none)'))
        return True

    def push_names(self, force=False):
        keys = self.columns()
        if keys == self.keys and not force:
            return False
        self.keys = keys
        for i, k in enumerate(keys):
            self.send('label %d %s' % (i, (k or '').upper()))
        print('columns: %s' % ' | '.join(k or '-' for k in keys))
        return True

    def push_state(self):
        """Keep the panel honest about volume/mute changed elsewhere."""
        for i, k in enumerate(self.keys):
            v = int(self.src.volume(k) * 100 + 0.5) if k else 0
            if v != self.vol[i]:
                self.vol[i] = v
                self.send('level %d %d' % (i, v))
            m = 1 if (k and self.src.muted(k)) else 0
            if m != self.muted[i]:
                self.muted[i] = m
                self.send('mute %d %d' % (i, m))
        v = int(self.src.system_volume() * 100 + 0.5)
        if v != self.vol[NCOL]:
            self.vol[NCOL] = v
            self.send('level %d %d' % (NCOL, v))

    def handle(self, line):
        t = line.split()
        if not t:
            return
        if t[0] == 'vol' and len(t) == 3:
            ch, v = int(t[1]), int(t[2])
            if ch == NCOL:
                self.src.set_system_volume(v / 100.0)
            elif 0 <= ch < len(self.keys) and self.keys[ch]:
                self.src.set_volume(self.keys[ch], v / 100.0)
            if ch <= NCOL:
                self.vol[ch] = v          # ours now; do not echo it back
            print('  knob %d -> %d%%' % (ch, v))
        elif t[0] == 'mute' and len(t) == 3:
            ch, m = int(t[1]), int(t[2])
            if 0 <= ch < len(self.keys) and self.keys[ch]:
                self.src.set_mute(self.keys[ch], bool(m))
                self.muted[ch] = m
            print('  mute %d -> %d' % (ch, m))
        elif t[0] == 'pick' and len(t) >= 3:
            ch = int(t[1])
            key = ' '.join(t[2:]).lower()
            if 0 <= ch < NCOL:
                self.pinned[ch] = key
                print('  column %d pinned to %s' % (ch, key))
                self.push_names(force=True)
                self.push_state()
        elif t[0] == 'naub':
            print('  device: %s' % line)
            self.push_apps(force=True)
            self.push_names(force=True)
            self.push_state()

    def poll_rx(self):
        n = self.ser.in_waiting
        if n:
            self._rx += self.ser.read(n)
            if b'\n' in self._rx:
                parts = self._rx.split(b'\n')
                self._rx = parts.pop()
                for raw in parts:
                    self.handle(raw.decode('utf-8', 'replace').strip())

    def run(self, seconds=0.0):
        self.send('hello')
        self.push_apps(force=True)
        self.push_names(force=True)
        self.push_state()
        period = 1.0 / self.rate
        t0 = time.monotonic()
        next_meter = t0
        next_scan = t0 + RESCAN_S
        next_log = t0 + 1.0
        sent = 0
        while True:
            now = time.monotonic()
            if seconds and now - t0 >= seconds:
                print('ran %.1fs, sent %d meter frames' % (now - t0, sent))
                return
            self.poll_rx()
            if now >= next_meter:
                next_meter = now + period
                lv = self.src.levels(self.keys)
                self.send('meter %d %d %d' % tuple(
                    int(min(1.0, x) * 100 + 0.5) for x in lv))
                if self.apps:      # the switcher meters whichever app it shows
                    self.send('ameter %s' % ' '.join(
                        '%d' % int(min(1.0, x) * 100 + 0.5)
                        for x in self.src.levels(self.apps)))
                sent += 1
                if now >= next_log:
                    next_log = now + 1.0
                    print('  %s' % '  '.join(
                        '%-10s %3d%% %s' % (k or '-', int(v * 100),
                                            '#' * int(v * 18))
                        for k, v in zip(self.keys, lv)))
            if now >= next_scan:
                next_scan = now + RESCAN_S
                self.src.refresh()
                self.push_apps()
                self.push_names()
                self.push_state()
            time.sleep(0.002)


def wait_for_data(timeout):
    """Block until the data port shows up (i.e. the board was power cycled)."""
    end = time.monotonic() + timeout
    seen = None
    while time.monotonic() < end:
        cons, data = find_ports()
        if data:
            return data
        if cons != seen:
            seen = cons
            print('  waiting for the data port; console on %s' % cons)
        time.sleep(0.5)
    return None


def main(argv):
    port = None
    rate = 50.0
    seconds = 0.0
    wait = 0.0
    it = iter(argv)
    for a in it:
        if a == '--port':
            port = next(it)
        elif a == '--rate':
            rate = float(next(it))
        elif a == '--seconds':
            seconds = float(next(it))
        elif a == '--wait':
            wait = float(next(it))
        elif a == '--ports':
            for p in list_ports.comports():
                print('%-8s %s [%s]' % (p.device, p.description, p.hwid))
            print('\ndetected (console, data) = %r' % (find_ports(),))
            return
        else:
            sys.exit(__doc__)

    if port is None:
        cons, port = find_ports()
        if port is None and wait:
            print('waiting up to %gs for a power cycle...' % wait)
            port = wait_for_data(wait)
        if port is None:
            sys.exit('No data port found.\n'
                     'Deploy device/boot.py and FULLY POWER CYCLE the board '
                     '(unplug/replug) so USB re-enumerates with two CDC '
                     'interfaces.\nConsole port seen: %s\n'
                     'Run with --ports to list what is there.' % cons)
    print('data port: %s at %g Hz' % (port, rate))
    ser = open_data(port)
    try:
        Bridge(ser, rate).run(seconds)
    except KeyboardInterrupt:
        print('\nstopped')
    finally:
        ser.close()


if __name__ == '__main__':
    main(sys.argv[1:])
