"""Per-app audio levels, volume and mute from WASAPI — the data behind the
mixer screen's dot trace.

    src = appmeter.AppMeters()
    names = src.top(3)                  # the three most interesting apps
    src.levels(names)                   # -> [0.0..1.0] peak, right now
    src.volume('spotify'), src.set_volume('spotify', 0.5)

What Windows will and will not give you, since it shapes the UI:

  * **Level, yes.** Every audio session exposes IAudioMeterInformation, whose
    GetPeakValue() is the largest sample since the last call — a real amplitude
    trace of that app alone.  Costs ~0.05 ms per session, so polling at 100 Hz
    is free.  pycaw does not wrap the interface, so it is declared below.
  * **But the peak is the RAW STREAM.**  Measured on this box: the value does
    not move when the session volume changes, nor when the endpoint volume
    does — it is what the app rendered, before anybody's fader.  So a meter
    built on it alone would sit still while you turn the knob, which is not
    what a mixer meter means.  levels() therefore scales by the session's own
    volume (and zeroes it when muted); the SYSTEM volume is deliberately left
    out, since it is not this app getting quieter.
  * **Spectrum, no.** There is no per-session tap.  WASAPI loopback captures
    the endpoint *mix*, not one app, so a per-app FFT would need code injected
    into every process.  Hence a scrolling amplitude trace rather than a
    frequency analyzer.
  * **One program is NOT one session.**  Discord runs six processes and holds
    a session from more than one of them: measured in a voice call, pid 5724
    was ACTIVE with a peak of 1.0 while pid 14168 sat idle at 0.0.  This module
    used to key a dict by program name and *assign* into it, so it kept
    whichever the enumeration returned last -- measured, six times out of six,
    the idle one.  The result is precisely the bug that was reported: Discord
    visible (its UI-sound session blips Active when you join), no level, and a
    volume knob driving a fader nobody can hear.  Sessions are therefore
    GROUPED per program: the level is the loudest of them, and a volume or mute
    goes to ALL of them, because which one is audible changes from call to
    call.
  * **Nor is one endpoint all of them.**  Apps can be pointed at any output
    device -- Discord has its own output setting, and this machine has fifteen
    active render endpoints including two virtual cables -- so enumerating only
    the default device would lose them.  All of them are enumerated, which is
    also the CHEAPER option: pycaw's GetAllSessions() rebuilds its COM plumbing
    on every call and measured 15 ms for the single default device, while
    holding the session managers and sweeping all fifteen measured 3 ms.

Peak is an amplitude (0..1 linear), which looks dull on a small display because
speech and music sit low in the range; `levels()` applies a gentle curve so the
trace uses the full height.  Set curve=1.0 for raw amplitude.

The curve is applied to the PEAK and the volume multiplies the result, not the
other way round: the curve exists to lift quiet program material into view, and
running the fader through it too would compress the knob (half volume would
read as 0.73 of full height instead of half).  Peak first, then fader, so the
knob moves the meter linearly and predictably.

TODO: this duplicates part of host/naub_host/audio_windows.py (the V1 host).
Fold the meter interface back into that module when V2 gets its own daemon.
"""
import os
import time
from ctypes import POINTER, c_float, c_uint32
from ctypes.wintypes import DWORD

import comtypes
import psutil
from comtypes import CLSCTX_ALL, COMMETHOD, COMError, GUID, HRESULT, IUnknown
from pycaw.constants import CLSID_MMDeviceEnumerator
from pycaw.pycaw import (AudioUtilities, IAudioSessionControl2,
                         IAudioSessionManager2, IMMDeviceEnumerator,
                         ISimpleAudioVolume)

ERENDER = 0               # EDataFlow: render (output) endpoints
DEVICE_ACTIVE = 1         # DEVICE_STATE_ACTIVE
ENDPOINT_REBUILD_S = 15.0  # how often to look for new output devices


class IAudioMeterInformation(IUnknown):
    """endpointvolume.h — not wrapped by pycaw, so declared here."""
    _iid_ = GUID('{C02216F6-8C67-4B5B-9D00-D008E73E0064}')
    _methods_ = (
        COMMETHOD([], HRESULT, 'GetPeakValue',
                  (['out'], POINTER(c_float), 'pfPeak')),
        COMMETHOD([], HRESULT, 'GetMeteringChannelCount',
                  (['out'], POINTER(c_uint32), 'pnChannelCount')),
        COMMETHOD([], HRESULT, 'GetChannelsPeakValues',
                  (['in'], c_uint32, 'u32ChannelCount'),
                  (['out'], POINTER(c_float), 'afPeakValues')),
        COMMETHOD([], HRESULT, 'QueryHardwareSupport',
                  (['out'], POINTER(DWORD), 'pdwHardwareSupportMask')),
    )


_OWN_PID = os.getpid()


class Endpoints:
    """A session manager per active render endpoint, held open.

    Held, because building them is the expensive part: activating all fifteen
    on this machine measured 5 ms once, after which re-enumerating every
    session on every one of them costs 3 ms -- a fifth of what pycaw's
    single-device GetAllSessions() costs, since that rebuilds the plumbing
    every time.  Rebuilt occasionally so a newly plugged-in device shows up."""

    def __init__(self):
        self.mgrs = []
        self.t_built = 0.0
        self.rebuild()

    def rebuild(self):
        mgrs = []
        try:
            enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator,
                                             IMMDeviceEnumerator,
                                             comtypes.CLSCTX_INPROC_SERVER)
            coll = enum.EnumAudioEndpoints(ERENDER, DEVICE_ACTIVE)
            for i in range(coll.GetCount()):
                try:
                    p = coll.Item(i).Activate(IAudioSessionManager2._iid_,
                                              CLSCTX_ALL, None)
                    # QueryInterface, NOT ctypes.cast.  cast() reinterprets the
                    # pointer without taking a reference, so when the IUnknown
                    # it came from is collected, COM releases the object and
                    # every cached manager becomes a dangling pointer -- which
                    # crashed the bridge with an access violation minutes
                    # later, nowhere near the cause.
                    mgrs.append(p.QueryInterface(IAudioSessionManager2))
                except Exception:
                    pass                      # one dud device is not fatal
        except Exception:
            return False
        self.mgrs = mgrs
        self.t_built = time.monotonic()
        return True

    def controls(self):
        """Every session control on every output device."""
        if time.monotonic() - self.t_built > ENDPOINT_REBUILD_S:
            self.rebuild()
        out = []
        for m in self.mgrs:
            try:
                se = m.GetSessionEnumerator()
            except Exception:
                continue
            for i in range(se.GetCount()):
                try:
                    out.append(se.GetSession(i)
                               .QueryInterface(IAudioSessionControl2))
                except Exception:
                    pass
        return out


class Session:
    """One WASAPI session.  A program may have several; see AppMeters."""

    def __init__(self, ctl, pid, name):
        self._c = ctl
        self.meter = ctl.QueryInterface(IAudioMeterInformation)
        self.vol = ctl.QueryInterface(ISimpleAudioVolume)
        self.pid = pid
        self.name = name
        self.key = name.lower()
        if self.key.endswith('.exe'):
            self.key = self.key[:-4]

    @property
    def active(self):
        try:
            return self._c.GetState() == 1      # 0 inactive 1 active 2 expired
        except Exception:
            return False

    def peak(self):
        try:
            return float(self.meter.GetPeakValue())
        except COMError:
            return 0.0

    def gain(self):
        """This session's fader, 0 when muted.  Cheap enough to read every
        frame, which is what makes the meter answer the knob immediately
        rather than a second later when the state poll comes round."""
        try:
            if self.vol.GetMute():
                return 0.0
            return float(self.vol.GetMasterVolume())
        except COMError:
            return 1.0


class AppMeters:
    def __init__(self, curve=0.45):
        self.curve = curve
        self._fail = None
        self._by_key = {}        # key -> [Session, ...], one program's lot
        self._seen = []          # keys that have ever rendered audio, in order
        self._pname = {}         # pid -> process name, so psutil is asked once
        self._ends = Endpoints()
        self.refresh()

    def _name_of(self, pid):
        n = self._pname.get(pid)
        if n is None:
            try:
                n = psutil.Process(pid).name()
            except Exception:
                n = 'pid%d' % pid
            if len(self._pname) > 256:         # a long uptime is not a leak
                self._pname.clear()
            self._pname[pid] = n
        return n

    def refresh(self):
        """Re-enumerate sessions.  Cheap enough for ~1 Hz; peaks are polled
        from the cached objects and do not need this.

        -> True if the list was rebuilt.  Returns False rather than raising
        when WASAPI is mid-change: the default endpoint being torn down and
        recreated (headphones plugged in, a Bluetooth speaker connecting, the
        Equalizer APO configurator doing its work) makes every call on the old
        device fail with COMError 'Access is denied' for a moment.  That is a
        normal event, not a fault, so keep the previous session map and try
        again next time."""
        try:
            ctls = self._ends.controls()
        except (COMError, OSError) as e:
            self._fail = '%s' % (e,)
            return False
        self._fail = None
        by_key = {}
        for c in ctls:
            try:
                if c.IsSystemSoundsSession() == 0:        # S_OK == is that one
                    continue
                pid = c.GetProcessId()
            except Exception:
                continue
            if not pid:
                continue
            # Skip OUR OWN process.  sysmeter's loopback capture opens the
            # render endpoint, which registers the host as an audio session --
            # so naub showed up in its own mixer as "python", metering the
            # system output it was capturing.  A tool should not appear among
            # the things it controls.
            if pid == _OWN_PID:
                continue
            try:
                sess = Session(c, pid, self._name_of(pid))
            except Exception:
                continue
            # APPEND, never assign.  One program can hold several sessions and
            # only one of them may be the one you can hear; keeping the last
            # one enumerated is how Discord ended up in the list with no level
            # and a dead volume knob.
            g = by_key.get(sess.key)
            if g is None:
                by_key[sess.key] = [sess]
            else:
                g.append(sess)
            if sess.active and sess.key not in self._seen:
                self._seen.append(sess.key)
        for g in by_key.values():
            g.sort(key=lambda x: x.pid)        # stable pick for reads
        self._by_key = by_key
        return True

    def _rep(self, key):
        """The session to READ a program's volume from: one that is actually
        playing if there is one, since a silent sibling's fader tells you
        nothing about what you are hearing."""
        g = self._by_key.get(key)
        if not g:
            return None
        for s in g:
            if s.active:
                return s
        return g[0]

    def active(self):
        """Every app with a live audio session right now, longest-established
        first.  This is the app switcher's list, and its counter is len() of
        it -- so it must NOT be padded or truncated."""
        live = [k for k, g in self._by_key.items()
                if any(s.active for s in g)]
        out = [k for k in self._seen if k in live]
        out += [k for k in live if k not in out]
        return out

    def top(self, n):
        """Exactly n apps worth showing: currently rendering audio first, then
        ones that have played before (so a paused player keeps its column), then
        anything else holding a session.  Padded with '' — an empty column is a
        legitimate state, not an error."""
        out = self.active()
        out += [k for k in self._seen if k not in out and k in self._by_key]
        out += [k for k in self._by_key if k not in out]
        out = out[:n]
        return out + [''] * (n - len(out))

    def levels(self, keys):
        """What each app actually SOUNDS like right now: its stream peak,
        curved, then scaled by its own fader.  Muted reads zero.

        The LOUDEST of a program's sessions wins -- each scaled by its own
        fader first, since they can differ.  A program with a silent session
        open alongside the one you can hear should meter what you can hear."""
        out = []
        for k in keys:
            g = self._by_key.get(k) if k else None
            best = 0.0
            for s in (g or ()):
                p = s.peak()
                if p <= 0.0:
                    continue
                v = (p ** self.curve) * s.gain()
                if v > best:
                    best = v
            out.append(best)
        return out

    def volume(self, key):
        s = self._rep(key)
        if not s:
            return 0.0
        try:
            return float(s.vol.GetMasterVolume())
        except COMError:
            return 0.0

    def set_volume(self, key, f):
        """To EVERY session the program has open.  Which one is audible can
        change from moment to moment -- Discord opens one per call -- and a
        knob that moves only whichever we happened to read from would work
        sometimes and do nothing the rest of the time."""
        v = max(0.0, min(1.0, f))
        for s in self._by_key.get(key) or ():
            try:
                s.vol.SetMasterVolume(v, None)
            except COMError:
                pass

    def muted(self, key):
        s = self._rep(key)
        try:
            return bool(s.vol.GetMute()) if s else False
        except COMError:
            return False

    def set_mute(self, key, muted):
        for s in self._by_key.get(key) or ():
            try:
                s.vol.SetMute(1 if muted else 0, None)
            except COMError:
                pass

    def sessions(self):
        """Every session, grouped, for diagnostics: naub_bridge --sessions."""
        return dict(self._by_key)

    def system_volume(self):
        try:
            return float(AudioUtilities.GetSpeakers()
                         .EndpointVolume.GetMasterVolumeLevelScalar())
        except Exception:
            return 0.0

    def system_muted(self):
        try:
            return bool(AudioUtilities.GetSpeakers().EndpointVolume.GetMute())
        except Exception:
            return False

    def set_system_mute(self, muted):
        try:
            AudioUtilities.GetSpeakers().EndpointVolume.SetMute(
                1 if muted else 0, None)
        except Exception:
            pass

    def set_system_volume(self, f):
        try:
            AudioUtilities.GetSpeakers().EndpointVolume \
                .SetMasterVolumeLevelScalar(max(0.0, min(1.0, f)), None)
        except Exception:
            pass


if __name__ == '__main__':
    import time
    src = AppMeters()
    keys = src.top(3)
    print('columns: %r' % (keys,))
    print('system volume %.2f' % src.system_volume())
    for k in keys:
        print('  %-16s vol=%.2f muted=%s' % (k, src.volume(k), src.muted(k)))
    print('\nlevels (play something):')
    for _ in range(40):
        lv = src.levels(keys)
        print('  ' + '  '.join('%-12s %s' % (k, '#' * int(v * 20))
                               for k, v in zip(keys, lv)))
        time.sleep(0.1)
