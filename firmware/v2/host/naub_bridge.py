"""Drive the mixer screen from this PC's real audio.

    python naub_bridge.py [--port COMx] [--rate 50] [--wait S] [--seconds S]
    python naub_bridge.py --ports          # what serial ports are there?
    python naub_bridge.py --once           # don't reconnect; exit on unplug

LEAVE THIS RUNNING.  Without it the device has no audio to show and no way to
apply a knob: the panel is a display, the host is the thing that talks to
WASAPI.  It reconnects by itself when the board reloads (every deploy.py does)
or is replugged, so it can just stay in a terminal.

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
    ->  ping                  liveness probe; the device answers `pong`
    <-  vol   <ch> <0..100>   a knob moved
    <-  mute  <ch> <0|1>      a knob was held; ch 3 is the system knob's click
    <-  pick  <ch> <APP>      the switcher assigned an app to a column
    <-  mode  <idx>           the mode bar was flipped (BTN1/BTN2)
    <-  btn   <n> <0|1>       an aux key; BTN3-5 are media transport and are
                              ALSO sent as USB HID, which is what actually
                              moves the track -- this line is only telemetry
"""
import re
import sys
import time

import serial
from serial.tools import list_ports

import appmeter
import eqapo
import sysmeter

NCOL = 3
SBANDS = 21               # must match device/eqscreen.NMETER
EQ_MODE = 1               # ... and demo_mixer.EQ_MODE
VID_PID = '2E8A'
RESCAN_S = 1.0            # how often to re-enumerate audio sessions
RECONNECT_S = 1.0         # how often to look for the board after it vanishes


class Dropped(Exception):
    """The device went away -- a reload, a replug, or the port closing.

    This is the NORMAL case, not an error: every `deploy.py` reloads the board
    and drops the CDC link.  A bridge that died here would leave the panel
    showing nothing and silently stop applying knob moves, which looks exactly
    like a firmware bug and is not one."""


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
        self.muted = [None] * (NCOL + 1)   # ... + the system endpoint
        self.eq = eqapo.ParametricEq()
        # The EQ screen's meter is a REAL analysis of the system output, not an
        # invented shape: per-app spectrum does not exist, but the endpoint can
        # be captured.  Optional -- without it the screen just shows a floor.
        self.sys = sysmeter.SysMeter(nband=SBANDS)
        self.sys_ok = self.sys.start()
        if not self.sys_ok:
            print('system spectrum unavailable: %s' % self.sys.error)
        self.mode = 0
        self._rx = b''

    def send(self, s):
        try:
            self.ser.write((s + '\n').encode('ascii', 'replace'))
        except serial.SerialTimeoutException:
            pass                      # device not draining; drop the line
        except (serial.SerialException, OSError):
            raise Dropped()

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
        m = 1 if self.src.system_muted() else 0
        if m != self.muted[NCOL]:
            self.muted[NCOL] = m
            self.send('mute %d %d' % (NCOL, m))

    def handle(self, line):
        t = line.split()
        if not t:
            return
        if t[0] == 'eqp' and len(t) == 4:
            i, param, val = int(t[1]), t[2], t[3]
            if 0 <= i < len(self.eq.bands):
                if param == 'kind':
                    self.eq.set_band(i, kind=val)
                elif param == 'on':
                    self.eq.set_band(i, on=val == '1')
                elif param == 'gain':
                    self.eq.set_band(i, db=float(val))
                elif param == 'freq':
                    self.eq.set_band(i, f0=float(val))
                elif param == 'q':
                    self.eq.set_band(i, q=float(val))
                print('  eq band %d %s -> %s' % (i, param, val))
        elif t[0] == 'eqflat':
            self.eq.flat()
            print('  eq flat')
        elif t[0] == 'mode' and len(t) == 2:
            # Track it so the spectrum is only streamed to the screen that
            # draws it -- 21 numbers at 50 Hz is bandwidth worth not wasting.
            self.mode = int(t[1])
        elif t[0] == 'pong':
            print('  pong')
        elif t[0] == 'mode' and len(t) == 2:
            print('  mode -> %s' % t[1])
        elif t[0] == 'btn' and len(t) == 3:
            if t[2] == '1':
                print('  aux key %s' % t[1])
        elif t[0] == 'vol' and len(t) == 3:
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
            if ch == NCOL:                    # the system knob's click
                self.src.set_system_mute(bool(m))
            elif 0 <= ch < len(self.keys) and self.keys[ch]:
                self.src.set_mute(self.keys[ch], bool(m))
            self.muted[ch] = m            # ours now; do not push it back
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
        try:
            n = self.ser.in_waiting
            if not n:
                return
            self._rx += self.ser.read(n)
        except (serial.SerialException, OSError):
            raise Dropped()
        if b'\n' not in self._rx:
            return
        parts = self._rx.split(b'\n')
        self._rx = parts.pop()                # keep the trailing partial line
        for raw in parts:
            self.handle(raw.decode('utf-8', 'replace').strip())

    def push_eq(self):
        """Everything the EQ screen needs: what the engine can do, whether it
        would be heard at all, and where every band currently sits."""
        ok, why = self.eq.status()
        self.send('eqcaps %d %g %g %g %d %d'
                  % (len(self.eq.bands), eqapo.FMIN, eqapo.FMAX, eqapo.MAXDB,
                     48000, 1 if ok else 0))
        for i, b in enumerate(self.eq.bands):
            self.send('eqband %d %s' % (i, b.wire()))
        print('eq: %s -- %s' % ('ready' if ok else 'INERT', why))

    def resync(self):
        """Push everything the device needs after a (re)connect.  It boots with
        nothing and is not the source of truth for volume -- see PROTOCOL.md
        section 3."""
        self.keys = []
        self.apps = []
        self.vol = [None] * (NCOL + 1)
        self.muted = [None] * (NCOL + 1)
        self.send('hello')
        self.push_apps(force=True)
        self.push_names(force=True)
        self.push_state()
        self.push_eq()

    def run(self, seconds=0.0):
        """Stream until --seconds elapses, or forever.  Raises Dropped when the
        device goes away, which the caller turns into a reconnect."""
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
                if self.sys_ok and self.mode == EQ_MODE:
                    self.send('smeter %s' % ' '.join(
                        '%d' % int(v * 100 + 0.5) for v in self.sys.bands()))
                sent += 1
                if now >= next_log:
                    next_log = now + 1.0
                    print('  %s' % '  '.join(
                        '%-10s %3d%% %s' % (k or '-', int(v * 100),
                                            '#' * int(v * 18))
                        for k, v in zip(self.keys, lv)))
            # Debounced: a knob per detent would otherwise make Equalizer
            # APO re-parse the user's whole config a hundred times a second.
            self.eq.tick(now)
            if now >= next_scan:
                next_scan = now + RESCAN_S
                self.src.refresh()
                self.push_apps()
                self.push_names()
                self.push_state()
            time.sleep(0.002)


def serve(port, rate, seconds):
    """One connected session.  -> True if it ended on its own terms."""
    ser = open_data(port)
    try:
        b = Bridge(ser, rate)
        b.resync()
        b.run(seconds)
        return True
    finally:
        try:
            ser.close()
        except Exception:
            pass


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
    # This is usually left running in a terminal or piped to a log, and a
    # block-buffered log that says nothing for 4 kB is useless for telling
    # whether the link is alive.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    port = None
    rate = 50.0
    seconds = 0.0
    wait = 0.0
    once = False
    it = iter(argv)
    for a in it:
        if a == '--once':
            once = True
        elif a == '--port':
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

    fixed_port = port
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
    print('data port: %s at %g Hz%s'
          % (port, rate, '' if once else '  (reconnects on reload)'))
    fixed = fixed_port
    try:
        while True:
            try:
                serve(port, rate, seconds)
                return                      # --seconds elapsed: done
            except Dropped:
                print('device dropped (reload or replug) - waiting for it')
            except (serial.SerialException, OSError) as e:
                print('port error: %s - waiting for it' % e)
            if once:
                return
            # A reload re-enumerates, so re-find the port rather than trusting
            # the old name; only an explicit --port stays pinned.
            while True:
                time.sleep(RECONNECT_S)
                _, found = find_ports()
                port = fixed if fixed else found
                if port and (found or fixed):
                    break
            # Deliberately not announcing a reconnect here: the port merely
            # reappeared in the enumeration, and opening it can still fail.
            # serve() saying nothing and then streaming is the real signal.
    except KeyboardInterrupt:
        print('\nstopped')


if __name__ == '__main__':
    main(sys.argv[1:])
