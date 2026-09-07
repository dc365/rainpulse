"""Task-scoped, lazy, read-only array views shared by QC stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import zarr
from zarr.storage import MemoryStore


class _ArrayView:
    def __init__(self, source: zarr.Array) -> None:
        self._source = source
        self._values: np.ndarray | None = None

    def __getitem__(self, selection: Any) -> np.ndarray:
        if self._values is None:
            self._values = self._source[:]
            self._values.flags.writeable = False
        return self._values[selection]

    def __len__(self) -> int:
        return len(self._source)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)


class QCGroupView:
    def __init__(self, source: zarr.Group) -> None:
        self._source = source
        self._children: dict[str, QCGroupView | _ArrayView] = {}

    @property
    def attrs(self) -> Any:
        return self._source.attrs

    def __contains__(self, name: str) -> bool:
        return name in self._source

    def __getitem__(self, name: str) -> QCGroupView | _ArrayView:
        if name not in self._children:
            value = self._source[name]
            self._children[name] = (
                QCGroupView(value) if isinstance(value, zarr.Group) else _ArrayView(value)
            )
        return self._children[name]

    def __iter__(self):
        return iter(self._source)


@dataclass(frozen=True)
class QCInputView:
    # Identity prevents accidentally pairing a cache with a different artifact.
    objects: dict[str, bytes]
    root: QCGroupView


def open_qc_input(objects: dict[str, bytes]) -> QCInputView:
    store = MemoryStore()
    store.update(objects)
    return QCInputView(objects, QCGroupView(zarr.open_group(store=store, mode="r")))
