import numpy as np
import pytest
from perf_helpers import assert_arrays, clone, cuts, network, qc, reference, station, volume

from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.fusion import (
    build_composite,
    footprint,
    ground_geometry,
    prepare_polar,
)
from rainpulse_algo.multiband.model import Volume, epoch
from rainpulse_algo.multiband.quality import _mask, accept_s_qc, x_qc
from rainpulse_algo.multiband.stream_fusion import build_composite_streaming


@pytest.mark.parametrize("mode", ["none", "upstream_verified", "phidp_linear"])
@pytest.mark.parametrize("dtype", ["float32", "float64"])
@pytest.mark.parametrize("missing", [None, "SNRH", "RHOHV", "PHIDP"])
def test_qc_exact_reference(mode, dtype, missing):
    st = station(attenuation=mode)
    v = volume(st, cuts=2, rays=24, gates=65, dtype=dtype)
    if missing:
        for s in v.sweeps:
            s.fields.pop(missing)
    before = clone(v)
    expected = reference("quality").x_qc(clone(v), st, "b" * 64)
    actual = x_qc(v, st, "b" * 64)
    assert actual.metadata == expected.metadata
    for old, new, ref, raw in zip(v.sweeps, actual.sweeps, expected.sweeps, before.sweeps):
        assert_arrays({key: new.fields[key] for key in ref.fields}, ref.fields)
        assert new.fields["QC_ACTION"].shape == raw.fields["DBZH"].shape
        assert new.fields["DBZH_QC_DISPLAY"].shape == raw.fields["DBZH"].shape
        assert_arrays(old.fields, raw.fields)
        assert np.shares_memory(new.fields["DBZH"], old.fields["DBZH"])
        assert not new.fields["DBZH"].flags.writeable
        assert np.shares_memory(new.azimuth_deg, old.azimuth_deg)
    if mode == "upstream_verified":
        assert all(s.fields["ATTENUATION_VALID_MASK"].all() for s in v.sweeps)


def test_s_reuses_immutable_fields():
    st = station(band="S")
    v = volume(st)
    v.sweeps[0].fields["CR_WITHHELD_MASK"] = np.zeros(v.sweeps[0].fields["DBZH"].shape, "uint8")
    v.sweeps[0].fields["CR_WITHHELD_MASK"][0, 2] = 1
    old = reference("quality").accept_s_qc(clone(v), st, "b" * 64)
    new = accept_s_qc(v, st, "b" * 64)
    for a, b, c in zip(new.sweeps, old.sweeps, v.sweeps):
        assert_arrays(a.fields, b.fields)
        assert np.shares_memory(a.fields["DBZH_QC"], c.fields["DBZH_QC"])
    assert new.sweeps[0].fields["REFLECTIVITY_ELIGIBLE_FOR_CR"][0, 2] == 0


def test_mask_does_not_allocate_default(monkeypatch):
    a = np.ones((4, 5), bool)

    def fail(*a, **k):
        raise AssertionError("unnecessary default allocation")

    monkeypatch.setattr(np, "full", fail)
    assert _mask({"A": a}, "A", a.shape) is a


@pytest.mark.parametrize("tile", [1, 4, 19])
@pytest.mark.parametrize("spilled", [True, False])
@pytest.mark.parametrize("backend", ["numpy", "numba"])
def test_fusion_all_fields_exact(tmp_path, tile, spilled, backend):
    if backend == "numba":
        pytest.importorskip("numba", reason="optional backend not installed")
    stations = [station("s1", band="S"), station("x2", longitude=120.03)]
    net = network(stations, tile_rows=tile)
    vs = [qc(volume(st, cuts=3, seed=i + 9), st, net) for i, st in enumerate(stations)]
    expected = reference("fusion").build_composite(
        list(reversed(vs)), net, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:10:00Z"
    )
    eager = build_composite(vs, net, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:10:00Z")
    stats = {}
    options = ExecutionOptions(
        streaming=True, selection_backend=backend, layer_memory_bytes=0 if spilled else 64 * 1024**2
    )
    actual = build_composite_streaming(
        cuts(vs),
        net,
        "demo",
        "2026-08-28T00:06:00Z",
        "2026-08-28T00:10:00Z",
        options=options,
        directory=tmp_path,
        metrics=stats,
    )
    assert_arrays(eager.arrays, expected.arrays)
    assert_arrays(actual.arrays, expected.arrays)
    assert eager.metadata == expected.metadata == actual.metadata
    assert stats["processed_cuts"] == 6 and not list(tmp_path.glob("*.bin"))
    assert stats["geometry_hits"] > 0


@pytest.mark.parametrize("shape", [(1, 1), (3, 8)])
def test_actual_geometry_reference(shape):
    st = station()
    v = volume(st, rays=180, gates=120)
    s = v.sweeps[0]
    lon = np.linspace(119.98, 120.2, np.prod(shape)).reshape(shape)
    lat = np.full(shape, 26.0)
    expected = reference("fusion").footprint(s, st, lon, lat, epoch("2026-08-28T00:06:00Z"))
    g = ground_geometry(st, lon, lat)
    p = prepare_polar(s)
    actual = footprint(s, st, lon, lat, epoch("2026-08-28T00:06:00Z"), ground=g, prepared=p)
    assert_arrays(vars(actual), vars(expected))
    later = footprint(s, st, lon, lat, epoch("2026-08-28T00:20:00Z"), ground=g, prepared=p)
    assert not later.horizontal.any()


def test_stream_failure_cleans_workspace(tmp_path):
    st = station()
    net = network([st])
    v = qc(volume(st), st, net)

    def broken():
        yield Volume(v.metadata, [v.sweeps[0]])
        raise RuntimeError("late cut failure")

    with pytest.raises(RuntimeError, match="late cut"):
        build_composite_streaming(
            broken(),
            net,
            "demo",
            "2026-08-28T00:06:00Z",
            "2026-08-28T00:10:00Z",
            options=ExecutionOptions(streaming=True, layer_memory_bytes=0),
            directory=tmp_path,
        )
    assert not list(tmp_path.iterdir())


def test_stream_rejects_duplicate_and_budget(tmp_path):
    st = station()
    net = network([st])
    v = qc(volume(st), st, net)
    one = Volume(v.metadata, [v.sweeps[0]])
    for sequence, opt, match in [
        ([one, one], ExecutionOptions(streaming=True), "duplicated"),
        (cuts([v]), ExecutionOptions(streaming=True, maximum_sweeps=1), "budget"),
    ]:
        with pytest.raises(ValueError, match=match):
            build_composite_streaming(
                sequence,
                net,
                "demo",
                "2026-08-28T00:06:00Z",
                "2026-08-28T00:10:00Z",
                options=opt,
                directory=tmp_path,
            )


def test_expired_and_future_skips_match(tmp_path):
    st = station()
    net = network([st])
    v = qc(volume(st), st, net)
    for target, cutoff in [
        ("2026-08-28T00:00:00Z", "2026-08-28T00:10:00Z"),
        ("2026-08-28T01:00:00Z", "2026-08-28T01:00:00Z"),
    ]:
        expected = reference("fusion").build_composite([v], net, "demo", target, cutoff)
        actual = build_composite_streaming(
            cuts([v]),
            net,
            "demo",
            target,
            cutoff,
            options=ExecutionOptions(streaming=True, layer_memory_bytes=0),
            directory=tmp_path,
        )
        assert_arrays(actual.arrays, expected.arrays)
        assert actual.metadata == expected.metadata


def test_geometry_calls_are_per_station_not_cut(monkeypatch):
    import rainpulse_algo.multiband.fusion as new

    old = reference("fusion")
    st = station()
    net = network([st], tile_rows=4)
    v = qc(volume(st, cuts=8), st, net)

    class Count:
        def __init__(self, g):
            self.g = g
            self.calls = 0

        def inv(self, *a, **k):
            self.calls += 1
            return self.g.inv(*a, **k)

    a = Count(old.GEOD)
    b = Count(new.GEOD)
    monkeypatch.setattr(old, "GEOD", a)
    monkeypatch.setattr(new, "GEOD", b)
    ref = old.build_composite([v], net, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:10:00Z")
    result = new.build_composite([v], net, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:10:00Z")
    assert_arrays(result.arrays, ref.arrays)
    tiles = (net.products["demo"].height + 3) // 4
    assert a.calls == tiles * 8 and b.calls == tiles


@pytest.mark.parametrize("cache_bytes", [0, 1, 1024 * 1024])
def test_geometry_cache_budget_does_not_change_values(tmp_path, cache_bytes):
    st = station()
    net = network([st])
    v = qc(volume(st, cuts=3), st, net)
    expected = reference("fusion").build_composite(
        [v], net, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:10:00Z"
    )
    stats = {}
    got = build_composite_streaming(
        cuts([v]),
        net,
        "demo",
        "2026-08-28T00:06:00Z",
        "2026-08-28T00:10:00Z",
        options=ExecutionOptions(streaming=True, geometry_cache_bytes=cache_bytes),
        directory=tmp_path,
        metrics=stats,
    )
    assert_arrays(got.arrays, expected.arrays)
    assert stats["geometry_cache_peak_bytes"] <= cache_bytes
