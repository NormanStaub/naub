"""Make device/ and demos/ importable from host tools (gfx, graybars, ...)."""
import sys, os
_here = os.path.dirname(os.path.abspath(__file__))
for _d in ('device', 'demos'):
    _p = os.path.normpath(os.path.join(_here, '..', _d))
    if _p not in sys.path:
        sys.path.insert(0, _p)
