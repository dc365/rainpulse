import hashlib
import json
from types import SimpleNamespace as NS

import numpy as np
import pytest

from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.input_reuse import (
    SourceInventory,
    source_key,
)
from rainpulse_algo.multiband.managed import VolumeCache
from rainpulse_algo.multiband.model import Sweep, Volume
from rainpulse_algo.worker.asset_access import VerifiedArtifactReader, artifact_digest
from rainpulse_algo.worker.asset_cache import CacheLimits, VerifiedObjectCache


def fake_store(schema="2.0"):
    objects = {"a": b"abc", "b": b"xyz"}
    sha = artifact_digest(objects)
    physical = objects if schema == "2.0" else {"p": b"abcxyz"}
    marker = dict(
        schema_version=schema,
        sha256=sha,
        size_bytes=sum(map(len, physical.values())),
        objects=[
            dict(key=k, size_bytes=len(v), sha256=hashlib.sha256(v).hexdigest())
            for k, v in physical.items()
        ],
    )
    if schema == "3.0":
        marker["packed_entries"] = [["a", "p", 0, 3], ["b", "p", 3, 3]]
    data = {"sample/" + k: v for k, v in physical.items()}
    calls = []

    def refresh():
        data["sample/_SUCCESS.json"] = json.dumps(marker).encode()

    refresh()

    def read(bucket, key, limit):
        calls.append(key)
        if key not in data:
            raise FileNotFoundError(key)
        b = data[key]
        if len(b) > limit:
            raise ValueError("transport budget")
        return b

    reader = VerifiedArtifactReader(
        read, namespace="test", workers=1, cache=VerifiedObjectCache(CacheLimits(max_bytes=0))
    )
    return reader, data, calls, marker, refresh


def setup():
    clock = [0.0]
    cache = VolumeCache(1000000, 60, clock=lambda: clock[0])
    e = NS(
        network=NS(sha256="f" * 64),
        execution=ExecutionOptions(streaming=True),
        execution_policy_sha256="e" * 64,
        flag_version="flag",
        reject_mask=7,
        cut_cache=cache,
    )
    source = dict(
        input_uri="s3://bucket/sample",
        radar_id="x1",
        scan_id="scan1",
        available_at="end",
        volume_start="start",
        volume_end="end",
    )
    for n in range(2):
        s = Sweep(
            n,
            np.arange(3.0),
            np.arange(4.0),
            np.zeros(3),
            np.zeros(3),
            {"DBZH": np.ones((3, 4), "float32")},
        )
        cache.put(("key", n), Volume({"scan_id": "scan1", "n": n}, [s]))
    return e, source, clock


@pytest.mark.parametrize("schema", ["2.0", "3.0"])
def test_full_verified_receipt_reuses_only_after_fresh_marker(tmp_path, schema):
    reader, data, calls, marker, refresh = fake_store(schema)
    e, source, _ = setup()
    first = reader.open(source["input_uri"])
    with first.staged(
        directory=tmp_path, maximum_disk_bytes=1000, maximum_object_bytes=1000
    ) as mapping:
        assert mapping["a"] == b"abc" and mapping["b"] == b"xyz"
    identity = source_key(e, first, source, purpose="x_qc")
    inventory = SourceInventory()
    assert inventory.remember(identity, e.cut_cache, [("key", 0), ("key", 1)], [12, 12])
    calls.clear()
    fresh = reader.open(source["input_uri"])
    values = inventory.lookup(
        source_key(e, fresh, source, purpose="x_qc"),
        e.cut_cache,
        maximum_cuts=64,
        maximum_gates=100,
        maximum_cut_bytes=1000000,
    )
    assert len(values) == 2 and calls == ["sample/_SUCCESS.json"]
    del data["sample/_SUCCESS.json"]
    with pytest.raises(FileNotFoundError):
        reader.open(source["input_uri"])


@pytest.mark.parametrize("change", ["expire", "partial", "disabled", "replacement_identity"])
def test_miss_does_not_invent_partial_result(change):
    reader, _, _, _, _ = fake_store()
    e, source, clock = setup()
    session = reader.open(source["input_uri"])
    key = source_key(e, session, source, purpose="x_qc")
    cache = SourceInventory()
    assert cache.remember(key, e.cut_cache, [("key", 0), ("key", 1)], [12, 12])
    if change == "expire":
        clock[0] = 70
    elif change == "partial":
        e.cut_cache.entries.pop(("key", 1))
    elif change == "disabled":
        e.cut_cache.maximum = 0
    else:
        key = (*key, "different")
    assert (
        cache.lookup(
            key, e.cut_cache, maximum_cuts=64, maximum_gates=100, maximum_cut_bytes=1000000
        )
        is None
    )


def test_manifest_index_change_cannot_borrow_proof_even_same_sha():
    reader, _, _, _, _ = fake_store("3.0")
    e, src, _ = setup()
    s = reader.open(src["input_uri"])
    a = source_key(e, s, src, purpose="x_qc")
    s.index.logical = {"a": ("p", 3, 3), "b": ("p", 0, 3)}
    assert source_key(e, s, src, purpose="x_qc") != a
    e.execution_policy_sha256 = "9" * 64
    assert source_key(e, s, src, purpose="x_qc") != a


def test_budget_mutable_metadata_and_arrays_refused():
    reader, _, _, _, _ = fake_store()
    e, src, _ = setup()
    s = reader.open(src["input_uri"])
    key = source_key(e, s, src, purpose="x_qc")
    c = SourceInventory()
    c.remember(key, e.cut_cache, [("key", 0), ("key", 1)], [12, 12])
    with pytest.raises(ValueError, match="budget"):
        c.lookup(key, e.cut_cache, maximum_cuts=1, maximum_gates=100, maximum_cut_bytes=10**6)
    e.cut_cache.entries[("key", 0)][1].metadata["changed"] = True
    with pytest.raises(ValueError, match="metadata"):
        c.lookup(key, e.cut_cache, maximum_cuts=3, maximum_gates=100, maximum_cut_bytes=10**6)
    del e.cut_cache.entries[("key", 0)][1].metadata["changed"]
    e.cut_cache.entries[("key", 0)][1].sweeps[0].fields["DBZH"].setflags(write=True)
    with pytest.raises(ValueError, match="read-only"):
        c.lookup(key, e.cut_cache, maximum_cuts=3, maximum_gates=100, maximum_cut_bytes=10**6)


def test_receipt_only_remembers_complete_cached_inventory():
    e, _, _ = setup()
    c = SourceInventory(1)
    assert not c.remember("bad", e.cut_cache, [("key", 0), ("absent", 1)], [12, 12])
    assert not c.entries
    for i in range(4):
        assert c.remember(str(i), e.cut_cache, [("key", 0), ("key", 1)], [12, 12])
    assert list(c.entries) == ["3"]


@pytest.mark.parametrize("schema", ["2.0", "3.0"])
def test_first_verification_still_rejects_corrupt_payload(tmp_path, schema):
    reader, data, _, marker, _ = fake_store(schema)
    key = "sample/" + marker["objects"][0]["key"]
    data[key] = b"!" * len(data[key])
    session = reader.open("s3://bucket/sample")
    with pytest.raises(RuntimeError):
        with session.staged(
            directory=tmp_path, maximum_disk_bytes=1000, maximum_object_bytes=1000
        ) as mapping:
            mapping["a"]
    assert not list(tmp_path.iterdir())
