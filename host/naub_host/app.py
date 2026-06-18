"""naub host orchestrator: bridge device events <-> Windows audio + display."""
import time

import serial

from . import protocol
from .audio_windows import SYSTEM_MATCH, WindowsAudio
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
        # System-dedicated channels stay pinned to the endpoint and are not
        # part of the hold-and-turn source cycle.
        self.is_system = SYSTEM_MATCH in [m.lower() for m in match]
        self.target = None
        self.key = None          # bound source key (proc name / __system__), or None
        self.level = None        # last level (0-100) we believe is in sync
        self.mute = None


class App:
    def __init__(self, cfg):
        self.cfg = cfg
        self.audio = WindowsAudio()
        self.ch = {ch: ChannelState(c["match"], c.get("label", ""))
                   for ch, c in cfg.channels.items()}
        self.link = None
        self._last_input = 0.0  # last time a device input event arrived

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
            # Defer the (relatively slow) session reconcile while the user is
            # actively turning a knob, so it never hitches live volume changes.
            idle = (now - self._last_input) > 0.2
            if idle and now - last_reconcile >= self.cfg.refresh_seconds:
                self._reconcile()
                last_reconcile = now
            time.sleep(0.005)

    # ---- device -> us --------------------------------------------------------

    def _handle_event(self, line):
        verb, args = protocol.parse(line)
        if verb is None:
            return
        if verb == "naub":
            _log("connected: %s" % line)
            return
        self._last_input = time.time()  # any device event = active use
        if verb == "vol" and len(args) >= 2:
            self._apply_volume(int(args[0]), int(args[1]))
        elif verb == "mute" and len(args) >= 2:
            self._apply_mute(int(args[0]), args[1] == "1")
        elif verb == "btn" and len(args) >= 2:
            self._apply_button(int(args[0]), args[1] == "1")
        elif verb == "cycle" and len(args) >= 2:
            self._cycle(int(args[0]), 1 if int(args[1]) >= 0 else -1)
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
        # Prefer the currently-bound source (which may be a cycled-to app, not the
        # config default); fall back to the config match only when unbound.
        st.target = self.audio.resolve([st.key] if st.key else st.match)
        if st.target is not None:
            try:
                fn(st.target)
                return True
            except Exception:
                pass
        return False

    # ---- hold-and-turn: cycle a channel's source ----------------------------

    def _active_app_keys(self):
        """Distinct process names with a live audio session, in stable order."""
        keys = []
        for name, _sess in self.audio._sessions:
            if name not in keys:
                keys.append(name)
        return keys

    def _cycle(self, ch, direction):
        st = self.ch.get(ch)
        if st is None or st.is_system:
            return  # system channel is pinned, not cyclable
        try:
            self.audio.snapshot()
        except Exception as e:
            _log("ch%d cycle: snapshot failed: %s" % (ch, e))
            return
        # Sources bound to OTHER (non-system) channels are off-limits (unique per knob).
        taken = {ost.key for oc, ost in self.ch.items()
                 if oc != ch and not ost.is_system and ost.key}
        candidates = [k for k in self._active_app_keys() if k not in taken]
        if not candidates:
            _log("ch%d cycle: no free active sources" % ch)
            return
        if st.key in candidates:
            i = (candidates.index(st.key) + direction) % len(candidates)
        else:
            i = 0 if direction >= 0 else len(candidates) - 1
        new_key = candidates[i]
        if new_key == st.key:
            return  # only one option
        target = self.audio.resolve([new_key])
        if target is not None:
            _log("ch%d cycle -> %s" % (ch, target.proc_name))
            self._assign(ch, st, target)

    # ---- us -> device (reconcile) -------------------------------------------

    def _label_for(self, st, target):
        # Keep the configured label only while bound to a configured app;
        # cycled-to apps show their own name.
        if st.cfg_label and target.key in [m.lower() for m in st.match]:
            return st.cfg_label
        return _pretty(target.proc_name)

    def _reconcile(self):
        try:
            self.audio.snapshot()
        except Exception as e:
            _log("audio snapshot failed: %s" % e)
            return
        for ch, st in self.ch.items():
            if st.key is not None:
                # Bound (via config default or a manual cycle): keep it as long as
                # that source is still alive; only fall back to config if it dies.
                target = self.audio.resolve([st.key])
                if target is not None:
                    st.target = target
                    self._sync_external(ch, st, target)
                    continue
                _log("ch%d: '%s' gone" % (ch, st.key))
                st.target = None
                st.key = st.level = st.mute = None
            # Unbound: try the configured default.
            target = self.audio.resolve(st.match)
            if target is not None:
                self._assign(ch, st, target)
            else:
                self._clear(ch, st)

    def _clear(self, ch, st):
        if st.key is None and st.level is None:
            return  # already clear; don't spam the panel
        self.link.write_line(protocol.iconclear(ch))
        self.link.write_line(protocol.label(ch, "--"))
        self.link.write_line(protocol.level(ch, 0))
        st.target = None
        st.key = st.level = st.mute = None

    def _assign(self, ch, st, target):
        label = self._label_for(st, target)
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
