"""Lossless JSON record tables; never truncate scientific evidence to fit."""

import base64
import hashlib
import json
import zlib

PACKED = "xqc-record-table-zlib-v1"
MAX_DECODED_BYTES = 64 * 1024**2

FORMAT = "xqc-record-table-v1"


def compact(value):
    if isinstance(value, dict):
        return {key: compact(item) for key, item in value.items()}
    if not isinstance(value, list):
        return value
    if len(value) >= 2 and all(isinstance(item, dict) for item in value):
        columns = list(value[0])
        if columns and all(set(item) == set(columns) for item in value):
            return {
                "encoding": FORMAT,
                "columns": columns,
                "rows": [[compact(item[key]) for key in columns] for item in value],
            }
    return [compact(item) for item in value]


def expand(value):
    if isinstance(value, list):
        return [expand(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("encoding") == PACKED:
        size = value["decoded_bytes"]
        if type(size) is not int or not 0 <= size <= MAX_DECODED_BYTES:
            raise ValueError("X evidence decoded size exceeds limit")
        payload = base64.b64decode(value["data"], validate=True)
        decoder = zlib.decompressobj()
        raw = decoder.decompress(payload, size + 1)
        if (
            len(raw) != size
            or not decoder.eof
            or decoder.unused_data
            or decoder.unconsumed_tail
            or hashlib.sha256(raw).hexdigest() != value["sha256"]
        ):
            raise ValueError("X evidence compressed integrity mismatch")
        return expand(json.loads(raw))
    if value.get("encoding") == FORMAT and set(value) == {"encoding", "columns", "rows"}:
        columns = value["columns"]
        if len(set(columns)) != len(columns) or any(
            len(row) != len(columns) for row in value["rows"]
        ):
            raise ValueError("invalid X QC evidence record table")
        return [{key: expand(item) for key, item in zip(columns, row)} for row in value["rows"]]
    return {key: expand(item) for key, item in value.items()}


def pack_details(value):
    """Compress model tables only; status/count/module metadata stay readable."""
    if isinstance(value, list):
        # Model entries can have distinct schemas (fallback/primary modes).
        # Preserve the whole list losslessly rather than missing table packing.
        if len(value) >= 2 and all(isinstance(item, dict) for item in value):
            packed = _pack(value)
            if packed is not value:
                return packed
        return [pack_details(item) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("encoding") == FORMAT:
        return _pack(value)
    return {key: pack_details(item) for key, item in value.items()}


def _pack(value):
    raw = json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    if 1024 <= len(raw) <= MAX_DECODED_BYTES:
        packed = dict(encoding=PACKED, decoded_bytes=len(raw),
                      sha256=hashlib.sha256(raw).hexdigest(),
                      data=base64.b64encode(zlib.compress(raw)).decode("ascii"))
        if len(json.dumps(packed)) < len(raw):
            return packed
    return value
