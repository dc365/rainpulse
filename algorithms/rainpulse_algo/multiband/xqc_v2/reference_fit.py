"""Bounded, call-local reference fits; never cache held-out target decisions."""

from collections import OrderedDict
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReferenceFit:
    slope: float
    power: float
    reference_gates: int
    reference_span: float
    bounds: tuple[float, float, float, float] | None = None


class ReferenceFits:
    def __init__(self, stats, maximum_bytes):
        self.stats = stats
        self.maximum_bytes = max(0, maximum_bytes)
        self.values = OrderedDict()
        self.bytes = 0

    def get(self, key, factory):
        if key in self.values:
            self.stats.fit_cache_hits += 1
            self.values.move_to_end(key)
            return self.values[key]
        self.stats.fit_cache_misses += 1
        self.stats.trial()
        value = factory()
        self.put(key, value)
        return value

    def put(self, key, value):
        # Conservative bound for immutable key, slotted scalars, tuple/float
        # objects and OrderedDict bookkeeping. No native arrays are retained.
        size = 768 + len(key)
        if key in self.values:
            del self.values[key]
            self.bytes -= size
        if size > self.maximum_bytes:
            return
        while self.bytes + size > self.maximum_bytes:
            old, _ = self.values.popitem(last=False)
            self.bytes -= 768 + len(old)
            self.stats.fit_cache_evictions += 1
        self.values[key] = value
        self.bytes += size
        self.stats.fit_cache_peak_bytes = max(self.stats.fit_cache_peak_bytes, self.bytes)
