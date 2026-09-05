"""N-band parametric EQ, driven into Equalizer APO.

    eq = eqapo.ParametricEq()
    print(eq.status())                     # is there an engine, and is it live?
    eq.set_band(0, kind='PK', f0=1000, db=-3.0, q=1.4, on=True)
    eq.flush()                             # or let tick() debounce it

naub writes no DSP.  Equalizer APO is already a parametric EQ inside the
Windows audio pipeline; this writes it a config file and gets out of the way.
Filter syntax follows its Configuration reference: PK / LSC / HSC take
`Fc <hz> Hz Gain <db> dB Q <q>`.

Three things this is careful about, each learned the hard way:

  * **config.txt belongs to the user.**  Here it holds a 20-filter PEQ export
    and an Include for their own channel routing.  We own exactly one file,
    naub-eq.txt, and add exactly one guarded Include line to theirs.  Nothing
    else is ever rewritten, and remove() takes our line back out.
  * **installed is not enabled.**  Equalizer APO attaches per ENDPOINT, via its
    Configurator.  Measured on this machine: it was attached only to a
    Bluetooth speaker that was not even present, while the default output had
    no APO at all -- so a config write did precisely nothing, and no amount of
    checking `registry -> installed?` would have revealed it.  status() checks
    the *current default endpoint*, which is the question that matters.
  * **writes are debounced.**  A write costs us 0.26 ms, but it makes Equalizer
    APO re-parse the whole config -- the user's filters included -- and rebuild
    its graph.  A knob at one detent per 10 ms would do that 100x a second, so
    set_band() only marks dirty and tick() writes at most every WRITE_MS, with
    flush() guaranteeing the final value lands.
"""
import os
import time

try:
    import winreg
except ImportError:                       # importable off Windows; just inert
    winreg = None

INCLUDE_NAME = 'naub-eq.txt'
INCLUDE_LINE = 'Include: ' + INCLUDE_NAME
MARKER = '# naub parametric EQ (managed by the naub host)'

MAXDB = 12.0                              # knob full scale, +/-
FMIN, FMAX = 20.0, 20000.0
QMIN, QMAX = 0.3, 10.0
NBAND = 5
WRITE_MS = 50.0                           # debounce; see the note above

PK, LSC, HSC = 'PK', 'LSC', 'HSC'
KINDS = (PK, LSC, HSC)

LF = chr(10)
CR = chr(13)
CRLF = CR + LF

_RENDER = (r'SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render')
_CHILD = r'SOFTWARE\EqualizerAPO\Child APOs'
_NAME_KEY = '{b3f8fa53-0004-438e-9003-51a46e139bfc},6'


def _config_path():
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\EqualizerAPO') as k:
            path, _ = winreg.QueryValueEx(k, 'ConfigPath')
        return path if path and os.path.isdir(path) else None
    except OSError:
        return None


def _attached_endpoints():
    """-> {endpoint name: is it currently present} for every endpoint Equalizer
    APO has actually been attached to."""
    out = {}
    if winreg is None:
        return out
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _CHILD) as k:
            i = 0
            while True:
                try:
                    guid = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                name, state = None, None
                try:
                    with winreg.OpenKey(
                            winreg.HKEY_LOCAL_MACHINE,
                            _RENDER + '\\' + guid + '\\Properties') as p:
                        name = winreg.QueryValueEx(p, _NAME_KEY)[0]
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        _RENDER + '\\' + guid) as p:
                        state = winreg.QueryValueEx(p, 'DeviceState')[0]
                except OSError:
                    pass
                if name:
                    out[name] = bool(state and state & 1)   # 1 = ACTIVE
    except OSError:
        pass
    return out


def _default_endpoint():
    """The output device Windows is using right now, by name."""
    try:
        from pycaw.pycaw import AudioUtilities
        return AudioUtilities.GetSpeakers().FriendlyName
    except Exception:
        return None


def value_to_db(v):
    """0..100 knob position -> dB; 50 is flat."""
    v = max(0.0, min(100.0, float(v)))
    return (v - 50.0) / 50.0 * MAXDB


def db_to_value(db):
    return int(round(db / MAXDB * 50.0 + 50.0))


class Band:
    def __init__(self, kind=PK, f0=1000.0, db=0.0, q=1.0, on=False):
        self.kind = kind
        self.f0 = f0
        self.db = db
        self.q = q
        self.on = on

    def line(self):
        return ('Filter: %s %s Fc %.4g Hz Gain %.2f dB Q %.3g'
                % ('ON' if self.on else 'OFF', self.kind, self.f0,
                   self.db, self.q))

    def wire(self):
        """The compact form the protocol carries."""
        return '%s %.4g %.2f %.3g %d' % (self.kind, self.f0, self.db, self.q,
                                         1 if self.on else 0)


class ParametricEq:
    def __init__(self, nband=NBAND, config_path=None):
        self.config_path = config_path or _config_path()
        self.bands = [Band() for _ in range(nband)]
        self._dirty = False
        self._last_write = 0.0
        if self.available:
            self._restore()

    # ---- availability ----------------------------------------------------
    @property
    def available(self):
        return self.config_path is not None

    @property
    def eq_file(self):
        return os.path.join(self.config_path, INCLUDE_NAME)

    @property
    def config_file(self):
        return os.path.join(self.config_path, 'config.txt')

    def status(self):
        """-> (ok, human-readable reason).

        `ok` means a filter written now would actually be heard: Equalizer APO
        installed, AND attached to the endpoint currently in use.  The second
        half is the one that bites -- see the module docstring."""
        if not self.available:
            return False, 'Equalizer APO not installed'
        attached = _attached_endpoints()
        if not attached:
            return False, 'Equalizer APO installed but attached to no device'
        cur = _default_endpoint()
        if cur is None:
            return True, ('attached to %s; current device unknown'
                          % ', '.join(sorted(attached)))
        for name, active in attached.items():
            # The registry holds the DEVICE name ("High Definition Audio
            # Device") while pycaw reports the ENDPOINT name ("Speakers (High
            # Definition Audio Device)"), so one is embedded in the other at a
            # non-zero offset.  Comparing truncated prefixes silently fails on
            # exactly that case -- substring, both ways, full strings.
            a, b = name.strip().lower(), cur.strip().lower()
            if len(a) >= 6 and len(b) >= 6 and (a in b or b in a):
                if active:
                    return True, 'active on %s' % cur
                return False, '%s is attached but not present' % name
        return False, ('not attached to %s (only to %s) -- add it in the '
                       'Equalizer APO Configurator'
                       % (cur, ', '.join(sorted(attached))))

    # ---- setters ---------------------------------------------------------
    def set_band(self, i, kind=None, f0=None, db=None, q=None, on=None):
        b = self.bands[i]
        if kind is not None:
            if kind not in KINDS:
                raise ValueError('unknown filter type %r' % kind)
            b.kind = kind
        if f0 is not None:
            b.f0 = max(FMIN, min(FMAX, float(f0)))
        if db is not None:
            b.db = max(-MAXDB, min(MAXDB, float(db)))
        if q is not None:
            b.q = max(QMIN, min(QMAX, float(q)))
        if on is not None:
            b.on = bool(on)
        self._dirty = True

    def flat(self):
        for b in self.bands:
            b.db = 0.0
            b.on = False
        self._dirty = True

    # ---- writing ---------------------------------------------------------
    def tick(self, now=None):
        """Call from the host loop; writes at most every WRITE_MS."""
        if not self._dirty:
            return False
        now = now if now is not None else time.monotonic()
        if (now - self._last_write) * 1000.0 < WRITE_MS:
            return False
        return self.flush(now)

    def flush(self, now=None):
        """Write immediately.  Always call this when a knob stops, so the file
        cannot be left behind the display."""
        if not self.available:
            self._dirty = False
            return False
        self._write()
        self._ensure_include()
        self._dirty = False
        self._last_write = now if now is not None else time.monotonic()
        return True

    def preamp(self):
        """Enough headroom that boosts cannot clip.  The sum of the positive
        gains is conservative -- the true peak of the summed response is lower
        -- but it is one line and it can never be wrong in the direction that
        distorts."""
        boost = sum(b.db for b in self.bands if b.on and b.db > 0.0)
        return -boost

    def render(self):
        lines = [MARKER,
                 '# %d parametric bands driven by the device; do not hand-edit.'
                 % len(self.bands),
                 'Preamp: %.2f dB' % self.preamp()]
        lines += [b.line() for b in self.bands]
        return '\n'.join(lines) + '\n'

    def _write(self):
        """Atomic: Equalizer APO watches this file, and a half-written one is a
        config it will happily try to parse."""
        tmp = self.eq_file + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(self.render())
        os.replace(tmp, self.eq_file)

    @staticmethod
    def _read_raw(path):
        """Read preserving line endings exactly.  config.txt here is CRLF, and
        silently rewriting it LF would show up as a diff across the user's
        whole file."""
        try:
            with open(path, 'r', encoding='utf-8', newline='') as f:
                return f.read()
        except FileNotFoundError:
            return ''

    def _ensure_include(self):
        """Add our one line to the user's config.txt, once.

        Appended verbatim, with no decorative blank line, so remove() can take
        back exactly what was added.  An earlier version added a leading blank
        line and removed only the two content lines, so every install/uninstall
        cycle grew the user's file by one newline -- six of them accumulated
        before it was noticed.  Hence the byte-for-byte test in __main__.
        """
        path = self.config_file
        text = self._read_raw(path)
        for raw in text.splitlines():
            t = raw.strip()
            if t.startswith('#'):
                continue
            if t.replace(' ', '').lower() == INCLUDE_LINE.replace(' ', '').lower():
                return
        nl = CRLF if CRLF in text else LF
        add = MARKER + nl + INCLUDE_LINE + nl
        if text and not text.endswith((LF, CR)):
            add = nl + add
        with open(path, 'a', encoding='utf-8', newline='') as f:
            f.write(add)

    def remove(self):
        """Take naub back out of the user's config entirely, byte for byte."""
        if not self.available:
            return
        path = self.config_file
        text = self._read_raw(path)
        out = text
        for nl in (CRLF, LF):
            block = MARKER + nl + INCLUDE_LINE + nl
            if block in out:
                out = out.replace(block, '')
                break
        else:
            # Older or hand-edited forms: drop the lines, at the cost of not
            # being able to promise byte-identity.
            keep = [ln for ln in out.splitlines()
                    if ln.strip() != MARKER
                    and ln.strip().replace(' ', '').lower()
                    != INCLUDE_LINE.replace(' ', '').lower()]
            nl = CRLF if CRLF in out else LF
            out = nl.join(keep) + (nl if keep else '')
        if out != text:
            with open(path, 'w', encoding='utf-8', newline='') as f:
                f.write(out)
        if os.path.exists(self.eq_file):
            os.remove(self.eq_file)

    def _restore(self):
        """Read our own file back, so the EQ survives a host restart."""
        try:
            with open(self.eq_file, 'r', encoding='utf-8') as f:
                text = f.read()
        except (FileNotFoundError, OSError):
            return
        i = 0
        for raw in text.splitlines():
            t = raw.split()
            if not t or t[0] != 'Filter:' or i >= len(self.bands):
                continue
            b = self.bands[i]
            i += 1
            try:
                b.on = t[1] == 'ON'
                b.kind = t[2]
                b.f0 = float(t[t.index('Fc') + 1])
                b.db = float(t[t.index('Gain') + 1])
                b.q = float(t[t.index('Q') + 1]) if 'Q' in t else 1.0
            except (ValueError, IndexError):
                continue


if __name__ == '__main__':
    import sys
    eq = ParametricEq()
    ok, why = eq.status()
    print('engine: %s -- %s' % ('READY' if ok else 'NOT USABLE', why))
    print('config: %s' % (eq.config_path or '(none)'))
    if '--check' in sys.argv:
        # The user's config.txt is not ours: prove a full cycle leaves it
        # byte-identical before trusting this with it.
        before = open(eq.config_file, 'rb').read()
        for _ in range(3):
            e = ParametricEq()
            e.set_band(0, kind=LSC, f0=80, db=4.0, q=0.7, on=True)
            e.flush()
            e.remove()
        after = open(eq.config_file, 'rb').read()
        print('3 install/uninstall cycles leave config.txt identical: %s'
              % (before == after))
        print('  %d bytes before, %d after' % (len(before), len(after)))
    elif '--write' in sys.argv:
        eq.set_band(0, kind=LSC, f0=80, db=4.0, q=0.7, on=True)
        eq.set_band(2, kind=PK, f0=1000, db=-3.0, q=1.4, on=True)
        eq.flush()
        print('wrote %s:\n%s' % (eq.eq_file, eq.render()))
        if '--keep' not in sys.argv:
            eq.remove()
            print('removed again (pass --keep to leave it in place)')
    else:
        print('\nwould write:\n%s' % eq.render())
