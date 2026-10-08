# ruff: noqa: E501, I001
"""Frozen existing PNG/colour expressions with a synthetic test palette.

No reference meteorological QC is replaced. The injectable renderer makes the
comparison writer independently testable; full repository tests may also use
rainpulse_algo.multiband.product directly.
"""
import struct
import zlib
import numpy as np
from rainpulse_algo.multiband.codec import encode_arrays as encode_arrays
LEVELS = np.arange(-10, 70, 5, dtype=float)
COLORS = np.array([[i * 13 % 256, i * 31 % 256, i * 43 % 256] for i in range(len(LEVELS))], np.uint8)

def png(rgba):
    h, w = rgba.shape[:2]

    def chunk(n, d):
        return struct.pack('>I', len(d)) + n + d + struct.pack('>I', zlib.crc32(n + d) & 4294967295)
    scan = b''.join((b'\x00' + row.tobytes() for row in rgba))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(scan, 3)) + chunk(b'IEND', b'')

def quicklook(values):
    supported = np.isfinite(values)
    index = np.clip(np.searchsorted(LEVELS, np.nan_to_num(values, nan=-100), side='right') - 1, 0, len(LEVELS) - 1)
    rgba = np.zeros((*values.shape, 4), np.uint8)
    rgba[:, :, :3] = COLORS[index]
    rgba[:, :, 3] = np.where(supported, 255, 0)
    return png(rgba[::-1])

def categorical_preview(values, legend):
    rgba = np.zeros((*values.shape, 4), np.uint8)
    for entry in legend:
        color = entry['color'].lstrip('#')
        rgba[values == entry['minimum']] = [*[int(color[i:i + 2], 16) for i in (0, 2, 4)], 255]
    return png(rgba[::-1])

def difference_quicklook(values):
    return quicklook(values)
