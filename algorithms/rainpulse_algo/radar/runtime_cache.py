from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock
from typing import Generic, TypeVar

ValueT = TypeVar("ValueT")


@dataclass(frozen=True)
class CacheLookup(Generic[ValueT]):
    value: ValueT
    hit: bool
    cached: bool
    size_bytes: int


@dataclass(frozen=True)
class _CacheEntry(Generic[ValueT]):
    value: ValueT
    size_bytes: int


class ByteBudgetLRUCache(Generic[ValueT]):
    def __init__(self, budget_bytes: int) -> None:
        if budget_bytes <= 0:
            raise ValueError("cache budget must be positive")
        self._budget_bytes = int(budget_bytes)
        self._resident_bytes = 0
        self._entries: OrderedDict[str, _CacheEntry[ValueT]] = OrderedDict()
        self._pending: dict[str, Future[tuple[ValueT, int, bool]]] = {}
        self._lock = Lock()

    @property
    def budget_bytes(self) -> int:
        return self._budget_bytes

    @property
    def resident_bytes(self) -> int:
        with self._lock:
            return self._resident_bytes

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._resident_bytes = 0
            self._pending.clear()

    def get_or_compute(
        self,
        key: str,
        *,
        factory: Callable[[], ValueT],
        size_of: Callable[[ValueT], int],
        freeze: Callable[[ValueT], ValueT] | None = None,
    ) -> CacheLookup[ValueT]:
        if not key:
            raise ValueError("cache key must not be empty")
        if freeze is None:
            def freeze(value: ValueT) -> ValueT:
                return value

        pending: Future[tuple[ValueT, int, bool]] | None = None
        with self._lock:
            cached = self._entries.pop(key, None)
            if cached is not None:
                self._entries[key] = cached
                return CacheLookup(
                    value=cached.value,
                    hit=True,
                    cached=True,
                    size_bytes=cached.size_bytes,
                )
            pending = self._pending.get(key)
            if pending is None:
                pending = Future()
                self._pending[key] = pending
                owner = True
            else:
                owner = False

        if not owner:
            value, size_bytes, is_cached = pending.result()
            return CacheLookup(
                value=value,
                hit=True,
                cached=is_cached,
                size_bytes=size_bytes,
            )

        try:
            value = freeze(factory())
            size_bytes = max(0, int(size_of(value)))
            is_cached = False

            with self._lock:
                if size_bytes <= self._budget_bytes:
                    while self._entries and self._resident_bytes + size_bytes > self._budget_bytes:
                        _, evicted = self._entries.popitem(last=False)
                        self._resident_bytes -= evicted.size_bytes
                    if self._resident_bytes + size_bytes <= self._budget_bytes:
                        self._entries[key] = _CacheEntry(value=value, size_bytes=size_bytes)
                        self._resident_bytes += size_bytes
                        is_cached = True
                pending.set_result((value, size_bytes, is_cached))
                self._pending.pop(key, None)
            return CacheLookup(
                value=value,
                hit=False,
                cached=is_cached,
                size_bytes=size_bytes,
            )
        except Exception as error:  # noqa: BLE001 - propagate factory failures to waiters
            with self._lock:
                pending.set_exception(error)
                self._pending.pop(key, None)
            raise
