"""Immutable measured native objects shared by nomination and disposition."""

import hashlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class MeasuredWindow:
    block: int
    start_m: float
    end_m: float
    left_deg: float
    right_deg: float
    left_row: int | None
    right_row: int | None
    members: tuple[int, ...]
    known_fraction: float
    contrast_fraction: float
    anchor_support_m: float


@dataclass(frozen=True)
class RawObject:
    identity: int
    kind: str
    scale_m: float
    level_dbz: float
    start_m: float
    end_m: float
    support_m: float
    windows: tuple[MeasuredWindow, ...]
    history_holds: tuple[str, ...]
    origin_ids: tuple[int, ...] = ()
    native_segment_start: int = 0

    @property
    def members(self):
        return tuple(sorted({i for w in self.windows for i in w.members}))

    @property
    def membership_sha256(self):
        return hashlib.sha256(np.asarray(self.members, dtype="<u8").tobytes()).hexdigest()
