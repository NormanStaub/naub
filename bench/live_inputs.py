#!/usr/bin/env python3
"""naub V2 - live graphical monitor for all 17 inputs.

    python3 live_inputs.py            # full-screen live view
    python3 live_inputs.py --raw 5    # 5s of raw frames (no TTY needed)

Pushes a reporter to the board over the REPL (no CIRCUITPY drive required) and
renders every encoder and switch live. Ticks each input off as you exercise it,
so you can see when all 17 are covered.  Ctrl-C to quit.
"""
import os, sys, termios, select, time, glob, shutil, re

ENCODERS = [("ENC1", 0, 1), ("ENC2", 3, 4), ("ENC3", 14, 15), ("ENC4", 17, 18)]
SWITCHES = [("ENC1_SW", 2), ("ENC2_SW", 5), ("ENC3_SW", 23), ("ENC4_SW", 19),
            ("BTN1", 6), ("BTN2", 7), ("BTN3", 20), ("BTN4", 21), ("BTN5", 22)]

REPORTER = '''
import rotaryio, digitalio, time, gc
try:
    for _e in enc: _e.deinit()
except Exception: pass
try:
    for _d in sw: _d.deinit()
except Exception: pass
enc=[]; sw=[]
gc.collect()
from microcontroller import pin as m
E=[(0,1),(3,4),(14,15),(17,18)]
S=[2,5,23,19,6,7,20,21,22]
enc=[rotaryio.IncrementalEncoder(getattr(m,"GPIO%d"%a),getattr(m,"GPIO%d"%b)) for a,b in E]
for n in S:
    d=digitalio.DigitalInOut(getattr(m,"GPIO%d"%n))
    d.direction=digitalio.Direction.INPUT
    d.pull=digitalio.Pull.UP
    sw.append(d)
while True:
    print("D%s|%s"%(",".join(str(e.position) for e in enc),
                    "".join("1" if not d.value else "0" for d in sw)))
    time.sleep(0.02)
'''

C = dict(rst="\033[0m", b="\033[1m", dim="\033[2m", grn="\033[32m", red="\033[31m",
         yel="\033[33m", cyn="\033[36m", mag="\033[35m", gry="\033[90m",
         bgg="\033[42m", bgr="\033[41m")

def open_port():
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    if not ports:
        sys.exit("No board found on /dev/cu.usbmodem*  - is it plugged in?")
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

def push(fd):
    if not wait_prompt(fd):
        print("No REPL prompt - replug the board."); return False
    drain(fd, 0.4)
    os.write(fd, b"\x05"); time.sleep(0.4)
    if b"paste mode" not in drain(fd, 1.0):
        print("Board would not enter paste mode."); return False
    for l in REPORTER.strip().split("\n"):
        os.write(fd, l.encode() + b"\r\n"); time.sleep(0.006)
    os.write(fd, b"\x04")
    frame = re.compile(rb"D-?\d+(?:,-?\d+){3}\|[01]{9}")
    t0 = time.time(); buf = b""
    while time.time() - t0 < 15:
        buf += drain(fd, 0.3)
        if len(frame.findall(buf)) >= 3:
            return True
        if b"Traceback" in buf:
            print(buf.decode("utf-8", "replace")[-600:]); return False
        buf = buf[-4000:]
    print("No valid frames in 15s."); return False

def bar(delta, width=13):
    mid = width // 2
    cells = [C["gry"] + "-" + C["rst"]] * width
    cells[mid] = C["dim"] + "|" + C["rst"]
    if delta:
        n = max(-mid, min(mid, delta))
        for i in range(min(mid, mid + n), max(mid, mid + n) + 1):
            cells[i] = (C["cyn"] if n > 0 else C["mag"]) + "=" + C["rst"]
    return "".join(cells)

def main():
    raw = None
    if "--raw" in sys.argv:
        raw = float(sys.argv[sys.argv.index("--raw") + 1])
    fd, portname = open_port()
    print("port %s - pushing reporter..." % portname)
    if not push(fd):
        os.close(fd); sys.exit("Reporter did not start. Try replugging the board.")
    print("reporter live.")

    pos = [0] * 4; last = [0] * 4; delta = [0] * 4
    cw = [0] * 4; ccw = [0] * 4; decay = [0] * 4
    swstate = [0] * 9; presses = [0] * 9; prev_sw = [0] * 9
    buf = b""; t_end = time.time() + raw if raw else None
    frames = 0; t0 = time.time()

    if not raw:
        sys.stdout.write("\033[?25l\033[2J")
    try:
        while True:
            if t_end and time.time() > t_end: break
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try: buf += os.read(fd, 8192)
                except OSError: pass
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                s = line.decode("utf-8", "replace").strip()
                if not s.startswith("D") or "|" not in s: continue
                frames += 1
                try:
                    pstr, sstr = s[1:].split("|")
                    newpos = [int(x) for x in pstr.split(",")]
                    if len(newpos) != 4 or len(sstr) < 9: continue
                except Exception: continue
                for i in range(4):
                    d = newpos[i] - last[i]
                    if d:
                        delta[i] = d; decay[i] = 25
                        if d > 0: cw[i] += d
                        else: ccw[i] += -d
                    last[i] = newpos[i]; pos[i] = newpos[i]
                for i in range(9):
                    v = 1 if sstr[i] == "1" else 0
                    if v and not prev_sw[i]: presses[i] += 1
                    prev_sw[i] = v; swstate[i] = v
                if raw and frames <= 6:
                    print("  frame:", s)

            if raw: continue
            for i in range(4):
                if decay[i] > 0:
                    decay[i] -= 1
                    if decay[i] == 0: delta[i] = 0

            w = shutil.get_terminal_size((90, 30)).columns
            done_e = sum(1 for i in range(4) if cw[i] and ccw[i])
            done_s = sum(1 for i in range(9) if presses[i])
            out = ["\033[H"]
            out.append("%s%s naub V2 - live input monitor %s%s  %s%d fps  ctrl-c to quit%s\033[K" %
                       (C["b"], C["cyn"], C["rst"], "", C["gry"], frames / max(time.time()-t0, 1), C["rst"]))
            out.append("\033[K")
            out.append("%s ENCODERS%s   (needs BOTH directions to tick)\033[K" % (C["b"], C["rst"]))
            out.append("%s   name  pins        movement        pos     CW    CCW   ok%s\033[K" % (C["gry"], C["rst"]))
            for i, (n, a, b) in enumerate(ENCODERS):
                ok = C["grn"] + "OK" + C["rst"] if (cw[i] and ccw[i]) else C["gry"] + " -" + C["rst"]
                arrow = (C["cyn"] + "CW " if delta[i] > 0 else C["mag"] + "CCW") + C["rst"] if delta[i] else "   "
                out.append("   %-5s %-10s %s %s %s%+5d%s %6d %6d   %s\033[K" %
                           (n, "GP%d/GP%d" % (a, b), bar(delta[i]), arrow,
                            C["b"], pos[i], C["rst"], cw[i], ccw[i], ok))
            out.append("\033[K")
            out.append("%s SWITCHES%s   (press each once)\033[K" % (C["b"], C["rst"]))
            for row in range(3):
                cells = []
                for col in range(3):
                    i = row * 3 + col
                    if i >= 9: break
                    n, p = SWITCHES[i]
                    if swstate[i]:
                        mark = C["bgg"] + C["b"] + " DOWN " + C["rst"]
                    elif presses[i]:
                        mark = C["grn"] + "  ok  " + C["rst"]
                    else:
                        mark = C["gry"] + "  --  " + C["rst"]
                    cells.append("  %-8s %-5s %s x%-3d" % (n, "GP%d" % p, mark, presses[i]))
                out.append("".join(cells) + "\033[K")
            out.append("\033[K")
            covered = done_e + done_s
            colr = C["grn"] if covered == 13 else C["yel"]
            out.append(" %s%sCOVERAGE  %d/4 encoders both ways   %d/9 switches   -> %d/13%s\033[K"
                       % (C["b"], colr, done_e, done_s, covered, C["rst"]))
            if covered == 13:
                out.append(" %s%s ALL 17 INPUTS EXERCISED - every encoder both directions, every switch %s\033[K"
                           % (C["b"], C["grn"], C["rst"]))
            else:
                out.append(" %sstill untouched: %s%s\033[K" % (C["gry"],
                    ", ".join([ENCODERS[i][0] for i in range(4) if not (cw[i] and ccw[i])] +
                              [SWITCHES[i][0] for i in range(9) if not presses[i]]) or "none", C["rst"]))
            out.append("\033[J")
            sys.stdout.write("\n".join(out)); sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    finally:
        if not raw: sys.stdout.write("\033[?25h\033[%d;1H\n" % 30)
        os.write(fd, b"\x03"); os.close(fd)
        print("stopped. frames received: %d" % frames)

main()
