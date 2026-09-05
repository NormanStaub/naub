"""Host <-> device line link over the USB CDC *data* port (see PROTOCOL.md).

One message per line, space separated, ASCII, unknown verbs ignored.  The
console port cannot be used for this: host->device writes on it hang on this
board (V2 bring-up finding), which is exactly what the data port is for.

Needs device/boot.py deployed AND a full power cycle -- USB descriptors are
fixed at enumeration.  If that has not happened, `Link.ready` is False and the
caller should fall back to running standalone.

    lk = link.Link()
    for line in lk.lines():      # non-blocking, may be empty
        ...
    lk.send('vol 0 62')

Verbs used by the mixer screen (V1 verbs kept, `meter` added for V2):

    H->D   label <ch> <text>      column name, ch 0-2
           level <ch> <0..100>    volume; ch 3 is the system/master
           mute  <ch> <0|1>
           meter <v0> <v1> <v2>   live per-column levels, 0..100  (V2)
           apps  <A,B,C>          apps with a live session; the app switcher's
                                  list, comma separated because process names
                                  can contain spaces  (V2)
           ameter <v0> <v1> ...   one level per app in `apps`, in that order.
                                  Fast while the switcher is open, 5 Hz
                                  otherwise -- the device times how long each
                                  app has played from these (V2)
           eqcaps <n> <fmin> <fmax> <maxdb> <sr> <ok>
                                  what the EQ engine offers, on connect; ok=0
                                  means writing filters would do nothing  (V2)
           eqband <i> <kind> <f0> <db> <q> <on>
                                  one band's full state  (V2)
    D->H   naub proto=2 ...       banner, on connect
           vol   <ch> <0..100>    a knob moved
           mute  <ch> <0|1>       a knob was held
           pick  <ch> <APP>       the switcher assigned an app to a column;
                                  the host pins it there  (V2)
           eqp   <i> <param> <v>  an EQ parameter moved; param is one of
                                  gain freq q kind on  (V2)
           eqflat                 reset every band  (V2)
           mode  <idx>            the mode bar was flipped, and on connect (V2)
           wheel <0|1>            the switcher opened / closed  (V2)
"""
import usb_cdc

BANNER = 'naub proto=2 channels=4 fw=v2-mixer'


class Link:
    def __init__(self, port=None):
        self.port = usb_cdc.data if port is None else port
        if self.port is not None:
            # never block the frame loop waiting on the host
            self.port.timeout = 0
            self.port.write_timeout = 0
        self._buf = b''

    @property
    def ready(self):
        return self.port is not None

    @property
    def connected(self):
        p = self.port
        return p is not None and p.connected

    def lines(self):
        """Complete lines received since the last call; never blocks."""
        p = self.port
        if p is None:
            return ()
        n = p.in_waiting
        if not n:
            return ()
        self._buf += p.read(n)
        if b'\n' not in self._buf:
            return ()
        parts = self._buf.split(b'\n')
        self._buf = parts.pop()          # keep the trailing partial line
        out = []
        for raw in parts:
            try:
                s = raw.decode('utf-8').strip()
            except Exception:
                continue
            if s:
                out.append(s)
        return out

    def send(self, s):
        """Fire and forget.  Drops the message rather than stalling if the host
        is not draining the port."""
        p = self.port
        if p is None or not p.connected:
            return
        try:
            p.write(s.encode('utf-8'))
            p.write(b'\n')
        except Exception:
            pass
