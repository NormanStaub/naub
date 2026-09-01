#!/usr/bin/env python3
"""Run the Stage 4 display bring-up on the board and relay your keyboard to it.

    python3 run_stage4.py                  # full interactive bring-up
    python3 run_stage4.py --check          # part A only: bus short check, no panel drive

Pastes stage4_display.py over the REPL (no CIRCUITPY drive needed), then hands
you an interactive terminal so the [ENTER to continue] prompts work.
Ctrl-] to detach.
"""
import os, sys, termios, tty, select, time, glob

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "stage4_display.py")

def open_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("No board found on /dev/cu.usbmodem*  - plug it in.")
    fd = os.open(ports[0], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    a = termios.tcgetattr(fd); a[0] = a[1] = a[3] = 0
    a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL; a[4] = a[5] = termios.B115200
    a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, a)
    return fd, ports[0]

def drain(fd, sec):
    out = b""; end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try: out += os.read(fd, 8192)
            except OSError: pass
    return out

def wait_prompt(fd, timeout=10):
    t0 = time.time(); buf = b""
    while time.time() - t0 < timeout:
        os.write(fd, b"\x03")
        buf = (buf + drain(fd, 0.4))[-500:]
        if b">>>" in buf:
            return True
    return False

def main():
    src = open(SRC).read()
    if "--check" in sys.argv:
        src = src.split("# ---------------------------------------------------------------- B: init")[0]
        src += '\nprint("\\nBus check done - panel not driven.")\n'
    fd, name = open_port()
    print("port %s   pasting stage4_display.py (%d lines)..." % (name, len(src.splitlines())))
    if not wait_prompt(fd):
        sys.exit("No REPL prompt - replug the board.")
    drain(fd, 0.4)
    os.write(fd, b"\x05"); time.sleep(0.4)
    if b"paste mode" not in drain(fd, 1.0):
        sys.exit("Board would not enter paste mode.")
    for l in src.split("\n"):
        os.write(fd, l.encode() + b"\r\n"); time.sleep(0.006)
    os.write(fd, b"\x04")
    print("running. Ctrl-] to detach.\n" + "-" * 60)

    isatty = sys.stdin.isatty()
    old = termios.tcgetattr(sys.stdin) if isatty else None
    try:
        if isatty:
            tty.setraw(sys.stdin.fileno())
        while True:
            r, _, _ = select.select([fd, sys.stdin], [], [], 0.1)
            if fd in r:
                try: b = os.read(fd, 4096)
                except OSError: b = b""
                if b:
                    sys.stdout.write(b.decode("utf-8", "replace"))
                    sys.stdout.flush()
            if sys.stdin in r:
                c = os.read(sys.stdin.fileno(), 64)
                if b"\x1d" in c:      # Ctrl-]
                    break
                if not isatty and not c:
                    break
                os.write(fd, c.replace(b"\n", b"\r\n") if not isatty else c)
    except KeyboardInterrupt:
        pass
    finally:
        if isatty:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        os.close(fd)
        print("\n" + "-" * 60 + "\ndetached.")

main()
