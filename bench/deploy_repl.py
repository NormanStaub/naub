#!/usr/bin/env python3
"""Deploy a .py to the board as code.py over the REPL - no CIRCUITPY drive needed.

    python3 deploy_repl.py stage3_test.py

Works when macOS binds the mass-storage interface but never produces a block
device (which is the state this board is in). Writes via storage.remount(),
then soft-reboots so the file runs.
"""
import os, sys, termios, select, time, glob, base64

src = sys.argv[1] if len(sys.argv) > 1 else "stage3_test.py"
if not os.path.isabs(src):
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), src)
data = open(src, "rb").read()
b64 = base64.b64encode(data).decode()
CH = 180
chunks = [b64[i:i+CH] for i in range(0, len(b64), CH)]

ports = sorted(glob.glob("/dev/cu.usbmodem*"))
if not ports:
    sys.exit("No board found on /dev/cu.usbmodem*")
PORT = ports[0]
print("port %s   %s -> code.py   %d bytes, %d chunks" % (PORT, os.path.basename(src), len(data), len(chunks)))

fd = os.open(PORT, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
a = termios.tcgetattr(fd); a[0] = a[1] = a[3] = 0
a[2] = termios.CS8 | termios.CREAD | termios.CLOCAL; a[4] = a[5] = termios.B115200
a[6][termios.VMIN] = 0; a[6][termios.VTIME] = 0
termios.tcsetattr(fd, termios.TCSANOW, a)

def drain(sec):
    out = b""; end = time.time() + sec
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try: out += os.read(fd, 4096)
            except OSError: pass
    return out

def paste(lines, wait=8):
    os.write(fd, b"\x05"); time.sleep(0.3); drain(0.3)
    for l in lines:
        os.write(fd, l.encode() + b"\r\n"); time.sleep(0.006)
    os.write(fd, b"\x04")
    return drain(wait).decode("utf-8", "replace").replace("\r", "")

os.write(fd, b"\x03"); time.sleep(0.3); drain(0.5)

prog  = ['import storage, binascii',
         'storage.remount("/", readonly=False)',
         'f = open("/code.py", "wb")']
prog += ['f.write(binascii.a2b_base64("%s"))' % c for c in chunks]
prog += ['f.close()',
         'storage.remount("/", readonly=True)',
         'import os',
         'print("WROTE", os.stat("/code.py")[6], "bytes")']

out = paste(prog, wait=20)
tail = [l for l in out.split("\n") if "WROTE" in l or "Error" in l or "Traceback" in l]
print("\n".join(tail) if tail else out[-600:])

if any("WROTE" in l for l in tail):
    print("Deployed. Soft-rebooting...")
    os.write(fd, b"\x04")
    time.sleep(1)
    print("\nNow attach and follow the prompts:\n    screen %s 115200\n(ctrl-a then k to quit)" % PORT)
else:
    print("Write did not confirm - board left untouched.")
os.close(fd)
