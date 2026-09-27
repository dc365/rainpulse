import copy
import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from pyproj import Transformer

from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.multiband.model import Grid, Network, Station, Sweep, Volume, epoch
from rainpulse_algo.multiband.stream_fusion import build_composite_streaming


def exact_root():
    value = os.getenv("RAINPULSE_CD_REFERENCE_ROOT")
    if value is None:
        raise RuntimeError("set RAINPULSE_CD_REFERENCE_ROOT to extracted verified-source.zip")
    return Path(value)


def original_stream():
    name = "rainpulse_algo.multiband._cd_before_stream"
    if name not in sys.modules:
        p = exact_root() / "algorithms/rainpulse_algo/multiband/stream_fusion.py"
        spec = importlib.util.spec_from_file_location(name, p)
        m = importlib.util.module_from_spec(spec)
        sys.modules[name] = m
        spec.loader.exec_module(m)
    return sys.modules[name]


def scene(cuts=3):
    rng = np.random.default_rng(374)
    st = []
    vol = []
    t = epoch("2026-08-28T00:00:00Z")
    for sid, band in [("s1", "S"), ("x1", "X")]:
        s = Station(
            radar_id=sid,
            band=band,
            source="native_bundle",
            frequency_hz=2.9e9 if band == "S" else 9.4e9,
            longitude_deg=120.0,
            latitude_deg=26.0,
            altitude_m_msl=100.0,
            beam_width_h_deg=2.0,
            beam_width_v_deg=1.0,
            enabled=True,
            geometry_verified=True,
            maximum_age_seconds=900,
        )
        st.append(s)
        meta = dict(
            radar_id=sid,
            band=band,
            scan_id="scan" + sid,
            frequency_hz=s.frequency_hz,
            longitude_deg=120.0,
            latitude_deg=26.0,
            altitude_m_msl=100.0,
            height_datum="MSL",
            volume_start="2026-08-28T00:00:00Z",
            volume_end="2026-08-28T00:01:00Z",
            available_at="2026-08-28T00:01:00Z",
            scan_type="volume",
            asset_sha256="a" * 64,
            network_sha256="b" * 64,
        )
        for n in range(cuts):
            z = rng.uniform(-10, 60, (180, 96)).astype("float32")
            obs = (rng.random(z.shape) > 0.1).astype("uint8")
            no = ((rng.random(z.shape) < 0.02) & (obs == 1)).astype("uint8")
            z[(obs == 0) | (no == 1)] = np.nan
            f = dict(
                DBZH=z,
                DBZH_QC=z.copy(),
                OBSERVED_MASK=obs,
                NO_ECHO_MASK=no,
                QUALITY_SCORE=np.full(z.shape, 0.7, "float32"),
                REFLECTIVITY_ELIGIBLE_FOR_CR=(obs & (rng.random(z.shape) > 0.1)).astype("uint8"),
            )
            f["CR_UNCERTAIN_MASK"] = (obs & (f["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 0)).astype(
                "uint8"
            )
            sw = Sweep(
                n,
                np.arange(180) * 2.0,
                np.arange(96) * 500.0 + 250.0,
                np.full(180, 0.5 + n),
                np.linspace(t, t + 60, 180),
                f,
            )
            vol.append(Volume(copy.deepcopy(meta), [sw]))
    tx = Transformer.from_crs(4326, 32651, always_xy=True)
    x, y = tx.transform(120.0, 26.0)
    g = Grid(
        "demo",
        "EPSG:32651",
        x - 15000,
        y - 13000,
        1000.0,
        30,
        26,
        (250.0, 500.0, 1000.0, 2000.0),
        4,
        360,
    )
    return vol, Network("rel", {s.radar_id: s for s in st}, {"demo": g}, "b" * 64)


def assert_product(a, b):
    assert a.metadata == b.metadata
    assert a.arrays.keys() == b.arrays.keys()
    for k in a.arrays:
        assert a.arrays[k].dtype == b.arrays[k].dtype
        assert np.array_equal(a.arrays[k], b.arrays[k], equal_nan=True), k


@pytest.mark.parametrize("capacity", [0, 1, 50000, 150000, 10_000_000])
@pytest.mark.parametrize("comparison", [False, True])
def test_real_stream_loop_matches_previous_all_fields(tmp_path, capacity, comparison):
    v, network = scene()
    o = ExecutionOptions(streaming=True, layer_memory_bytes=capacity)
    olddir = tmp_path / "old"
    newdir = tmp_path / "new"
    olddir.mkdir()
    newdir.mkdir()
    args = (network, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:06:00Z")
    before = original_stream().build_composite_streaming(
        iter(copy.deepcopy(v)), *args, options=o, directory=olddir, comparison=comparison
    )
    m = {}
    after = build_composite_streaming(
        iter(v), *args, options=o, directory=newdir, comparison=comparison, metrics=m
    )
    assert np.isfinite(before.arrays["CR_DBZH"]).any()
    assert_product(before, after)
    if comparison:
        for b in ("S", "X"):
            assert_product(before.band_comparisons[b], after.band_comparisons[b])
    assert m["height_resident_array_peak_bytes"] <= max(capacity, m["peak_height_tile_bytes"])
    assert not list(newdir.iterdir())


def test_last_cut_failure_returns_no_product_and_cleans(tmp_path):
    v, n = scene()

    def broken():
        yield from v[:-1]
        raise RuntimeError("last cut")

    with pytest.raises(RuntimeError, match="last cut"):
        build_composite_streaming(
            broken(),
            n,
            "demo",
            "2026-08-28T00:06:00Z",
            "2026-08-28T00:06:00Z",
            options=ExecutionOptions(streaming=True, layer_memory_bytes=0),
            directory=tmp_path,
            comparison=True,
        )
    assert not list(tmp_path.iterdir())


def before_rdr():
    name = "cd_before_volume"
    if name not in sys.modules:
        import rainpulse_algo.radar.qc_engine.volume_review as current

        paths = [
            str(exact_root() / "algorithms/rainpulse_algo/radar/qc_engine/volume_review"),
            str(Path(current.__file__).parent),
        ]
        mod = ModuleType(name)
        mod.__path__ = paths
        sys.modules[name] = mod
        sub = ModuleType(name + ".receiver_domain")
        sub.__path__ = [str(Path(p) / "receiver_domain") for p in paths]
        sys.modules[sub.__name__] = sub
    return importlib.import_module(name + ".receiver_domain.core"), importlib.import_module(
        name + ".receiver_domain.source_family"
    )


def rdr_scene():
    from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep
    from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.config import (
        ReceiverDomainConfig,
        SourceFamilyConfig,
    )

    r = np.arange(50125.0, 460000.0, 500.0)
    az = np.arange(50.0, 65.0)
    sh = (len(az), len(r))
    f = {k: np.full(sh, np.nan, "float32") for k in ("DBZH", "SNR", "PHIDP", "ZDR", "RHOHV")}
    f["SNR"][:] = -7.5
    for row in (8, 9, 10):
        f["SNR"][row] = 55.5
        f["DBZH"][row] = 55.5 + 20 * np.log10(r / 1000) + 0.01 * r / 1000 - 44.6
        f["PHIDP"][row] = 65.0
        f["ZDR"][row] = 0.2
        f["RHOHV"][row] = 0.99
    target = ((r >= 390000) & (r < 403000)) | (r >= 429000)
    f["SNR"][7, target] = 48.5
    f["DBZH"][7, target] = 48.5 + 20 * np.log10(r[target] / 1000) + 0.01 * r[target] / 1000 - 44.3
    f["PHIDP"][7, target] = 65
    f["ZDR"][7, target] = 0.2
    f["RHOHV"][7, target] = 0.99
    near = r < 200000
    f["SNR"][7, near] = np.linspace(20, 54, near.sum())
    f["DBZH"][7, near] = 25
    f["PHIDP"][7, near] = 20
    f["ZDR"][7, near] = 5
    f["RHOHV"][7, near] = 0.6
    gaps = np.zeros(len(az), bool)
    gaps[-1] = True
    s = Sweep(
        "sweep_000",
        az,
        np.full(len(az), 0.5),
        r,
        f,
        {k: np.isfinite(v) for k, v in f.items()},
        np.ones(len(az), bool),
        gaps,
        np.arange(len(az)) * 2.0,
    )
    c = ReceiverDomainConfig(
        mode="quarantine",
        local_policy="source_joint_review",
        source_family=SourceFamilyConfig(mode="experiment"),
    )
    return s, c


@pytest.mark.parametrize(
    "variant", ["normal", "missing", "counter", "protected", "rotation", "reversed_range_value"]
)
def test_rdr_arrays_models_references_identical(variant):
    from dataclasses import replace

    from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.core import evaluate

    s, c = rdr_scene()
    kw = {}
    if variant in ("missing", "counter", "reversed_range_value"):
        f = {k: v.copy() for k, v in s.fields.items()}
        a = {k: v.copy() for k, v in s.available.items()}
        if variant == "missing":
            a["ZDR"][7, 600:] = False
        elif variant == "counter":
            f["PHIDP"][7, 600:] = 145
        else:
            f["DBZH"][7] = f["DBZH"][7, ::-1]
            a["DBZH"][7] = np.isfinite(f["DBZH"][7])
        s = replace(s, fields=f, available=a)
    if variant == "rotation":
        s = replace(s, azimuth=(s.azimuth + 280) % 360)
    if variant == "protected":
        mask = np.zeros(s.shape, bool)
        mask[7, 650:] = True
        kw["independent_weather"] = mask
    before = before_rdr()[0].evaluate(s, c, **kw)
    after = evaluate(s, c, **kw)
    assert before.summary == after.summary and before.models == after.models
    for k in before.arrays:
        assert np.array_equal(before.arrays[k], after.arrays[k], equal_nan=True), k


@pytest.mark.parametrize("block", [19, 20, 21, 22])
def test_reference_excludes_all_target_guard_measurements(block):
    from dataclasses import replace

    from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.source_family import (
        fit_family,
    )

    s, c = rdr_scene()
    model, why = fit_family(s, 7, block, c)
    assert model is not None, why
    f = {k: v.copy() for k, v in s.fields.items()}
    a = {k: v.copy() for k, v in s.available.items()}
    mask = np.abs((s.ranges // c.block_m).astype(int) - block) <= c.guard_blocks
    for k in f:
        f[k][:, mask] = 0.25 if k == "RHOHV" else 120
    changed = replace(s, fields=f, available=a)
    newer, status = fit_family(changed, 7, block, c)
    assert status == why and newer == model


@pytest.mark.parametrize("capacity", [0, 50000, 10_000_000])
def test_current_optional_numba_selection_with_pool(tmp_path, capacity):
    pytest.importorskip("numba")
    v, n = scene()
    a = tmp_path / "numpy"
    b = tmp_path / "numba"
    a.mkdir()
    b.mkdir()
    args = (n, "demo", "2026-08-28T00:06:00Z", "2026-08-28T00:06:00Z")
    opts = ExecutionOptions(streaming=True, layer_memory_bytes=capacity)
    from dataclasses import replace

    x = build_composite_streaming(
        iter(copy.deepcopy(v)), *args, options=opts, directory=a, comparison=True
    )
    y = build_composite_streaming(
        iter(v),
        *args,
        options=replace(opts, selection_backend="numba"),
        directory=b,
        comparison=True,
    )
    assert_product(x, y)
    for band in ("S", "X"):
        assert_product(x.band_comparisons[band], y.band_comparisons[band])
