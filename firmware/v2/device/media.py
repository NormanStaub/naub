"""Media transport keys over USB HID Consumer Control.

    md = media.Media()
    md.tap(media.PREV)          # on a button press

This deliberately does NOT go through the naub line protocol.  Transport keys
are a system-level thing, exactly like V1's system volume: sending them as HID
means they work with no host software running at all -- on the lock screen,
in a browser, in a game -- and whichever app Windows currently considers the
media session picks them up.  Nothing to configure, nothing to install.

No library needed.  CircuitPython exposes a Consumer Control endpoint by
default (usage page 0x0C, usage 0x01) and its report is just the 16-bit usage
ID, little endian, with 0 meaning "released".  adafruit_hid wraps exactly this;
vendoring one struct.pack is cheaper than shipping the library.

If the endpoint is missing (HID disabled in boot.py, or the board is running
without USB), `ready` is False and every call is a no-op -- the panel still
works, the keys just do nothing.
"""
import struct

import usb_hid

# Consumer page usage IDs.  SCAN_PREV/NEXT are the track-skip pair every player
# honours -- they are what a transport button labelled |<< or >>| actually does.
# REWIND/FAST_FORWARD are the literal scrub usages; they exist, but most Windows
# apps (Spotify included) ignore them, so they are not the default.
SCAN_PREV = 0x00B6
SCAN_NEXT = 0x00B5
PLAY_PAUSE = 0x00CD
STOP = 0x00B7
REWIND = 0x00B4
FAST_FORWARD = 0x00B3

PREV, PLAY, NEXT = SCAN_PREV, PLAY_PAUSE, SCAN_NEXT

_RELEASE = struct.pack('<H', 0)


class Media:
    def __init__(self):
        self.dev = None
        for d in usb_hid.devices:
            if d.usage_page == 0x0C and d.usage == 0x01:
                self.dev = d
                break

    @property
    def ready(self):
        return self.dev is not None

    def tap(self, usage):
        """Press and release one key.  Fire and forget: a host that is not
        reading HID must never stall the frame loop."""
        d = self.dev
        if d is None:
            return False
        try:
            d.send_report(struct.pack('<H', usage))
            d.send_report(_RELEASE)
            return True
        except Exception:
            return False
