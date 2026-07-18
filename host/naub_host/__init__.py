"""naub host helper - per-app audio mixer bridge for the naub USB device.

Reads knob/mute events from the device over USB CDC and applies them to Windows
per-app audio (WASAPI via pycaw); pushes labels, program icons, and corrected
volume/mute back to the device's e-paper display.

The OS-specific audio + icon code is isolated in `audio_windows` / `icons_windows`
so Linux (PipeWire) and macOS adapters can slot in alongside later.
"""
__version__ = "1.0"
