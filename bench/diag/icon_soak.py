"""Can the link carry `aicon` lines?  They are 1.1 kB against a ~200 byte ring.

Stands in for the daemon on the data port: meters at the real rate, plus rounds
of full-size icon lines, as tight as the link will take them and then paced.
Watches the console for what the device made of it, since a line that lost any
bytes fails the length check and is printed as a bad line.  Stop naubd first.

    python icon_soak.py [--data COM10] [--console COM9] [--count 40]
"""
import argparse
import base64
import re
import threading
import time

import serial

BYTES = 800


def line(i):
    # different every time, so a stale or merged line cannot pass for a good one
    blob = bytes((i * 31 + n * 7) & 255 for n in range(BYTES))
    return ('aicon %s SOAK%d\n' % (base64.b64encode(blob).decode(), i)).encode()


class Console(threading.Thread):
    """Everything the device prints, timestamped, until told to stop."""

    def __init__(self, port):
        super().__init__(daemon=True)
        self.s = serial.Serial(port, 115200, timeout=0.2)
        self.log = []
        self.done = False

    def run(self):
        buf = b''
        while not self.done:
            buf += self.s.read(4096)
            *full, buf = buf.split(b'\n')
            for raw in full:
                self.log.append((time.time(), raw.decode('ascii', 'replace').strip()))

    def since(self, t, pat):
        return [m for ts, m in self.log if ts >= t and re.search(pat, m)]


def round_(s, con, gap, count, base):
    """Send `count` icons `gap` apart under 50 Hz meters; report the damage."""
    t0 = time.time()
    worst = 0.0
    next_icon = t0
    sent = 0
    next_meter = t0
    while sent < count or time.time() < next_icon + 0.5:
        now = time.time()
        if now >= next_meter:
            s.write(b'meter 10 20 30\n')
            next_meter = now + 0.02
        if sent < count and now >= next_icon:
            w0 = time.time()
            s.write(line(base + sent))
            worst = max(worst, time.time() - w0)
            sent += 1
            next_icon = time.time() + gap
        time.sleep(0.001)
    took = time.time() - t0
    # the fps line comes every 1024 frames (11 s): wait for one wholly after
    time.sleep(1.0)
    ok = len(con.since(t0, r'^icon: SOAK'))
    bad = con.since(t0, r'ignoring bad line|Traceback|Error')
    print('gap %3d ms: %d/%d stored, %d bad, worst write %.0f ms, %.1f s'
          % (gap * 1000, ok, count, len(bad), worst * 1000, took))
    for m in bad[:5]:
        print('   ', m[:110])
    return ok == count and not bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='COM10')
    ap.add_argument('--console', default='COM9')
    ap.add_argument('--count', type=int, default=40)
    a = ap.parse_args()

    con = Console(a.console)
    con.start()
    s = serial.Serial()
    s.port = a.data
    s.baudrate = 115200
    s.timeout = 0
    s.write_timeout = 0.5       # what the daemon runs with
    s.dtr = True
    s.open()
    time.sleep(0.25)
    s.write(b'\nhello\n')

    # A baseline, then each gap, and the fps line each one overlapped.
    t = time.time()
    while time.time() - t < 12:
        s.write(b'meter 10 20 30\n')
        time.sleep(0.02)
    print('baseline:', (con.since(t, r' fps ') or ['(no fps line)'])[-1][:40])

    good = True
    base = 0
    for gap in (0.0, 0.05, 0.1):
        t = time.time()
        good = round_(s, con, gap, a.count, base) and good
        base += a.count
        # stay in step until the frame counter has reported on this round
        while time.time() - t < 12 or not con.since(t, r' fps '):
            s.write(b'meter 10 20 30\n')
            time.sleep(0.02)
        print('    fps:', [m[:9] for m in con.since(t, r' fps ')])
    free = [m for _, m in con.log if m.startswith('icon:')]
    print('last:', free[-1] if free else '(none)')
    con.done = True
    print('PASS' if good else 'FAIL')


if __name__ == '__main__':
    main()
