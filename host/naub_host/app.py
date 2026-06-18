"""naub host orchestrator: bridge device events <-> Windows audio + display."""
import time

import serial

from . import protocol
from .audio_windows import WindowsAudio
from .icons_windows import icon_for
from .serial_link import SerialLink, autodetect

LEVEL_PUSH_THRESHOLD = 2  # don't repaint the panel for sub-2% external drift


def _log(msg):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), msg))


def _pretty(proc_name):
    base = proc_name[:-4] if proc_name.lower().endswith(".exe") else proc_name
    return base[:1].upper() + base[1:]


class ChannelState:
    def __init__(self, match, cfg_label):
        self.match = match
        self.cfg_label = cfg_label
        self.target = None
        self.key = None          # resolved app key, or None when absent
        self.level = None        # last level (0-100) we believe is in sync
        self.mute = None


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.audio = WindowsAudio()
        self.ch = {ch: ChannelState(c["match"], c.get("label", ""))
                   for ch, c in cfg.channels.items()}
        self.link = None

    # ---- connection ----------------------------------------------------------

    def connect(self):
        port = self.cfg.port
        if not port or port == "auto":
            _log("probing for naub device...")
            port = autodetect()
            if not port:
                raise ConnectionError("no naub device found (is it plugged in "
                                      "and running the CDC firmware?)")
        _log("opening %s" % port)
        self.link = SerialLink(port)
        self.link.write_line(protocol.hello())

    # ---- main loop -----------------------------------------------------------

    def run_forever(self):
        while True:
            try:
                self.connect()
                self._session_loop()
            except (ConnectionError, serial.SerialException, OSError) as e:
                _log("link error: %s -- retrying in 3s" % e)
                if self.link:
                    self.link.close()
                    self.link = None
                self._reset_channels()
                time.sleep(3)

    def _reset_channels(self):
        for st in self.ch.values():
            st.target = None
            st.key = st.level = st.mute = None

    def _session_loop(self):
        last_reconcile = 0.0
        while True:
            for line in self.link.read_lines():
                self._handle_event(line)
            now = time.time()
            if now - last_reconcile >= self.cfg.refresh_seconds:
                self._reconcile()
                last_reconcile = now
            time.sleep(0.02)

    # ---- device -> us --------------------------------------------------------

    def _handle_event(self, line):
        verb, args = protocol.parse(line)
        if verb is None:
            return
        if verb == "naub":
            _log("connected: %s" % line)
        elif verb == "vol" and len(args) >= 2:
            self._apply_volume(int(args[0]), int(args[1]))
        elif verb == "mute" and len(args) >= 2:
            self._apply_mute(int(args[0]), args[1] == "1")
        elif verb == "btn" and len(args) >= 2:
            self._apply_button(int(args[0]), args[1] == "1")
        elif verb == "pong":
            pass

    def _apply_volume(self, ch, value):
        st = self.ch.get(ch)
        if st is None:
            return
        st.level = value
        if not self._with_target(st, lambda t: t.set_volume(value / 100.0)):
            _log("ch%d vol %d (no target)" % (ch, value))

    def _apply_mute(self, ch, muted):
        st = self.ch.get(ch)
        if st is None:
            return
        st.mute = muted
        self._with_target(st, lambda t: t.set_mute(muted))
        _log("ch%d mute=%d" % (ch, muted))

    def _apply_button(self, n, state):
        # V1 default: Button 1 toggles system (master) mute.
        try:
            sys_t = self.audio.resolve(["__system__"])
            if sys_t:
                sys_t.set_mute(state)
        except Exception as e:
            _log("btn %d apply failed: %s" % (n, e))
        _log("btn %d -> system mute=%d" % (n, state))

    def _with_target(self, st, fn):
        """Run fn(target); re-resolve once if the cached session went stale."""
        if st.target is not None:
            try:
                fn(st.target)
                return True
            except Exception:
                pass
        self.audio.snapshot()
        st.target = self.audio.resolve(st.match)
        if st.target is not None:
            try:
                fn(st.target)
                return True
            except Exception:
                pass
        return False

    # ---- us -> device (reconcile) -------------------------------------------

    def _reconcile(self):
        try:
            self.audio.snapshot()
        except Exception as e:
            _log("audio snapshot failed: %s" % e)
            return
        for ch, st in self.ch.items():
            target = self.audio.resolve(st.match)
            if target is None:
                if st.key is not None:
                    _log("ch%d: '%s' gone" % (ch, st.key))
                    self.link.write_line(protocol.iconclear(ch))
                    self.link.write_line(protocol.label(ch, "--"))
                    st.target = None
                    st.key = st.level = st.mute = None
                continue
            if target.key != st.key:
                self._assign(ch, st, target)
            else:
                st.target = target
                self._sync_external(ch, st, target)

    def _assign(self, ch, st, target):
        label = st.cfg_label or _pretty(target.proc_name)
        st.target = target
        st.key = target.key
        _log("ch%d <- %s (%s)" % (ch, target.proc_name, label))
        self.link.write_line(protocol.label(ch, label))
        try:
            w, h, packed = icon_for(target.exe_path, label)
            self.link.write_line(protocol.icon(ch, w, h, packed))
        except Exception as e:
            _log("ch%d icon failed: %s" % (ch, e))
            self.link.write_line(protocol.iconclear(ch))
        try:
            st.level = int(round(target.get_volume() * 100))
            st.mute = target.get_mute()
            self.link.write_line(protocol.level(ch, st.level))
            self.link.write_line(protocol.mute(ch, st.mute))
        except Exception:
            pass

    def _sync_external(self, ch, st, target):
        """Push device updates only when the app/system changed under us."""
        try:
            lvl = int(round(target.get_volume() * 100))
            mu = target.get_mute()
        except Exception:
            return
        if st.level is None or abs(lvl - st.level) >= LEVEL_PUSH_THRESHOLD:
            self.link.write_line(protocol.level(ch, lvl))
            st.level = lvl
        if mu != st.mute:
            self.link.write_line(protocol.mute(ch, mu))
            st.mute = mu
