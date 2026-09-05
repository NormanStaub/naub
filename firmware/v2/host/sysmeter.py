"""The system output's real spectrum, by WASAPI loopback capture.

    sm = sysmeter.SysMeter(nband=21)
    sm.start()
    sm.bands()          # -> [0..1] per band, log spaced 20 Hz - 20 kHz
    sm.peak()           # -> 0..1 overall

The mixer's per-app meters invent their band shape, because Windows exposes no
per-session spectrum (PROTOCOL.md §11).  The system output is different: it can
be *captured*, so the EQ screen's meter is a real analysis rather than a
plausible one.

**Why not the endpoint's own peak meter.** `IAudioMeterInformation` on the
IMMDevice looked like the easy answer, and it is not: measured on this machine,
dropping the system fader from 1.00 to 0.30 moved it by ×0.91 — i.e. it reports
the stream *before* the volume, exactly like the per-session meters do, and it
gives one number rather than a spectrum. Loopback captures what is actually
being rendered.

Optional by design: pyaudiowpatch and numpy are only imported when a meter is
started, and `available` says whether they were there. Without them the bridge
simply sends no spectrum and the EQ screen shows a flat floor.
"""
import math
import threading

FMIN, FMAX = 20.0, 20000.0
FFT = 2048                 # ~43 ms at 48 kHz: enough for a 20 Hz bin to exist
FLOOR_DB = -66.0           # maps to 0 on the display
TOP_DB = -6.0              # ... and this to full height

try:
    import numpy as np
    import pyaudiowpatch as pyaudio
    available = True
except ImportError:                       # pragma: no cover
    np = None
    pyaudio = None
    available = False


class SysMeter:
    def __init__(self, nband=21, fft=FFT):
        self.nband = nband
        self.fft = fft
        self.rate = 48000
        self.name = None
        self._buf = None
        self._lock = threading.Lock()
        self._run = False
        self._thread = None
        self._win = None
        self._edges = None
        self._err = None

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        if not available:
            self._err = 'pyaudiowpatch/numpy not installed'
            return False
        try:
            pa = pyaudio.PyAudio()
            try:
                dev = pa.get_default_wasapi_loopback()
            finally:
                pa.terminate()
        except Exception as e:
            self._err = 'no loopback device: %s' % e
            return False
        self.rate = int(dev['defaultSampleRate'])
        self.name = dev['name']
        self._win = np.hanning(self.fft)
        self._edges = self._band_edges()
        self._buf = np.zeros(self.fft, dtype=np.float32)
        self._run = True
        self._thread = threading.Thread(target=self._loop, args=(dev,),
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self):
        self._run = False

    @property
    def error(self):
        return self._err

    # ---- capture ---------------------------------------------------------
    def _loop(self, dev):
        """Capture forever into a rolling buffer.  Runs in its own thread so a
        stalled audio device can never hold up the serial loop."""
        pa = pyaudio.PyAudio()
        st = None
        try:
            ch = min(2, int(dev['maxInputChannels']))
            st = pa.open(format=pyaudio.paInt16, channels=ch,
                         rate=self.rate, input=True,
                         input_device_index=dev['index'],
                         frames_per_buffer=512)
            while self._run:
                raw = st.read(512, exception_on_overflow=False)
                x = np.frombuffer(raw, dtype='<i2').astype(np.float32)
                if ch == 2:
                    x = x.reshape(-1, 2).mean(axis=1)
                x /= 32768.0
                with self._lock:
                    n = len(x)
                    if n >= self.fft:
                        self._buf[:] = x[-self.fft:]
                    else:
                        self._buf[:-n] = self._buf[n:]
                        self._buf[-n:] = x
        except Exception as e:                # pragma: no cover
            self._err = '%s' % e
        finally:
            try:
                if st is not None:
                    st.close()
            finally:
                pa.terminate()

    # ---- analysis --------------------------------------------------------
    def _band_edges(self):
        """Log-spaced band edges as FFT bin indices."""
        k = math.log(FMAX / FMIN) / self.nband
        hz_per_bin = self.rate / float(self.fft)
        edges = []
        for i in range(self.nband + 1):
            f = FMIN * math.exp(k * i)
            edges.append(int(f / hz_per_bin))
        # every band must own at least one bin, or the bottom ones read zero
        for i in range(1, len(edges)):
            if edges[i] <= edges[i - 1]:
                edges[i] = edges[i - 1] + 1
        return edges

    def bands(self):
        """-> list of nband values 0..1, low to high."""
        if self._buf is None:
            return [0.0] * self.nband
        with self._lock:
            x = self._buf.copy()
        sp = np.abs(np.fft.rfft(x * self._win)) * (2.0 / self.fft)
        out = []
        e = self._edges
        top = len(sp) - 1
        for i in range(self.nband):
            a = min(e[i], top)
            b = min(max(e[i + 1], a + 1), top + 1)
            v = float(sp[a:b].max()) if b > a else 0.0
            db = 20.0 * math.log10(v) if v > 1e-9 else -120.0
            out.append(min(1.0, max(0.0, (db - FLOOR_DB) / (TOP_DB - FLOOR_DB))))
        return out

    def peak(self):
        if self._buf is None:
            return 0.0
        with self._lock:
            v = float(np.abs(self._buf).max())
        return min(1.0, v)


if __name__ == '__main__':
    import sys
    import time
    sm = SysMeter(nband=21)
    if not sm.start():
        sys.exit('cannot start: %s' % sm.error)
    print('capturing %s at %d Hz -- play something' % (sm.name, sm.rate))
    try:
        for _ in range(60):
            b = sm.bands()
            bar = ''.join('#' if v > 0.66 else ('+' if v > 0.33 else
                                                ('.' if v > 0.05 else ' '))
                          for v in b)
            print('  peak %.3f |%s|' % (sm.peak(), bar))
            time.sleep(0.15)
    except KeyboardInterrupt:
        pass
    sm.stop()
