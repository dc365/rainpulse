"""A bounded, read-only view over a frozen ArtifactSession.

Schema 1/2: verify logical objects on demand through the existing byte cache.
Schema 3: verify every physical pack AND the complete logical aggregate before
exposing any key. Packs are staged sequentially, not accumulated in RAM.
No new marker schema; no weakening of the historical checksum contract.
"""

from __future__ import annotations

import hashlib
import shutil
from collections import OrderedDict
from collections.abc import MutableMapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from rainpulse_algo.performance import timed as _perf_timed

BLOCK = 1024 * 1024


class StagedArtifact(MutableMapping):
    def __init__(self, session, directory, *, selected, maximum_object_bytes):
        self.session = session
        self.directory = Path(directory)
        self.selected = frozenset(selected)
        self.maximum_object_bytes = maximum_object_bytes
        self.files = {}
        self.metadata_cache = OrderedDict()
        self.metadata_bytes = 0
        self.metadata_limit = min(4 * 1024**2, maximum_object_bytes)
        self.closed = False
        self.stats = {
            "staged_bytes": 0,
            "peak_object_bytes": 0,
            "logical_read_bytes": 0,
            "logical_reads": 0,
            "packed_full_verified": 0,
            "metadata_cache_hits": 0,
            "metadata_cache_peak_bytes": 0,
        }

    def _ensure_open(self):
        if self.closed:
            raise RuntimeError("staged artifact is closed")

    def __iter__(self):
        self._ensure_open()
        return iter(sorted(self.selected))

    def __len__(self):
        return len(self.selected)

    def __contains__(self, key):
        self._ensure_open()
        return key in self.selected

    def __setitem__(self, key, value):
        raise TypeError("verified input is read-only")

    def __delitem__(self, key):
        raise TypeError("verified input is read-only")

    def _physical(self, key):
        entry = self.session.index.physical[key]
        if entry.size > self.maximum_object_bytes:
            raise ValueError("physical object exceeds streaming in-memory read budget")
        _, data = self.session._read_entry(entry)
        self.stats["peak_object_bytes"] = max(self.stats["peak_object_bytes"], len(data))
        return data

    def __getitem__(self, key):
        self._ensure_open()
        if key not in self.selected:
            raise KeyError(key)
        metadata = key.rsplit("/", 1)[-1] in {".zattrs", ".zarray", ".zgroup"}
        if metadata and key in self.metadata_cache:
            self.stats["metadata_cache_hits"] += 1
            self.metadata_cache.move_to_end(key)
            return self.metadata_cache[key]
        pack, offset, size = self.session.index.logical[key]
        if size > self.maximum_object_bytes:
            raise ValueError("logical object exceeds streaming read budget")
        if self.session.index.schema == "3.0":
            with self.files[pack].open("rb") as stream:
                stream.seek(offset)
                value = stream.read(size)
            if len(value) != size:
                raise RuntimeError("staged pack truncated")
        else:
            value = self._physical(pack)
        if metadata and size <= self.metadata_limit:
            while self.metadata_cache and (
                self.metadata_bytes + size > self.metadata_limit or len(self.metadata_cache) >= 4096
            ):
                _, old = self.metadata_cache.popitem(last=False)
                self.metadata_bytes -= len(old)
            self.metadata_cache[key] = value
            self.metadata_bytes += size
            self.stats["metadata_cache_peak_bytes"] = max(
                self.stats["metadata_cache_peak_bytes"], self.metadata_bytes
            )
        self.stats["logical_read_bytes"] += size
        self.stats["logical_reads"] += 1
        return value

    @_perf_timed("io.local_stage")
    def copy_logical_to(self, key, destination):
        """Bounded local copy, primarily for a seekable legacy NPZ container."""
        self._ensure_open()
        if key not in self.selected:
            raise KeyError(key)
        pack, offset, size = self.session.index.logical[key]
        with Path(destination).open("xb") as out:
            if self.session.index.schema == "3.0":
                with self.files[pack].open("rb") as source:
                    source.seek(offset)
                    remaining = size
                    while remaining:
                        data = source.read(min(BLOCK, remaining))
                        if not data:
                            raise RuntimeError("truncated staged pack")
                        out.write(data)
                        remaining -= len(data)
            else:
                # Existing transport returns one physical object as bytes.
                # Its declared size is checked BEFORE any read is issued.
                data = self._physical(pack)
                out.write(data)
        return size

    @_perf_timed("io.verify_packed_asset")
    def verify_packs(self):
        index = self.session.index
        for number, key in enumerate(sorted(index.physical)):
            path = self.directory / f"pack-{number:06d}.bin"
            data = self._physical(key)
            with path.open("xb") as stream:
                stream.write(data)
            self.stats["staged_bytes"] += len(data)
            self.files[key] = path
            del data
        aggregate = hashlib.sha256()
        current = None
        stream = None
        try:
            for key in sorted(index.logical):
                pack, offset, size = index.logical[key]
                if pack != current:
                    if stream is not None:
                        stream.close()
                    stream = self.files[pack].open("rb")
                    current = pack
                stream.seek(offset)
                remaining, digest = size, hashlib.sha256()
                while remaining:
                    data = stream.read(min(BLOCK, remaining))
                    if not data:
                        raise RuntimeError("staged pack truncated")
                    digest.update(data)
                    remaining -= len(data)
                encoded = key.encode()
                aggregate.update(len(encoded).to_bytes(4, "big"))
                aggregate.update(encoded)
                aggregate.update(size.to_bytes(8, "big"))
                aggregate.update(digest.digest())
        finally:
            if stream is not None:
                stream.close()
        if aggregate.hexdigest() != index.sha256:
            raise RuntimeError("published artifact bundle checksum differs")
        self.stats["packed_full_verified"] = 1


@contextmanager
def staged(
    session, *, directory=None, maximum_disk_bytes, maximum_object_bytes, keys=None, prefixes=None
):
    if (
        type(maximum_disk_bytes) is not int
        or maximum_disk_bytes <= 0
        or type(maximum_object_bytes) is not int
        or maximum_object_bytes <= 0
    ):
        raise ValueError("positive staging budgets required")
    selected = session.index.select(keys, prefixes)
    packed = session.index.schema == "3.0"
    needed = session.index.size if packed else 0
    if needed > maximum_disk_bytes:
        raise ValueError("packed asset exceeds scratch budget")
    entries = (
        session.index.physical.values() if packed else (session.index.physical[k] for k in selected)
    )
    if any(e.size > maximum_object_bytes for e in entries):
        raise ValueError("physical object exceeds streaming read budget")
    with TemporaryDirectory(prefix="rp-verified-", dir=directory) as root:
        if needed > shutil.disk_usage(root).free:
            raise ValueError("insufficient scratch capacity for verified packs")
        view = StagedArtifact(
            session, root, selected=selected, maximum_object_bytes=maximum_object_bytes
        )
        try:
            if packed:
                view.verify_packs()
            yield view
        finally:
            view.closed = True
            view.metadata_cache.clear()
            view.metadata_bytes = 0
