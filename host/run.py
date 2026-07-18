#!/usr/bin/env python3
"""naub host helper - entry point.

Usage:
  python run.py                         # use config.json (or config.example.json)
  python run.py --config myconfig.json
  python run.py --port COM7             # skip autodetect
  python run.py --list-ports            # show serial ports and exit
  python run.py --probe                 # autodetect the naub data port and exit
"""
import argparse
import os
import sys

from naub_host import config as cfgmod
from naub_host.app import App
from naub_host.serial_link import autodetect, list_ports


def _default_config_path():
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("config.json", "config.example.json"):
        p = os.path.join(here, name)
        if os.path.exists(p):
            return p
    return os.path.join(here, "config.json")


def main(argv=None):
    ap = argparse.ArgumentParser(description="naub host helper")
    ap.add_argument("--config", default=None, help="path to config JSON")
    ap.add_argument("--port", default=None, help="serial port (overrides config)")
    ap.add_argument("--list-ports", action="store_true", help="list serial ports and exit")
    ap.add_argument("--probe", action="store_true", help="autodetect naub port and exit")
    args = ap.parse_args(argv)

    if args.list_ports:
        for dev, desc in list_ports():
            print("%-10s %s" % (dev, desc))
        return 0

    if args.probe:
        port = autodetect()
        print("naub data port: %s" % (port or "not found"))
        return 0 if port else 1

    cfg = cfgmod.load(args.config or _default_config_path())
    if args.port:
        cfg.port = args.port

    app = App(cfg)
    try:
        app.run_forever()
    except KeyboardInterrupt:
        print("\nbye")
        return 0


if __name__ == "__main__":
    sys.exit(main())
