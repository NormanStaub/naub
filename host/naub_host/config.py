"""Load and normalize the host config (deej-style channel -> process mapping)."""
import json


class Config:
    def __init__(self, port="auto", refresh_seconds=2.0, channels=None):
        self.port = port
        self.refresh_seconds = refresh_seconds
        self.channels = channels or {}  # {int ch: {"label": str, "match": [str]}}


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    channels = {}
    for k, v in (raw.get("channels") or {}).items():
        try:
            ch = int(k)
        except (TypeError, ValueError):
            continue
        match = [str(m) for m in (v.get("match") or [])]
        channels[ch] = {"label": v.get("label", ""), "match": match}

    return Config(
        port=raw.get("port", "auto"),
        refresh_seconds=float(raw.get("refresh_seconds", 2.0)),
        channels=channels,
    )
