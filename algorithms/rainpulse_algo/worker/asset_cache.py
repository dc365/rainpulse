"""Bounded, process-local cache of verified immutable object bytes.

Only bytes with a declared SHA-256 and length enter the cache. Manifests are
never cached. Concurrent reads of the same identity share one load; unrelated
loads share a hard in-flight limit, including oversized/bypass objects.
"""
from __future__ import annotations

from rainpulse_algo.performance import (timed as _perf_timed, measure as _perf_measure, observe as _perf_observe)

import hashlib
import math
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class CacheLimits:
    max_bytes: int = 0  # Conservative compatibility default; enable per worker pool.
    max_entries: int = 4096
    max_entry_bytes: int = 16 * 1024**2
    max_inflight: int = 4
    ttl_seconds: float = 600

    def __post_init__(self) -> None:
        for name, lower, upper in (
            ("max_bytes", 0, 8 * 1024**3),
            ("max_entries", 1, 100_000),
            ("max_entry_bytes", 1, 2 * 1024**3),
            ("max_inflight", 1, 32),
        ):
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"invalid asset cache {name}")
        if (type(self.ttl_seconds) not in (float, int)
                or not math.isfinite(self.ttl_seconds) or not 0 < self.ttl_seconds <= 86400):
            raise ValueError("invalid asset cache ttl_seconds")

    @classmethod
    def from_environment(cls) -> CacheLimits:
        return cls(
            max_bytes=int(os.getenv("RAINPULSE_ASSET_CACHE_BYTES", "0")),
            max_entries=int(os.getenv("RAINPULSE_ASSET_CACHE_ENTRIES", "4096")),
            max_entry_bytes=int(os.getenv(
                "RAINPULSE_ASSET_CACHE_MAX_OBJECT_BYTES", str(16 * 1024**2)
            )),
            max_inflight=int(os.getenv("RAINPULSE_ASSET_READ_CONCURRENCY", "4")),
            ttl_seconds=float(os.getenv("RAINPULSE_ASSET_CACHE_TTL_SECONDS", "600")),
        )


@dataclass(frozen=True)
class ObjectIdentity:
    namespace: str
    bucket: str
    key: str
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if not self.namespace or not self.bucket or not self.key:
            raise ValueError("object cache identity requires namespace, bucket and key")
        if type(self.size) is not int or self.size < 0 or not _SHA256.fullmatch(self.sha256):
            raise ValueError("object cache identity requires a SHA-256 and non-negative size")


@dataclass(frozen=True)
class CacheRead:
    data: bytes
    source: str  # cache, shared, or store


class VerifiedObjectCache:
    def __init__(self, limits: CacheLimits, *, clock: Callable[[], float] = time.monotonic):
        self.limits = limits
        self._clock = clock
        self._condition = threading.Condition()
        self._entries: OrderedDict[ObjectIdentity, tuple[bytes, float]] = OrderedDict()
        self._inflight: dict[ObjectIdentity, Future[bytes]] = {}
        self._bytes = 0
        self._generation = 0
        self._counters = dict.fromkeys(
            ("hits", "misses", "shared_reads", "capacity_waits", "evictions", "expirations",
             "bypasses", "errors", "store_bytes", "peak_bytes", "peak_inflight"), 0
        )

    @_perf_timed("io.cache_and_verify")
    def read(self, identity: ObjectIdentity, loader: Callable[[], bytes]) -> CacheRead:
        owner = False
        with self._condition:
            while True:
                hit = self._entries.get(identity)
                if hit is not None:
                    value, expires = hit
                    if self._clock() < expires:
                        self._entries.move_to_end(identity)
                        self._counters["hits"] += 1
                        _perf_observe("io.retained_cache_hits", 1)
                        return CacheRead(value, "cache")
                    self._drop(identity)
                    self._counters["expirations"] += 1
                future = self._inflight.get(identity)
                if future is not None:
                    self._counters["shared_reads"] += 1
                    break
                if len(self._inflight) < self.limits.max_inflight:
                    future = Future()
                    self._inflight[identity] = future
                    generation = self._generation
                    self._counters["misses"] += 1
                    self._counters["peak_inflight"] = max(
                        self._counters["peak_inflight"], len(self._inflight)
                    )
                    owner = True
                    break
                self._counters["capacity_waits"] += 1
                self._condition.wait()
        if not owner:
            _perf_observe("io.shared_cache_waits", 1)
            return CacheRead(future.result(), "shared")
        try:
            with _perf_measure("io.download"):
                data = loader()
            _perf_observe("io.downloaded_bytes", int(len(data)) if isinstance(data, bytes) else 0)
            with _perf_measure("io.checksum"):
                if not isinstance(data, bytes) or len(data) != identity.size:
                    raise RuntimeError("published artifact size differs")
                if hashlib.sha256(data).hexdigest() != identity.sha256:
                    raise RuntimeError("published artifact checksum differs")
            with self._condition:
                self._counters["store_bytes"] += len(data)
                if (generation == self._generation and self.limits.max_bytes > 0
                        and len(data) <= min(self.limits.max_bytes, self.limits.max_entry_bytes)):
                    while self._entries and (
                        self._bytes + len(data) > self.limits.max_bytes
                        or len(self._entries) >= self.limits.max_entries
                    ):
                        self._drop(next(iter(self._entries)))
                        self._counters["evictions"] += 1
                    self._entries[identity] = (data, self._clock() + self.limits.ttl_seconds)
                    self._bytes += len(data)
                    self._counters["peak_bytes"] = max(self._counters["peak_bytes"], self._bytes)
                else:
                    self._counters["bypasses"] += 1
                # Publish the Future while the entry is still marked in-flight.
                future.set_result(data)
                del self._inflight[identity]
                self._condition.notify_all()
            return CacheRead(data, "store")
        except BaseException as error:
            with self._condition:
                self._counters["errors"] += 1
                future.set_exception(error)
                self._inflight.pop(identity, None)
                self._condition.notify_all()
            raise

    def _drop(self, identity: ObjectIdentity) -> None:
        self._bytes -= len(self._entries.pop(identity)[0])

    def clear(self) -> None:
        """Invalidate retained bytes; a load begun before clear cannot refill it."""
        with self._condition:
            self._generation += 1
            self._entries.clear()
            self._bytes = 0

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            # Release expired memory even when traffic moves to another asset.
            now = self._clock()
            for key, (_, expires) in list(self._entries.items()):
                if expires <= now:
                    self._drop(key)
                    self._counters["expirations"] += 1
            return {**self._counters, "bytes": self._bytes, "entries": len(self._entries),
                    "inflight": len(self._inflight), "limit_bytes": self.limits.max_bytes,
                    "limit_inflight": self.limits.max_inflight}


_PROCESS_LOCK = threading.Lock()
_PROCESS_CACHE: VerifiedObjectCache | None = None
_PROCESS_PID = 0


def process_asset_cache() -> VerifiedObjectCache:
    """One cache/budget per process, not per client or per task.

    Environment changes require a worker restart. A post-fork child starts with
    a fresh cache and no inherited in-flight Future ownership.
    """
    global _PROCESS_CACHE, _PROCESS_PID
    with _PROCESS_LOCK:
        if _PROCESS_CACHE is None or _PROCESS_PID != os.getpid():
            _PROCESS_CACHE = VerifiedObjectCache(CacheLimits.from_environment())
            _PROCESS_PID = os.getpid()
        return _PROCESS_CACHE


def process_cache_metrics() -> dict[str, int]:
    return {"asset_cache_" + key: value for key, value in process_asset_cache().snapshot().items()}


def _after_fork_child() -> None:
    # Locks/Futures inherited from other parent threads must never be reused.
    global _PROCESS_LOCK, _PROCESS_CACHE, _PROCESS_PID
    _PROCESS_LOCK = threading.Lock()
    _PROCESS_CACHE = None
    _PROCESS_PID = 0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork_child)
