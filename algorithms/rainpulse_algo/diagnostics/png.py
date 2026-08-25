from __future__ import annotations

import struct
import zlib

import numpy as np

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def encode_rgba_png(values: np.ndarray) -> bytes:
    rgba = np.asarray(values, dtype=np.uint8)
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.shape[0] == 0 or rgba.shape[1] == 0:
        raise ValueError("PNG input must be a non-empty height x width x RGBA array")
    height, width, _ = rgba.shape
    scanlines = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))
    return b"".join(
        (
            PNG_SIGNATURE,
            _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            _chunk(b"IDAT", zlib.compress(scanlines, level=7)),
            _chunk(b"IEND", b""),
        )
    )


def png_dimensions(data: bytes) -> tuple[int, int]:
    if not data.startswith(PNG_SIGNATURE) or len(data) < 24 or data[12:16] != b"IHDR":
        raise ValueError("invalid PNG signature or header")
    return struct.unpack(">II", data[16:24])


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))
