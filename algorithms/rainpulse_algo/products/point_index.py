from __future__ import annotations

import struct

import numpy as np

MAGIC = b"RPPNTV1\x00"
HEADER = struct.Struct(">8sHHHHdddd16x")
RECORD_BYTES = 5


def encode_point_query_index(
    rain_rate: np.ndarray,
    confidence: np.ndarray,
    valid_mask: np.ndarray,
    *,
    west: float,
    south: float,
    longitude_interval: float,
    latitude_interval: float,
) -> bytes:
    rate = np.asarray(rain_rate, dtype="float32")
    quality = np.asarray(confidence, dtype="float32")
    valid = np.asarray(valid_mask) == 1
    if rate.ndim != 3 or rate.shape != quality.shape or rate.shape != valid.shape:
        raise ValueError("point-query arrays must share lead x lat x lon shape")
    leads, height, width = rate.shape
    if not 1 <= leads <= 65535 or max(height, width) > 65535:
        raise ValueError("point-query dimensions exceed contract limits")
    if np.any(~np.isfinite(rate[valid])) or np.any(rate[valid] < 0):
        raise ValueError("valid point-query rain rates must be finite and non-negative")
    if np.any(~np.isfinite(quality[valid])) or np.any(
        (quality[valid] < 0) | (quality[valid] > 1)
    ):
        raise ValueError("valid point-query confidence must be within [0, 1]")

    cell_order = (1, 2, 0)
    encoded_rate = np.transpose(rate, cell_order).astype(">f4", copy=True)
    encoded_rate[~np.transpose(valid, cell_order)] = np.nan
    encoded_confidence = np.full(encoded_rate.shape, 255, dtype="uint8")
    encoded_confidence[np.transpose(valid, cell_order)] = np.rint(
        np.transpose(quality, cell_order)[np.transpose(valid, cell_order)] * 254.0
    ).astype("uint8")
    records = np.empty(encoded_rate.shape, dtype=[("rate", ">f4"), ("confidence", "u1")])
    records["rate"] = encoded_rate
    records["confidence"] = encoded_confidence
    header = HEADER.pack(
        MAGIC,
        width,
        height,
        leads,
        RECORD_BYTES,
        west,
        south,
        longitude_interval,
        latitude_interval,
    )
    return header + records.tobytes(order="C")


def validate_point_query_index(data: bytes) -> dict[str, int | float]:
    if len(data) < HEADER.size:
        raise ValueError("point-query index is shorter than its header")
    magic, width, height, leads, record_bytes, west, south, dx, dy = HEADER.unpack_from(data)
    if magic != MAGIC or record_bytes != RECORD_BYTES:
        raise ValueError("point-query index identity is invalid")
    if min(width, height, leads) <= 0 or min(dx, dy) <= 0:
        raise ValueError("point-query grid metadata is invalid")
    expected = HEADER.size + width * height * leads * record_bytes
    if len(data) != expected:
        raise ValueError("point-query index byte length differs from its dimensions")
    return {
        "width": width,
        "height": height,
        "lead_count": leads,
        "record_bytes": record_bytes,
        "header_bytes": HEADER.size,
        "cell_bytes": leads * record_bytes,
        "west": west,
        "south": south,
        "longitude_interval": dx,
        "latitude_interval": dy,
    }
