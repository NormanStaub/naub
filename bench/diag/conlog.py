"""Timestamped capture of the device console (COM9), for catching a crash.

The console is where CircuitPython puts tracebacks, so this is the only place
the reason a board died is ever written down.  It reconnects, because the
interesting failure re-enumerates the port and a logger that dies with it
records nothing about what happened next.

    python conlog.py [--port COM9] [--out console.log]
"""
import sys
import time

import serial
from serial.tools import list_ports

VID = '2E8A'


def _iface(p):
    """USB interface number behind a COM port -- same rule as
    naub_bridge._iface.  Windows reports this inconsistently: some setups tag
    the hwid with MI_00 / MI_02, others leave that out and put the interface at
    the end of `location`.  Both have to be tried.

    Getting this wrong is not harmless: falling back to sorting by NAME picks
    'COM10' before 'COM9' (because '1' < '9'), i.e. the data port the daemon is
    already using rather than the console."""
    import re
    m = re.search(r'MI_(\d+)', (p.hwid or '').upper())
    if m:
        return int(m.group(1))
    m = re.search(r'[:.](\d+)$', p.location or '')
    if m:
        return int(m.group(1))
    return 0


def console_port():
    """Lowest interface number among the board's CDC ports == the console."""
    cands = [p for p in list_ports.comports()
             if VID in (p.hwid or '').upper()]
    if not cands:
        return None
    cands.sort(key=_iface)
    return cands[0].device


def main(argv):
    port = None
    out = 'console.log'
    it = iter(argv)
    for a in it:
        if a == '--port':
            port = next(it)
        elif a == '--out':
            out = next(it)
    f = open(out, 'a', buffering=1, encoding='utf-8', errors='replace')

    def note(msg):
        line = '[%s] === %s ===' % (time.strftime('%H:%M:%S'), msg)
        f.write(line + '\n')
        print(line, flush=True)

    note('conlog start')
    ser = None
    pending = ''
    last_seen = None
    while True:
        if ser is None:
            name = port or console_port()
            if name is None:
                if last_seen != 'gone':
                    last_seen = 'gone'
                    note('no board enumerated (USB dropped)')
                time.sleep(0.5)
                continue
            try:
                ser = serial.Serial(name, 115200, timeout=0)
            except Exception as e:
                if last_seen != 'busy':
                    last_seen = 'busy'
                    note('cannot open %s (%s)' % (name, e))
                time.sleep(0.5)
                continue
            last_seen = 'open'
            note('console open on %s' % name)
        try:
            n = ser.in_waiting
            chunk = ser.read(n) if n else b''
        except Exception as e:
            note('console read failed (%s) -- port went away' % e)
            try:
                ser.close()
            except Exception:
                pass
            ser = None
            continue
        if chunk:
            pending += chunk.decode('utf-8', 'replace')
            while '\n' in pending:
                line, pending = pending.split('\n', 1)
                line = line.replace('\r', '').rstrip()
                if line:
                    f.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), line))
        time.sleep(0.05)


if __name__ == '__main__':
    main(sys.argv[1:])
