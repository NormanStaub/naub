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
  * **Spectrum, no.** There is no per-session tap.  WASAPI loopback captures
    the endpoint *mix*, not one app, so a per-app FFT would need code injected
    into every process.  Hence a scrolling amplitude trace rather than a
    frequency analyzer.

Peak is an amplitude (0..1 linear), which looks dull on a small display because
speech and music sit low in the range; `levels()` applies a gentle curve so the
trace uses the full height.  Set curve=1.0 for raw amplitude.

TODO: this duplicates part of host/naub_host/audio_windows.py (the V1 host).
Fold the meter interface back into that module when V2 gets its own daemon.
"""
from ctypes import POINTER, c_float, c_uint32
from ctypes.wintypes import DWORD

from comtypes import COMMETHOD, COMError, GUID, HRESULT, IUnknown
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume


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


class Session:
    def __init__(self, session):
        self._s = session
        self.meter = session._ctl.QueryInterface(IAudioMeterInformation)
        self.vol = session._ctl.QueryInterface(ISimpleAudioVolume)
        try:
            self.name = session.Process.name()
        except Exception:
            self.name = '?'
        self.key = self.name.lower()
        if self.key.endswith('.exe'):
            self.key = self.key[:-4]

    @property
    def active(self):
        try:
            return self._s.State == 1          # 0 inactive 1 active 2 expired
        except Exception:
            return False

    def peak(self):
        try:
            return float(self.meter.GetPeakValue())
        except COMError:
            return 0.0


class AppMeters:
    def __init__(self, curve=0.45):
        self.curve = curve
        self._by_key = {}
        self._seen = []          # keys that have ever rendered audio, in order
        self.refresh()

    def refresh(self):
        """Re-enumerate sessions.  Cheap enough for ~1 Hz; peaks are polled
        from the cached objects and do not need this."""
        by_key = {}
        for s in AudioUtilities.GetAllSessions():
            if s.Process is None:
                continue
            try:
                if s._ctl.IsSystemSoundsSession() == 0:    # S_OK == is that one
                    continue
            except Exception:
                pass
            try:
                sess = Session(s)
            except (COMError, Exception):
                continue
            by_key[sess.key] = sess
            if sess.active and sess.key not in self._seen:
                self._seen.append(sess.key)
        self._by_key = by_key

    def active(self):
        """Every app with a live audio session right now, longest-established
        first.  This is the app switcher's list, and its counter is len() of
        it -- so it must NOT be padded or truncated."""
        live = [k for k in self._by_key if self._by_key[k].active]
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
        out = []
        for k in keys:
            s = self._by_key.get(k) if k else None
            p = s.peak() if s else 0.0
            out.append(p ** self.curve if p > 0.0 else 0.0)
        return out

    def volume(self, key):
        s = self._by_key.get(key)
        if not s:
            return 0.0
        try:
            return float(s.vol.GetMasterVolume())
        except COMError:
            return 0.0

    def set_volume(self, key, f):
        s = self._by_key.get(key)
        if s:
            try:
                s.vol.SetMasterVolume(max(0.0, min(1.0, f)), None)
            except COMError:
                pass

    def muted(self, key):
        s = self._by_key.get(key)
        try:
            return bool(s.vol.GetMute()) if s else False
        except COMError:
            return False

    def set_mute(self, key, muted):
        s = self._by_key.get(key)
        if s:
            try:
                s.vol.SetMute(1 if muted else 0, None)
            except COMError:
                pass

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
