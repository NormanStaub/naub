"""Regenerate naubd/src/frc_golden.bin: what the Rust icon encoder must match.

naubd encodes pushed icons itself (naubd/src/appicon.rs), so there are two
implementations of the FRC mapping and only a byte comparison keeps them the
same.  The pattern hits every gray level at every Bayer cell and phase.

    python gen_frc_golden.py
"""
import _paths
import os
from PIL import Image
import drawer, gen_switcher


def pattern(size):
    im = Image.new('L', (size, size))
    im.putdata([(x * 7 + y * 13) % 256 for y in range(size) for x in range(size)])
    return im


if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.normpath(os.path.join(here, '..', '..', '..', 'naubd', 'src',
                                        'frc_golden.bin'))
    blob = b''.join(p for size, page0 in drawer.TIERS
                    for p in gen_switcher.block(pattern(size), size, page0))
    with open(out, 'wb') as f:
        f.write(blob)
    print('wrote %s (%d bytes)' % (out, len(blob)))
