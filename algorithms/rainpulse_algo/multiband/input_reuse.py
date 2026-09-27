"""Reuse a completely verified source only when every QC cut is still cached.

No raw bytes or numerical arrays are retained here. The existing cut cache owns
all arrays and its TTL/byte budget stays authoritative. The caller MUST open a
fresh verified ArtifactSession first. No marker or incomplete-source receipt is
cached. A partial hit takes the normal staging/validation path.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass


def source_key(executor, session, source, *, purpose):
    # Includes the physical AND logical index. A changed schema-3 logical index
    # cannot borrow a receipt merely because it repeats an old aggregate hash.
    index = session.index
    manifest = {
        "schema": index.schema, "sha256": index.sha256,
        "data_prefix": index.data_prefix,
        "physical": [(k, v.size, v.sha256) for k, v in sorted(index.physical.items())],
        "logical": sorted(index.logical.items()),
    }
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True,
                            separators=(",", ":")).encode()).hexdigest()
    return (purpose, getattr(session, "namespace", ""), getattr(session, "bucket", ""),
            getattr(session, "prefix", ""), digest,
            executor.network.sha256, executor.execution_policy_sha256,
            executor.flag_version, executor.reject_mask,
            tuple((k, source[k]) for k in ("input_uri", "radar_id", "scan_id",
                  "available_at", "volume_start", "volume_end")))


@dataclass(frozen=True)
class Receipt:
    cache_keys: tuple
    gate_counts: tuple
    metadata_hashes: tuple
    expires: float


class SourceInventory:
    def __init__(self, maximum_entries=32):
        if type(maximum_entries) is not int or not 1 <= maximum_entries <= 128:
            raise ValueError("invalid source inventory entry budget")
        self.maximum = maximum_entries
        self.entries = OrderedDict()

    def _expire(self, now):
        for key, receipt in list(self.entries.items()):
            if now >= receipt.expires:
                del self.entries[key]

    def remember(self, identity, cache, cache_keys, gate_counts):
        """Call only AFTER exhausting and validating a whole source iterator."""
        now = cache.clock()
        self._expire(now)
        keys, gates = tuple(cache_keys), tuple(map(int, gate_counts))
        if not keys or len(keys) > 64 or len(set(keys)) != len(keys) or len(gates) != len(keys):
            raise ValueError("invalid complete cut inventory")
        if any(n <= 0 for n in gates):
            raise ValueError("invalid source gate count")
        if cache.maximum == 0:
            return False
        expirations = []
        metadata_hashes = []
        for key, count in zip(keys, gates, strict=True):
            entry = cache.entries.get(key)
            if entry is None or now >= entry[0]:
                return False
            value = entry[1]
            if len(value.sweeps) != 1 or value.sweeps[0].fields["DBZH"].size != count:
                raise ValueError("cached cut differs from complete source receipt")
            expirations.append(entry[0])
            metadata_hashes.append(_metadata_hash(value))
        self.entries[identity] = Receipt(keys, gates, tuple(metadata_hashes), min(expirations))
        self.entries.move_to_end(identity)
        while len(self.entries) > self.maximum:
            self.entries.popitem(last=False)
        return True

    def lookup(self, identity, cache, *, maximum_cuts, maximum_gates, maximum_cut_bytes):
        now = cache.clock()
        self._expire(now)
        receipt = self.entries.get(identity)
        if receipt is None or cache.maximum == 0:
            return None
        if len(receipt.cache_keys) > maximum_cuts or sum(receipt.gate_counts) > maximum_gates:
            raise ValueError("cached source exceeds current cut/gate budget")
        # Existing worker is a single numerical lane. Only the cache retains
        # arrays; this tuple pins at most its existing bounded working set.
        values = []
        for key, gates, meta_hash in zip(
                receipt.cache_keys, receipt.gate_counts, receipt.metadata_hashes, strict=True):
            value = cache.get(key)
            if value is None:
                self.entries.pop(identity, None)
                return None
            if (len(value.sweeps) != 1 or value.sweeps[0].fields["DBZH"].size != gates
                    or value.nbytes > maximum_cut_bytes):
                raise ValueError("cached source contract changed")
            if _metadata_hash(value) != meta_hash:
                raise ValueError("cached source metadata changed")
            for s in value.sweeps:
                for a in (s.azimuth_deg, s.range_m, s.elevation_deg,
                          s.ray_time_epoch, *s.fields.values()):
                    if a.flags.writeable:
                        raise ValueError("cached source is no longer read-only")
            values.append(value)
        self.entries.move_to_end(identity)
        return tuple(values)


def _metadata_hash(volume):
    return hashlib.sha256(json.dumps(volume.metadata,sort_keys=True,
                          separators=(",", ":"),allow_nan=False).encode()).hexdigest()


def inventory(executor):
    value = getattr(executor, "_verified_source_inventory", None)
    if value is None:
        value = SourceInventory()
        executor._verified_source_inventory = value
    return value


def cached_source(executor, session, source, *, purpose, maximum_cuts, maximum_gates, metrics):
    identity = source_key(executor, session, source, purpose=purpose)
    result = inventory(executor).lookup(identity, executor.cut_cache,
        maximum_cuts=maximum_cuts, maximum_gates=maximum_gates,
        maximum_cut_bytes=executor.execution.maximum_qc_cut_bytes)
    name = "verified_source_cache_hits" if result is not None else "verified_source_cache_misses"
    metrics[name] = metrics.get(name, 0) + 1
    if result is not None:
        metrics["verified_source_reused_cuts"] = (
            metrics.get("verified_source_reused_cuts", 0) + len(result))
        metrics["decoded_cut_cache_hits"] += len(result)
    return identity, result


def remember_source(executor, identity, base_key, numbers, gate_counts, metrics):
    complete = inventory(executor).remember(identity, executor.cut_cache,
                  [(*base_key, n) for n in numbers], gate_counts)
    if complete:
        metrics["verified_source_receipts"] = metrics.get("verified_source_receipts", 0) + 1


def _observe(name, value):
    try:
        from rainpulse_algo.performance import observe
        observe(name, value)
    except Exception:
        pass
