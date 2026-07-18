"""Standalone tester for the global EQ bridge (Equalizer APO).

This proves the Windows global-EQ path independently of the device: it writes the
same naub-eq.txt the host will, so you can *hear* bass/mid/treble move while
audio plays. Nothing here touches the device or the serial link.

    python eq_test.py status            # is Equalizer APO found? where?
    python eq_test.py show              # current band positions
    python eq_test.py set bass 80       # 0..100, 50 = flat
    python eq_test.py db treble +6      # set a band directly in dB
    python eq_test.py flat              # reset all bands to 0 dB
    python eq_test.py sweep bass        # ramp a band down..up..flat to hear it
"""
import argparse
import sys
import time

from naub_host.eq_windows import BANDS, MAX_DB, GlobalEq

_BANDS = [b[0] for b in BANDS]


def _require(eq):
    if not eq.available:
        print("Equalizer APO not found (registry HKLM\\SOFTWARE\\EqualizerAPO).")
        print("Install it from https://sourceforge.net/projects/equalizerapo/ "
              "and assign it to your output device, then re-run.")
        sys.exit(2)


def _print_state(eq):
    vals = eq.values()
    dbs = eq.db_values()
    for k in _BANDS:
        print("  %-7s %3d%%  (%+.1f dB)" % (k, vals[k], dbs[k]))


def main():
    ap = argparse.ArgumentParser(description="naub global EQ tester")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("show")
    sub.add_parser("flat")
    p = sub.add_parser("set"); p.add_argument("band", choices=_BANDS); p.add_argument("value", type=float)
    p = sub.add_parser("db"); p.add_argument("band", choices=_BANDS); p.add_argument("db", type=float)
    p = sub.add_parser("sweep"); p.add_argument("band", choices=_BANDS)
    args = ap.parse_args()

    eq = GlobalEq()

    if args.cmd == "status":
        if eq.available:
            print("Equalizer APO config: %s" % eq.config_path)
            print("EQ include file:      %s" % eq._eq_file)
        else:
            print("Equalizer APO: NOT FOUND")
        return

    _require(eq)

    if args.cmd == "show":
        _print_state(eq)
    elif args.cmd == "flat":
        eq.flat(); print("flat:"); _print_state(eq)
    elif args.cmd == "set":
        db = eq.set_band(args.band, args.value)
        print("%s -> %g%% (%+.1f dB)" % (args.band, args.value, db))
    elif args.cmd == "db":
        eq.set_db(args.band, args.db); print("%s -> %+.1f dB" % (args.band, args.db))
    elif args.cmd == "sweep":
        print("Sweeping %s -- play some audio. Ctrl-C to stop and leave flat."
              % args.band)
        try:
            for db in list(range(0, -int(MAX_DB) - 1, -1)) + \
                      list(range(-int(MAX_DB), int(MAX_DB) + 1)) + \
                      list(range(int(MAX_DB), -1, -1)):
                eq.set_db(args.band, db)
                print("  %+3d dB" % db, end="\r", flush=True)
                time.sleep(0.4)
            print("\nback to flat.")
        except KeyboardInterrupt:
            eq.set_db(args.band, 0); print("\nstopped; %s flat." % args.band)


if __name__ == "__main__":
    main()
