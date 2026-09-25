"""Frozen CPU execution settings, separate from meteorological network policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutionOptions:
    version: str = "multiband-cpu-stream-20260925-v1"
    streaming: bool = False
    selection_backend: str = "numpy"
    scratch_parent: str | None = None
    maximum_scratch_bytes: int = 4 * 1024**3
    maximum_object_bytes: int = 128 * 1024**2
    maximum_cut_bytes: int = 256 * 1024**2
    maximum_qc_cut_bytes: int = 512 * 1024**2
    maximum_output_bytes: int = 256 * 1024**2
    maximum_tile_bytes: int = 128 * 1024**2
    layer_memory_bytes: int = 64 * 1024**2
    maximum_sweeps: int = 64
    maximum_volume_gates: int = 64_000_000
    maximum_task_gates: int = 256_000_000
    # Kept per task / one station, not multiplied by every sweep or station.
    geometry_cache_bytes: int = 32 * 1024**2
    maximum_cut_cache_entries: int = 128

    def __post_init__(self):
        if self.version != "multiband-cpu-stream-20260925-v1" or type(self.streaming) is not bool:
            raise ValueError("invalid execution policy")
        if self.selection_backend not in {"numpy", "numba"}:
            raise ValueError("selection backend must be numpy or numba")
        if not self.streaming and self.selection_backend != "numpy":
            raise ValueError("Numba is explicit and currently limited to streaming selection")
        bounds = {
            "maximum_scratch_bytes": (1, 64 * 1024**3),
            "maximum_object_bytes": (1, 2 * 1024**3),
            "maximum_cut_bytes": (1, 2 * 1024**3),
            "maximum_qc_cut_bytes": (1, 4 * 1024**3),
            "maximum_output_bytes": (1, 2 * 1024**3),
            "maximum_tile_bytes": (1, 2 * 1024**3),
            "layer_memory_bytes": (0, 2 * 1024**3),
            "maximum_sweeps": (1, 64),
            "maximum_volume_gates": (1, 64_000_000),
            "maximum_task_gates": (1, 1_024_000_000),
            "geometry_cache_bytes": (0, 256 * 1024**2),
            "maximum_cut_cache_entries": (1, 1024),
        }
        for name, (lo, hi) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError("invalid execution budget: " + name)
        if self.maximum_cut_bytes > self.maximum_qc_cut_bytes:
            raise ValueError("QC cut budget cannot be smaller than input cut budget")
        if self.maximum_volume_gates > self.maximum_task_gates:
            raise ValueError("volume gate budget cannot exceed task budget")
        if self.scratch_parent is not None:
            if (
                not isinstance(self.scratch_parent, str)
                or not Path(self.scratch_parent).is_absolute()
            ):
                raise ValueError("scratch_parent must be an absolute local path")

    @property
    def digest(self):
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as stream:
            raw = stream.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise ValueError("execution settings too large")

        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError("duplicate execution key")
                out[key] = value
            return out

        value = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(value, dict):
            raise ValueError("execution settings must be an object")
        return cls(**value)
