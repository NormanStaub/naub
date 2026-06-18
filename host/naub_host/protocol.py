"""naub protocol (proto=1) encode/decode helpers. See ../../PROTOCOL.md.

Everything is one line per message, space-separated, UTF-8 / ASCII. Icon payloads
are Base64 of packed 1-bpp rows (MSB = leftmost pixel, 1 = black ink).
"""
import base64

PROTO = 1


# ---- host -> device encoders -------------------------------------------------

def hello():
    return "hello"


def ping():
    return "ping"


def label(ch, text):
    # text may contain spaces; it is the rest of the line on the device side.
    return "label %d %s" % (ch, text)


def icon(ch, w, h, packed_bytes):
    b64 = base64.b64encode(packed_bytes).decode("ascii")
    return "icon %d %d %d %s" % (ch, w, h, b64)


def iconclear(ch):
    return "iconclear %d" % ch


def level(ch, value_0_100):
    return "level %d %d" % (ch, _clamp(value_0_100))


def mute(ch, muted):
    return "mute %d %d" % (ch, 1 if muted else 0)


# ---- device -> host decoder --------------------------------------------------

def parse(line):
    """Return (verb, args_list) or (None, []) for blank lines.

    Banner lines like `naub proto=1 channels=4 fw=v1.0` come back as
    ('naub', ['proto=1', 'channels=4', 'fw=v1.0']).
    """
    line = line.strip()
    if not line:
        return None, []
    parts = line.split(" ")
    return parts[0], parts[1:]


def _clamp(v):
    v = int(round(v))
    return 0 if v < 0 else (100 if v > 100 else v)
