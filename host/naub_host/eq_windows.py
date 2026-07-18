"""Windows global (system) EQ via Equalizer APO.

naub drives a 3-band tone control -- bass / mid / treble -- on the output
endpoint by writing a small include file that Equalizer APO hot-reloads. This is
**global** EQ: it shapes everything mixed to the device, not per-app (Windows
exposes no per-session EQ -- see PROTOCOL.md / the EQ discussion). The host owns
exactly one file (`naub-eq.txt`) and adds a single `Include:` line to Equalizer
APO's `config.txt`; the user's own filters are never touched.

Discovery is via the registry (`HKLM\\SOFTWARE\\EqualizerAPO\\ConfigPath`). If
Equalizer APO isn't installed, `GlobalEq.available` is False and every setter is
a no-op, so the host runs fine without it -- the EQ is an optional add-on, not a
hard dependency.

Why these filters: a low-shelf for bass and a high-shelf for treble behave like
classic tone controls (everything below/above the corner moves together); a
gentle wide peak handles mids. Frequencies and the +/-12 dB range are the
conventional 3-band tone-control choices, not measured-room correction -- that's
what the user's own PEQ (left untouched, applied first) is for.
"""
import os

try:
    import winreg
except ImportError:  # non-Windows: module still imports, just unavailable
    winreg = None

# Marker so we can find/own our include line in the user's config.txt.
_INCLUDE_NAME = "naub-eq.txt"
_INCLUDE_LINE = "Include: " + _INCLUDE_NAME
_MARKER = "# naub global EQ (managed by the naub host)"

MAX_DB = 12.0  # knob full-scale = +/-12 dB; 50% = flat (0 dB)

# band key -> (Equalizer APO filter type, center freq Hz, optional Q)
# Order here is the order written to the file (cosmetic; APO sums them anyway).
BANDS = (
    ("bass",   "LS", 120,  None),
    ("mid",    "PK", 1000, 0.9),
    ("treble", "HS", 4000, None),
)
_BAND_KEYS = [b[0] for b in BANDS]


def _value_to_db(value_0_100):
    """0..100 knob position -> dB. 50 is flat; 0 -> -MAX_DB, 100 -> +MAX_DB."""
    v = max(0.0, min(100.0, float(value_0_100)))
    return (v - 50.0) / 50.0 * MAX_DB


def _db_to_value(db):
    """Inverse of _value_to_db, for restoring knob positions from a saved file."""
    return int(round(db / MAX_DB * 50.0 + 50.0))


def _discover_config_path():
    """Return Equalizer APO's config dir, or None if not installed."""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\EqualizerAPO") as k:
            path, _ = winreg.QueryValueEx(k, "ConfigPath")
        if path and os.path.isdir(path):
            return path
    except OSError:
        pass
    return None


class GlobalEq:
    """3-band global tone control backed by Equalizer APO.

    Construct once; call `set_band` / `set_db` / `flat`. Writes are idempotent
    and cheap (a ~200 B file), and Equalizer APO reloads on the file change, so
    a knob turn is audible within its watch latency.
    """

    def __init__(self, config_path=None):
        self.config_path = config_path or _discover_config_path()
        # dB held per band; restored from an existing naub-eq.txt if present so
        # the EQ survives a host restart, else flat.
        self._db = {k: 0.0 for k in _BAND_KEYS}
        if self.available:
            self._restore()

    # ---- availability --------------------------------------------------------

    @property
    def available(self):
        return self.config_path is not None

    @property
    def _eq_file(self):
        return os.path.join(self.config_path, _INCLUDE_NAME)

    @property
    def _config_file(self):
        return os.path.join(self.config_path, "config.txt")

    # ---- public setters ------------------------------------------------------

    def set_band(self, band, value_0_100):
        """Set a band from a 0..100 knob position (50 = flat). Returns the dB."""
        db = _value_to_db(value_0_100)
        self.set_db(band, db)
        return db

    def set_db(self, band, db):
        key = self._key(band)
        self._db[key] = max(-MAX_DB, min(MAX_DB, float(db)))
        self._write()

    def flat(self):
        """Reset all bands to 0 dB."""
        for k in _BAND_KEYS:
            self._db[k] = 0.0
        self._write()

    def values(self):
        """Current per-band knob positions (0..100), for pushing back to the panel."""
        return {k: _db_to_value(self._db[k]) for k in _BAND_KEYS}

    def db_values(self):
        return dict(self._db)

    # ---- internals -----------------------------------------------------------

    def _key(self, band):
        if isinstance(band, int):
            return _BAND_KEYS[band]
        b = str(band).lower()
        if b not in self._db:
            raise KeyError("unknown EQ band %r (want one of %s)"
                           % (band, _BAND_KEYS))
        return b

    def _render(self):
        lines = [
            _MARKER,
            "# 3-band tone control driven by the device; do not hand-edit.",
            "# Range +/-%g dB, written as low-shelf / peak / high-shelf." % MAX_DB,
        ]
        for key, ftype, fc, q in BANDS:
            db = self._db[key]
            line = "Filter: ON %s Fc %d Hz Gain %.1f dB" % (ftype, fc, db)
            if q is not None:
                line += " Q %.3g" % q
            lines.append(line)
        return "\n".join(lines) + "\n"

    def _write(self):
        if not self.available:
            return
        with open(self._eq_file, "w", encoding="utf-8") as f:
            f.write(self._render())
        self._ensure_include()

    def _ensure_include(self):
        """Append our Include line to config.txt once, leaving all else intact."""
        path = self._config_file
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            text = ""
        # Already included (ignore commented-out forms)?
        for raw in text.splitlines():
            s = raw.strip()
            if s.startswith("#"):
                continue
            if s.replace(" ", "").lower() == _INCLUDE_LINE.replace(" ", "").lower():
                return
        addition = "\n%s\n%s\n" % (_MARKER, _INCLUDE_LINE)
        if text and not text.endswith("\n"):
            addition = "\n" + addition
        with open(path, "a", encoding="utf-8") as f:
            f.write(addition)

    def _restore(self):
        """Load dB values back from an existing naub-eq.txt (best effort)."""
        try:
            with open(self._eq_file, "r", encoding="utf-8") as f:
                text = f.read()
        except (FileNotFoundError, OSError):
            return
        # Match our written order: first ON filter -> bass, etc.
        gains = []
        for raw in text.splitlines():
            s = raw.strip()
            if not s.lower().startswith("filter:"):
                continue
            # ... Gain <x> dB ...
            toks = s.split()
            try:
                gi = toks.index("Gain")
                gains.append(float(toks[gi + 1]))
            except (ValueError, IndexError):
                gains.append(0.0)
        for key, db in zip(_BAND_KEYS, gains):
            self._db[key] = max(-MAX_DB, min(MAX_DB, db))
