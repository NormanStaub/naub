"""Deploy a demo (or your own code.py) to the naub V2 CIRCUITPY drive.

    python deploy.py ball|gray|icons [--drive K:] [--port COM9] [--no-watch]
    python deploy.py path\\to\\my_code.py

Order matters: support modules first, code.py LAST — every copy triggers a
CircuitPython auto-reload and only the final one should run.  Serial is
strictly READ-ONLY: host->device console writes hang on this board (bring-up
finding), so we only listen for the boot banner / tracebacks.
"""
import os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICE = os.path.normpath(os.path.join(HERE, '..', 'device'))
DEMOS = os.path.normpath(os.path.join(HERE, '..', 'demos'))

LIBS = ('gfx.py', 'ccfont.py', 'st7565.py', 'naubhw.py')
DEMO_MAP = {
    'ball':  ('demo_ball.py', ()),
    'gray':  ('demo_gray_modes.py', ('graybars.py', 'graygrad.py')),
    'icons': ('demo_app_icons.py', ('appicons.py',)),
}


def watch(port, seconds=5.0):
    try:
        import serial
    except ImportError:
        print('(pyserial not installed - skipping console watch)')
        return
    s = serial.Serial()
    s.port = port
    s.baudrate = 115200
    s.timeout = 0.2
    s.dtr = True
    s.rts = True
    try:
        s.open()
    except Exception as e:
        print('(serial %s unavailable: %s)' % (port, e))
        return
    buf = b''
    end = time.time() + seconds
    while time.time() < end:
        buf += s.read(4096)
    s.close()
    txt = buf.decode('utf-8', 'replace')
    txt = re.sub(r'\x1b\][^\x1b]*\x1b\\', '', txt)          # OSC titles
    txt = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', txt)         # CSI
    print('----- console (%ss) -----' % seconds)
    print(txt.strip() or '(no output)')
    print('----- end -----')


def main(argv):
    drive = 'K:'
    port = 'COM9'
    do_watch = True
    args = []
    it = iter(argv)
    for a in it:
        if a == '--drive':
            drive = next(it)
        elif a == '--port':
            port = next(it)
        elif a == '--no-watch':
            do_watch = False
        else:
            args.append(a)
    if len(args) != 1:
        sys.exit(__doc__)

    if args[0] in DEMO_MAP:
        code, extras = DEMO_MAP[args[0]]
        code = os.path.join(DEMOS, code)
        extras = [os.path.join(DEMOS, e) for e in extras]
    else:
        code = args[0]
        extras = []
        if not os.path.exists(code):
            sys.exit('no such demo or file: %s' % args[0])

    root = drive if drive.endswith('\\') else drive + '\\'
    for lib in LIBS:
        shutil.copy(os.path.join(DEVICE, lib), root + lib)
        print('-> %s%s' % (root, lib))
    for e in extras:
        shutil.copy(e, root + os.path.basename(e))
        print('-> %s%s' % (root, os.path.basename(e)))
    shutil.copy(code, root + 'code.py')
    print('-> %scode.py  (%s)' % (root, os.path.basename(code)))
    if do_watch:
        watch(port)


if __name__ == '__main__':
    main(sys.argv[1:])
