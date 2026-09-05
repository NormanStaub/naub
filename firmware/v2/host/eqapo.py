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
WRITE_MS = 50.0                           # debounce; see the note above

PK, LSC, HSC = 'PK', 'LSC', 'HSC'
KINDS = (PK, LSC, HSC)

# Three bands, because there are three top knobs -- and these defaults MUST
# match device/eqscreen's LOW / MID / HIGH, or the panel draws a curve the
# host never applies.  Low shelf, peak, high shelf: the tone-control split.
DEFAULTS = ((LSC, 120.0, 0.7),
            (PK, 1000.0, 0.9),
            (HSC, 4000.0, 0.7))
NBAND = len(DEFAULTS)

# A band at 0 dB is already a no-op, so bands are ON by default and `on` is
# reserved for deliberately parking one.  They used to default to OFF, which
# meant every filter was written `Filter: OFF ...` -- Equalizer APO ignored the
# lot, and the EQ silently did nothing at all while the panel drew the curve.
AUTO_PREAMP = False
# Preamp policy.  -(sum of positive gains) can never clip, and measured on real
# audio it also means maxing one band drops everything else by 12.3 dB: the
# boosted region barely moves and it just sounds like the volume went down.
# A hardware mixer's EQ boosts, and this one has meters and a volume knob right
# next to it, so boost is boost by default.  Set True to trade that for
# guaranteed headroom.

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


def fmt_hz(hz):
    """Frequency as Equalizer APO will accept it.

    NOT %g: it switches to scientific notation at five significant digits, so
    20000 came out as `2e+04` and Equalizer APO silently dropped that filter --
    i.e. winding a band to the top of its range turned it off."""
    return ('%.0f' % hz) if hz >= 1000.0 else ('%.4g' % hz)


class Band:
    def __init__(self, kind=PK, f0=1000.0, db=0.0, q=1.0, on=True):
        self.kind = kind
        self.f0 = f0
        self.db = db
        self.q = q
        self.on = on

    def line(self):
        return ('Filter: %s %s Fc %s Hz Gain %.2f dB Q %.3g'
                % ('ON' if self.on else 'OFF', self.kind, fmt_hz(self.f0),
                   self.db, self.q))

    def wire(self):
        """The compact form the protocol carries."""
        return '%s %s %.2f %.3g %d' % (self.kind, fmt_hz(self.f0), self.db,
                                       self.q, 1 if self.on else 0)


class ParametricEq:
    def __init__(self, nband=NBAND, config_path=None):
        self.config_path = config_path or _config_path()
        # keywords, not *unpack: DEFAULTS is (kind, f0, q) while Band takes
        # (kind, f0, db, q), so positional unpacking silently puts Q in the
        # gain slot -- a band that starts at 0.7 dB with Q 1
        self.bands = [Band(kind=DEFAULTS[i][0], f0=DEFAULTS[i][1],
                           q=DEFAULTS[i][2], db=0.0, on=True)
                      if i < len(DEFAULTS) else Band()
                      for i in range(nband)]
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
        """Every band back to 0 dB.  They stay ON: a 0 dB filter is already a
        no-op, and switching them off would make the next gain change do
        nothing until something turned them back on."""
        for b in self.bands:
            b.db = 0.0
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
        cannot be left behind the display.

        Never raises: this runs inside the serial loop, and an EQ that fails to
        write is a far smaller problem than a panel that stops updating."""
        if not self.available:
            self._dirty = False
            return False
        try:
            self._write()
            self._ensure_include()
        except Exception as e:
            print('EQ write failed: %s' % e)
        self._dirty = False
        self._last_write = now if now is not None else time.monotonic()
        return True

    def preamp(self):
        """Headroom, in dB.  Zero unless AUTO_PREAMP -- see the note there."""
        if not AUTO_PREAMP:
            return 0.0
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
        """Write the config, atomically when Windows lets us.

        Atomic is preferable because Equalizer APO watches this file and a
        half-written one is a config it will try to parse.  But it watches it
        by holding it OPEN, so os.replace onto it intermittently fails with
        PermissionError -- which, unhandled in the bridge, would take the whole
        link down over a cosmetic nicety.  So: try the swap, retry briefly, and
        fall back to writing in place.  A torn read self-corrects on the next
        write, which is at most WRITE_MS away."""
        text = self.render()
        tmp = self.eq_file + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(text)
            for attempt in range(3):
                try:
                    os.replace(tmp, self.eq_file)
                    return
                except PermissionError:
                    time.sleep(0.02)
        except OSError:
            pass
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)          # never leave litter in their config
                except OSError:
                    pass
        try:
            with open(self.eq_file, 'w', encoding='utf-8') as f:
                f.write(text)
        except OSError as e:
            print('EQ write failed: %s' % e)

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
        """Read our own file back, so the EQ survives a host restart.

        A file written by a different band layout is IGNORED rather than
        half-applied.  That is not hypothetical: a five-band file left by an
        older build restored `OFF` onto every band, so the EQ silently did
        nothing and kept doing nothing across restarts, because each new
        instance loaded the same dead state back in."""
        try:
            with open(self.eq_file, 'r', encoding='utf-8') as f:
                text = f.read()
        except (FileNotFoundError, OSError):
            return
        n = sum(1 for ln in text.splitlines()
                if ln.strip().lower().startswith('filter:'))
        if n != len(self.bands):
            print('ignoring %s: %d filters, this build has %d bands'
                  % (INCLUDE_NAME, n, len(self.bands)))
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
