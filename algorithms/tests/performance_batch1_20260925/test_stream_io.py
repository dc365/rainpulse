from __future__ import annotations

import copy
import hashlib
import io
import json
from dataclasses import replace
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest
from perf_helpers import Store, assert_arrays, station, volume

from rainpulse_algo.multiband.codec import encode_volume
from rainpulse_algo.multiband.execution import ExecutionOptions as Options
from rainpulse_algo.multiband.model import Volume
from rainpulse_algo.multiband.stream_io import (
    STREAM_CONTRACT,
    GroupCuts,
    NativeStreamWriter,
    NPZCuts,
)


def source(v):
    m = v.metadata
    return {
        "radar_id": m["radar_id"],
        "scan_id": m["scan_id"],
        "input_uri": "s3://test/sample",
        **{k: m[k] for k in ("volume_start", "volume_end", "available_at")},
    }


@pytest.mark.parametrize("schema", ["1.0", "2.0", "3.0"])
def test_staging_checks_and_readonly(schema, tmp_path):
    store = Store({"a": b"A" * 12, "b": b"B" * 10, "c": b"C" * 8}, schema=schema, pack_bytes=16)
    s = store.reader().open("s3://test/sample")
    with s.staged(
        directory=tmp_path, maximum_disk_bytes=1024, maximum_object_bytes=1024, keys=["b"]
    ) as view:
        assert list(view) == ["b"]
        assert view["b"] == store.logical["b"]
        with pytest.raises(TypeError):
            view["b"] = b"x"
        with pytest.raises(KeyError):
            view["a"]
        view.copy_logical_to("b", tmp_path / "copied")
        assert (tmp_path / "copied").read_bytes() == b"B" * 10
        assert view.stats["packed_full_verified"] == int(schema == "3.0")
        assert view.stats["peak_object_bytes"] <= 16
    with pytest.raises(RuntimeError):
        view["b"]
    assert not list(tmp_path.glob("rp-verified-*"))
    reads = [x for x in store.calls if not x.endswith("_SUCCESS.json")]
    assert ("sample/a" not in reads) if schema != "3.0" else len(set(reads)) == 3


@pytest.mark.parametrize("schema", ["2.0", "3.0"])
def test_corruption_and_cleanup(schema, tmp_path):
    store = Store({"a": b"A" * 10, "b": b"B" * 10}, schema=schema, pack_bytes=10)
    key = "sample/a" if schema == "2.0" else "sample/pack0"
    store.data[key] = b"Z" * 10
    s = store.reader().open("s3://test/sample")
    with pytest.raises(RuntimeError, match="checksum"):
        with s.staged(
            directory=tmp_path, maximum_disk_bytes=100, maximum_object_bytes=100, keys=["a"]
        ) as v:
            v["a"]
    assert not list(tmp_path.glob("rp-verified-*"))


def test_schema3_verifies_unselected_and_logical_hash(tmp_path):
    store = Store({"a": b"A" * 10, "b": b"B" * 10}, schema="3.0", pack_bytes=10)
    store.data["sample/pack1"] = b"Z" * 10
    s = store.reader().open("s3://test/sample")
    with pytest.raises(RuntimeError, match="checksum"):
        with s.staged(
            directory=tmp_path, maximum_disk_bytes=100, maximum_object_bytes=100, keys=["a"]
        ):
            pytest.fail("unselected corrupt pack was exposed")
    store = Store({"a": b"A" * 10}, schema="3.0")
    store.marker["sha256"] = "f" * 64
    store.refresh()
    s = store.reader().open("s3://test/sample")
    with pytest.raises(RuntimeError, match="bundle checksum"):
        with s.staged(directory=tmp_path, maximum_disk_bytes=100, maximum_object_bytes=100):
            pass


@pytest.mark.parametrize("object_cap,disk_cap", [(9, 100), (100, 9)])
def test_pack_rejects_before_download(tmp_path, object_cap, disk_cap):
    store = Store({"a": b"A" * 10}, schema="3.0")
    s = store.reader().open("s3://test/sample")
    with pytest.raises(ValueError):
        with s.staged(
            directory=tmp_path, maximum_disk_bytes=disk_cap, maximum_object_bytes=object_cap
        ):
            pass
    assert store.calls == ["sample/_SUCCESS.json"]


def test_each_session_checks_fresh_marker(tmp_path):
    store = Store({"a": b"good"})
    reader = store.reader()
    reader.open("s3://test/sample")
    store.data["sample/_SUCCESS.json"] = b"bad"
    with pytest.raises(RuntimeError):
        reader.open("s3://test/sample")
    assert store.calls == ["sample/_SUCCESS.json"] * 2


@pytest.mark.parametrize("contract", ["legacy", "stream"])
def test_npz_cut_roundtrip(contract, tmp_path):
    v = volume(cuts=4)
    st = station()
    objects = encode_volume(v)
    if contract == "stream":
        writer = NativeStreamWriter(tmp_path / "writer.npz", 4 * 1024**2)
        for s in v.sweeps:
            writer.add(Volume(copy.deepcopy(v.metadata), [s]))
        out = writer.finish()
        objects = {"volume.json": out["native_volume.json"], "arrays.npz": out["native_arrays.npz"]}
        assert json.loads(objects["volume.json"])["contract"] == STREAM_CONTRACT
    path = tmp_path / "arrays.npz"
    path.write_bytes(objects["arrays.npz"])
    reader = NPZCuts(
        objects["volume.json"],
        path,
        st,
        source(v),
        sha256="a" * 64,
        options=Options(streaming=True),
        maximum_bytes=64 * 1024**2,
    )
    try:
        assert reader.numbers == [0, 1, 2, 3]
        for n in reader.numbers:
            x = reader.read(n)
            x.validate(st)
            assert_arrays(x.sweeps[0].fields, v.sweeps[n].fields)
            assert x.metadata == v.metadata
    finally:
        reader.close()


def test_native_stream_supports_40cuts_without_eager_limit_change(tmp_path):
    from rainpulse_algo.multiband.model import MAX_SWEEPS

    assert MAX_SWEEPS == 32
    v = volume(cuts=1, rays=6, gates=10)
    w = NativeStreamWriter(tmp_path / "v.npz", 1024**2)
    for n in range(40):
        w.add(Volume(copy.deepcopy(v.metadata), [replace(v.sweeps[0], number=n)]))
    out = w.finish()
    p = tmp_path / "v.npz"
    reader = NPZCuts(
        out["native_volume.json"],
        p,
        station(),
        source(v),
        sha256="a" * 64,
        options=Options(streaming=True),
        maximum_bytes=1024**2,
    )
    assert len(reader.numbers) == 40
    for n in reader.numbers:
        reader.read(n).validate(station())
    reader.close()
    m = json.loads(out["native_volume.json"])
    m["contract"] = "rainpulse.multiband.native-v1"
    with pytest.raises(ValueError, match="count"):
        NPZCuts(
            json.dumps(m).encode(),
            p,
            station(),
            source(v),
            sha256="a" * 64,
            options=Options(streaming=True),
            maximum_bytes=1024**2,
        )


def test_writer_deterministic_and_budget(tmp_path):
    v = volume(cuts=2, rays=8, gates=12)
    result = []
    for k in range(2):
        w = NativeStreamWriter(tmp_path / f"{k}.npz", 1024**2)
        for s in v.sweeps:
            w.add(Volume(v.metadata, [s]))
        result.append(w.finish())
    assert result[0] == result[1]
    w = NativeStreamWriter(tmp_path / "small.npz", 100)
    try:
        with pytest.raises(ValueError, match="budget"):
            w.add(Volume(v.metadata, [v.sweeps[0]]))
    finally:
        try:
            w.close()
        except ValueError:
            pass


@pytest.mark.parametrize("bad", ["hash", "duplicate", "object", "size", "trailing", "manifest"])
def test_npz_rejects_invalid_before_materialization(tmp_path, bad):
    v = volume(cuts=1, rays=4, gates=5)
    o = encode_volume(v)
    meta = json.loads(o["volume.json"])
    raw = o["arrays.npz"]
    if bad == "hash":
        meta["arrays_sha256"] = "f" * 64
    elif bad == "manifest":
        meta["sweeps"][0]["fields"].append("nonexistent")
    else:
        with ZipFile(io.BytesIO(raw)) as a:
            entries = [(x.filename, a.read(x)) for x in a.infolist()]
        key = "s0_DBZH.npy"
        if bad == "duplicate":
            entries.append(entries[0])
        elif bad == "object":
            buf = io.BytesIO()
            np.lib.format.write_array(buf, np.ones((4, 5), dtype=object), allow_pickle=True)
            entries = [(n, buf.getvalue() if n == key else data) for n, data in entries]
        elif bad == "size":
            buf = io.BytesIO()
            np.lib.format.write_array_header_1_0(
                buf, {"descr": "<f4", "fortran_order": False, "shape": (4096, 1000000)}
            )
            entries = [(n, buf.getvalue() if n == key else data) for n, data in entries]
        elif bad == "trailing":
            entries = [(n, data + b"x" if n == key else data) for n, data in entries]
        out = io.BytesIO()
        with ZipFile(out, "w", compression=ZIP_DEFLATED) as a:
            for n, data in entries:
                a.writestr(n, data)
        raw = out.getvalue()
        meta["arrays_sha256"] = hashlib.sha256(raw).hexdigest()
    p = tmp_path / "v.npz"
    p.write_bytes(raw)
    with pytest.raises(ValueError):
        NPZCuts(
            json.dumps(meta).encode(),
            p,
            station(),
            source(v),
            sha256="a" * 64,
            options=Options(streaming=True),
            maximum_bytes=1024**2,
        )


class Array:
    def __init__(self, a):
        self.a = np.asarray(a)
        self.shape = self.a.shape
        self.dtype = self.a.dtype
        self.attrs = {}
        self.reads = 0

    def __getitem__(self, sl):
        self.reads += 1
        return self.a[sl]


class Group(dict):
    attrs = {}

    def array_keys(self):
        return list(self)


def root_fixture(v, st):
    root = Group()
    root.attrs = {
        "contract_name": "rainpulse.normalized-radar-volume",
        "radar_id": st.radar_id,
        "scan_id": v.metadata["scan_id"],
    }
    root["sweep_number"] = Array(np.array([s.number for s in v.sweeps], np.int32))
    for s in v.sweeps:
        g = Group({k: Array(a) for k, a in s.fields.items()})
        for k, a in (
            ("azimuth", s.azimuth_deg),
            ("range", s.range_m),
            ("elevation", s.elevation_deg),
            ("ray_time", s.ray_time_epoch),
        ):
            g[k] = Array(a)
        root[f"sweep_{s.number:03d}"] = g
    return root


def test_group_preflight_does_not_materialize_all_cuts():
    st = replace(station(), source="normalized_zarr")
    v = volume(st, cuts=40, rays=8, gates=8)
    r = root_fixture(v, st)
    c = GroupCuts(
        r, st, source(v), sha256="a" * 64, options=Options(streaming=True), maximum_bytes=1024**2
    )
    assert len(c.numbers) == 40
    assert all(r[f"sweep_{i:03d}"]["DBZH"].reads == 0 for i in range(40))
    x = c.read(3)
    x.validate(st)
    assert r["sweep_003"]["DBZH"].reads > 0
    assert r["sweep_004"]["DBZH"].reads == 0


def test_real_zarr_roundtrip(tmp_path):
    zarr = pytest.importorskip(
        "zarr",
        reason="actual locked Zarr unavailable; fake protocol tests are not Zarr integration",
    )
    if int(zarr.__version__.split(".")[0]) != 2:
        pytest.fail("requires deployed Zarr 2")
    from zarr.storage import KVStore

    st = replace(station(), source="normalized_zarr")
    v = volume(st, cuts=2, rays=8, gates=10)
    store = {}
    r = zarr.group(store=store)
    r.attrs.update(
        contract_name="rainpulse.normalized-radar-volume",
        radar_id=st.radar_id,
        scan_id=v.metadata["scan_id"],
    )
    r.create_dataset("sweep_number", data=np.arange(2, dtype="int32"))
    for s in v.sweeps:
        g = r.create_group(f"sweep_{s.number:03d}")
        for k, a in {
            **s.fields,
            "azimuth": s.azimuth_deg,
            "range": s.range_m,
            "elevation": s.elevation_deg,
            "ray_time": s.ray_time_epoch,
        }.items():
            g.create_dataset(k, data=a)
    provider = Store({k: bytes(data) for k, data in store.items()})
    session = provider.reader().open("s3://test/sample")
    with session.staged(
        directory=tmp_path, maximum_disk_bytes=8 * 1024**2, maximum_object_bytes=8 * 1024**2
    ) as mapping:
        group = zarr.open_group(store=KVStore(mapping), mode="r")
        cuts = GroupCuts(
            group,
            st,
            source(v),
            sha256="a" * 64,
            options=Options(streaming=True),
            maximum_bytes=8 * 1024**2,
        )
        for n in cuts.numbers:
            cuts.read(n).validate(st)


def test_metadata_reused_only_inside_frozen_session(tmp_path):
    store = Store({".zattrs": b"{}", "sweep_000/DBZH/.zarray": b"{}", "sweep_000/DBZH/0.0": b"abc"})
    for _ in range(2):
        session = store.reader().open("s3://test/sample")
        with session.staged(
            directory=tmp_path, maximum_disk_bytes=1000, maximum_object_bytes=100
        ) as m:
            assert m[".zattrs"] == m[".zattrs"] == b"{}"
            assert m.stats["metadata_cache_hits"] == 1
            assert m.stats["metadata_cache_peak_bytes"] <= 100
    assert store.calls.count("sample/.zattrs") == 2
    assert store.calls.count("sample/_SUCCESS.json") == 2
