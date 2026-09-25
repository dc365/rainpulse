from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace

import numpy as np
import pytest
from perf_helpers import (
    Store,
    assert_arrays,
    network,
    reference,
    source,
    station,
    volume,
    write_network,
)

from rainpulse_algo.multiband.codec import decode_arrays, encode_volume
from rainpulse_algo.multiband.execution import ExecutionOptions as Options
from rainpulse_algo.multiband.managed import Executor
from rainpulse_algo.radar.qc_engine.group_stats import recurrence_tables, seed_tables


def request(v, net, mode):
    return {
        "event_type": "ops.multiband.requested.v1",
        "occurred_at": "2026-08-28T00:12:00Z",
        "payload": {
            "mode": mode,
            "sources": [source(v)],
            "product_id": "demo",
            "network_sha256": net.sha256,
            "input_cutoff": "2026-08-28T00:12:00Z",
            "analysis_time": "2026-08-28T00:06:00Z",
        },
    }


@pytest.mark.parametrize("mode", ["x_qc", "sx_composite"])
@pytest.mark.parametrize("schema", ["2.0", "3.0"])
@pytest.mark.parametrize("backend", ["numpy", "numba"])
def test_existing_executor_stream_integration(mode, schema, backend, tmp_path):
    if backend == "numba":
        pytest.importorskip("numba")
    st = station()
    v = volume(st, cuts=3, rays=45, gates=60)
    net = write_network(tmp_path / "net.json", replace(network(), cache_max_bytes=16 * 1024**2))
    store = Store(encode_volume(v), schema=schema, pack_bytes=1024 * 1024)
    req = request(v, net, mode)
    old = reference("managed").Executor(tmp_path / "net.json")
    expected, oldsummary, _ = old.execute(req, store.reader(), artifact_digest=lambda x: store.sha)
    executor = Executor(
        tmp_path / "net.json",
        execution=Options(
            streaming=True,
            scratch_parent=str(tmp_path),
            layer_memory_bytes=0,
            selection_backend=backend,
        ),
    )
    objects, summary, metrics = executor.execute(
        req, store.reader(), artifact_digest=lambda x: store.sha
    )
    assert set(objects) == set(expected)
    assert_arrays(
        decode_arrays(objects["arrays.npz"], maximum_bytes=1024**2),
        decode_arrays(expected["arrays.npz"], maximum_bytes=1024**2),
    )
    assert (
        objects["cr.png"] == expected["cr.png"]
        and objects["uncertain.png"] == expected["uncertain.png"]
    )
    manifest = json.loads(objects["manifest.json"])
    m0 = json.loads(expected["manifest.json"])
    receipt = manifest.pop("execution")
    assert receipt["streaming"]
    assert manifest == m0
    s = summary.copy()
    s.pop("execution")
    assert s == oldsummary
    assert metrics["qc_executions"] == 3
    assert not list(tmp_path.glob("rainpulse-multiband-*"))
    o2, _, m2 = executor.execute(req, store.reader(), artifact_digest=lambda x: store.sha)
    assert o2 == objects
    assert m2["decoded_cut_cache_hits"] == 3 and m2["qc_executions"] == 0
    assert not list(tmp_path.glob("rainpulse-multiband-*"))


def test_execution_identity_and_default_legacy(tmp_path):
    net = write_network(tmp_path / "net.json", network())
    v = volume()
    store = Store(encode_volume(v))
    req = request(v, net, "sx_composite")
    options = Options(streaming=True, scratch_parent=str(tmp_path))
    p = tmp_path / "exec.json"
    p.write_text(json.dumps(asdict(options)))
    executor = Executor(tmp_path / "net.json", execution_path=p)
    p.write_text(json.dumps(asdict(replace(options, maximum_sweeps=63))))
    with pytest.raises(ValueError, match="execution"):
        executor.execute(req, store.reader(), artifact_digest=lambda x: store.sha)
    a, _, _ = Executor(tmp_path / "net.json").execute(
        req, store.reader(), artifact_digest=lambda x: store.sha
    )
    b, _, _ = (
        reference("managed")
        .Executor(tmp_path / "net.json")
        .execute(req, store.reader(), artifact_digest=lambda x: store.sha)
    )
    assert a == b


@pytest.mark.parametrize(
    "bad",
    [
        {"maximum_sweeps": 65},
        {"maximum_cut_bytes": True},
        {"selection_backend": "gpu"},
        {"scratch_parent": "relative"},
        {"selection_backend": "numba"},
        {"maximum_task_gates": 1},
    ],
)
def test_options_refuse_unsafe(bad):
    with pytest.raises(ValueError):
        Options(**bad)


def test_execution_json_duplicate_and_unknown(tmp_path):
    p = tmp_path / "options.json"
    p.write_text('{"streaming":false,"streaming":true}')
    with pytest.raises(ValueError):
        Options.load(p)
    p.write_text('{"magic":true}')
    with pytest.raises(TypeError):
        Options.load(p)


@pytest.mark.parametrize("kind", ["recurrence", "seeds"])
@pytest.mark.parametrize("seed", range(10))
def test_grouped_count_exact_original_loops(kind, seed):
    rng = np.random.default_rng(seed)
    count = 31
    labels = rng.integers(0, count + 1, size=(37, 60), dtype="int32")
    first = rng.random(labels.shape) < 0.6
    second = rng.random(labels.shape) < 0.4
    expected = [
        np.zeros_like(labels),
        np.zeros(labels.shape, "uint32"),
        np.full(labels.shape, np.nan, "float32"),
        np.zeros(labels.shape, bool),
    ]
    for value in range(1, count + 1):
        component = labels == value
        members = int(component.sum())
        seeds = int((component & first).sum())
        fraction = (
            min(float(first[component].mean()), float(second[component].mean()))
            if kind == "recurrence"
            else seeds / members
        )
        expected[0][component] = value
        expected[1][component] = members
        expected[2][component] = fraction
        if kind == "recurrence":
            if members >= 5 and members <= 100 and fraction >= 0.4:
                expected[3] |= component
        elif seeds >= 3 and fraction >= 0.5 and members <= 100:
            expected[3] |= component & ~first
    got = (
        recurrence_tables(labels, count, first, second, 5, 100, 0.4)
        if kind == "recurrence"
        else seed_tables(labels, count, first, 3, 0.5, 100, True)
    )
    for a, b in zip(got, expected):
        assert np.array_equal(a, b, equal_nan=True)


def test_empty_gapped_labels_and_propagation_disabled():
    labels = np.array([[0, 2, 2], [0, 0, 2]], np.int32)
    m = np.ones(labels.shape, bool)
    o = seed_tables(labels, 4, m, 1, 0.1, 100, False)
    assert not o[3].any()
    assert np.isnan(o[2][labels == 0]).all()
    assert (o[1][labels == 2] == 3).all()
    o = recurrence_tables(np.zeros((2, 3), np.int32), 0, m, m, 1, 100, 0.1)
    assert not o[3].any()


def test_streaming_late_input_corruption_does_not_return_product(tmp_path):
    net = write_network(tmp_path / "net.json", network())
    v = volume()
    objects = encode_volume(v)
    store = Store(objects, schema="3.0", pack_bytes=4096)
    key = sorted(k for k in store.data if not k.endswith("_SUCCESS.json"))[-1]
    store.data[key] = b"X" * len(store.data[key])
    executor = Executor(
        tmp_path / "net.json", execution=Options(streaming=True, scratch_parent=str(tmp_path))
    )
    with pytest.raises(RuntimeError, match="checksum"):
        executor.execute(
            request(v, net, "x_qc"), store.reader(), artifact_digest=lambda x: store.sha
        )
    assert not list(tmp_path.glob("rainpulse-multiband-*"))


def test_40cut_native_managed_really_runs_same_executor(tmp_path):
    from rainpulse_algo.multiband.model import Volume
    from rainpulse_algo.multiband.stream_io import NativeStreamWriter

    net = write_network(tmp_path / "net.json", network(width=8, height=8))
    v = volume(cuts=1, rays=12, gates=16)
    w = NativeStreamWriter(tmp_path / "source.npz", 8 * 1024**2)
    for n in range(40):
        w.add(
            Volume(
                copy.deepcopy(v.metadata),
                [replace(v.sweeps[0], number=n, elevation_deg=np.full(12, 0.5 + n * 0.4))],
            )
        )
    o = w.finish()
    store = Store(
        {"arrays.npz": o["native_arrays.npz"], "volume.json": o["native_volume.json"]},
        schema="3.0",
        pack_bytes=128 * 1024,
    )
    executor = Executor(
        tmp_path / "net.json",
        execution=Options(streaming=True, scratch_parent=str(tmp_path), layer_memory_bytes=0),
    )
    objects, _, metrics = executor.execute(
        request(v, net, "x_qc"), store.reader(), artifact_digest=lambda x: store.sha
    )
    assert metrics["processed_cuts"] == 40 and metrics["qc_executions"] == 40
    assert len(json.loads(objects["native_volume.json"])["sweeps"]) == 40
    assert len(json.loads(objects["manifest.json"])["sources"]) == 40
    assert not list(tmp_path.glob("rainpulse-multiband-*"))
    assert metrics["packed_staged_bytes"] > 0


def test_cache_byte_entry_limits_and_expiry():
    from rainpulse_algo.multiband.managed import VolumeCache

    t = [0.0]
    v = volume(cuts=1, rays=4, gates=5)
    c = VolumeCache(v.nbytes * 2, 10, clock=lambda: t[0], maximum_entries=1)
    c.put(("a",), v)
    c.put(("b",), copy.deepcopy(v))
    assert c.get(("a",)) is None
    assert c.get(("b",)) is not None
    t[0] = 11.0
    assert c.get(("b",)) is None and c.bytes == 0
