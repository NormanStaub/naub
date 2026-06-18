"""Windows per-app + system audio via WASAPI (pycaw).

Exposes a uniform `Target` (an app session or the system endpoint) with
get/set volume (0.0-1.0) and mute. `WindowsAudio.snapshot()` re-enumerates
sessions; `resolve(match)` picks the first live target for a channel's match
list. Keep all pycaw/COM specifics in this file (the OS adapter boundary).
"""
from pycaw.pycaw import AudioUtilities, ISimpleAudioVolume

SYSTEM_MATCH = "__system__"


class AppTarget:
    def __init__(self, session):
        self._session = session
        self._vol = session._ctl.QueryInterface(ISimpleAudioVolume)
        try:
            self.proc_name = session.Process.name()
        except Exception:
            self.proc_name = "?"
        try:
            self.exe_path = session.Process.exe()
        except Exception:
            self.exe_path = None

    @property
    def key(self):
        return self.proc_name.lower()

    def get_volume(self):
        return float(self._vol.GetMasterVolume())

    def set_volume(self, f):
        self._vol.SetMasterVolume(max(0.0, min(1.0, f)), None)

    def get_mute(self):
        return bool(self._vol.GetMute())

    def set_mute(self, muted):
        self._vol.SetMute(1 if muted else 0, None)


class SystemTarget:
    proc_name = SYSTEM_MATCH
    exe_path = None
    key = SYSTEM_MATCH

    def __init__(self):
        # pycaw's AudioDevice exposes the endpoint volume interface directly.
        self._ep = AudioUtilities.GetSpeakers().EndpointVolume

    def get_volume(self):
        return float(self._ep.GetMasterVolumeLevelScalar())

    def set_volume(self, f):
        self._ep.SetMasterVolumeLevelScalar(max(0.0, min(1.0, f)), None)

    def get_mute(self):
        return bool(self._ep.GetMute())

    def set_mute(self, muted):
        self._ep.SetMute(1 if muted else 0, None)


class WindowsAudio:
    def __init__(self):
        self._sessions = []  # list of (name_lower, session)

    def snapshot(self):
        sessions = []
        for s in AudioUtilities.GetAllSessions():
            if s.Process is None:
                continue
            try:
                name = s.Process.name()
            except Exception:
                continue
            sessions.append((name.lower(), s))
        self._sessions = sessions

    def resolve(self, match):
        """Return a Target for this channel's match list, or None if absent."""
        match = [m.lower() for m in match]
        if SYSTEM_MATCH in match:
            try:
                return SystemTarget()
            except Exception:
                return None
        for name, sess in self._sessions:
            if name in match:
                try:
                    return AppTarget(sess)
                except Exception:
                    continue
        return None
