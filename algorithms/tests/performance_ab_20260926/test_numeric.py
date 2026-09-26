from types import SimpleNamespace

import numpy as np
import pytest
from scipy.ndimage import median_filter, minimum_filter1d

from rainpulse_algo.multiband.candidate_kernel import nonmet_candidate
from rainpulse_algo.multiband.preview_sampling import prepare_polar_sampling, render_polar_sampling
from rainpulse_algo.radar.qc_engine.group_validation import (
    validate_seed_objects,
    validate_temporal_objects,
)

from .references import legacy


def candidate_reference(f, echo, snr, cfg, r):
    dr = float(np.median(np.diff(r)))
    n = min(501, max(3, int(round(cfg.phase_window_m / dr))))
    n += n % 2 == 0
    measured = np.where(echo, f["DBZH"], np.nan)
    med = median_filter(np.where(echo, measured, 0.0), size=(1, n), mode="nearest")
    support = minimum_filter1d(echo.astype(np.uint8), size=n, axis=1, mode="nearest") == 1
    wx = f.get("WEATHER_PROTECTED_MASK", np.zeros(echo.shape, bool)).astype(bool)
    return (
        echo
        & support
        & snr
        & np.isfinite(f["RHOHV"])
        & (f["RHOHV"] < cfg.rho_candidate_max)
        & (abs(measured - med) > cfg.texture_candidate_db)
        & ~wx
    )


@pytest.mark.parametrize("nr,ng", [(1, 3), (7, 24), (30, 128)])
@pytest.mark.parametrize("active", [0, 0.1, 0.49, 0.5, 1])
@pytest.mark.parametrize("window", [3, 13, 51])
def test_candidate_same(nr, ng, active, window):
    rng = np.random.default_rng(638)
    z = rng.uniform(-45, 80, (nr, ng)).astype("f4")
    echo = rng.random(z.shape) > 0.04
    z[~echo] = np.nan
    rho = np.full(z.shape, 0.99, "f4")
    rho[: round(nr * active)] = rng.uniform(0.1, 0.75, rho[: round(nr * active)].shape)
    rho[rng.random(z.shape) < 0.03] = np.nan
    f = {
        "DBZH": z,
        "RHOHV": rho,
        "WEATHER_PROTECTED_MASK": (rng.random(z.shape) < 0.1).astype("u1"),
    }
    cfg = SimpleNamespace(
        phase_window_m=window * 75, rho_candidate_max=0.75, texture_candidate_db=8.0
    )
    r = np.arange(ng) * 75.0 + 37.5
    snr = rng.random(z.shape) > 0.1
    before = {k: v.copy() for k, v in f.items()}
    np.testing.assert_array_equal(
        nonmet_candidate(f, echo, snr, cfg, r), candidate_reference(f, echo, snr, cfg, r)
    )
    for k, v in f.items():
        np.testing.assert_array_equal(v, before[k])


@pytest.mark.parametrize("dtype", [np.float32, np.float64])
def test_candidate_threshold_no_weather_field(dtype):
    z = np.array([[0, 16, 0, 16, 0], [0, 16, 0, 16, 0]], dtype=dtype)
    cfg = SimpleNamespace(phase_window_m=3, rho_candidate_max=0.75, texture_candidate_db=8.0)
    for value in [0.75, np.nextafter(dtype(0.75), dtype(0))]:
        f = {"DBZH": z, "RHOHV": np.full(z.shape, value, dtype)}
        yes = np.ones(z.shape, bool)
        r = np.arange(5.0) + 1
        np.testing.assert_array_equal(
            nonmet_candidate(f, yes, yes, cfg, r), candidate_reference(f, yes, yes, cfg, r)
        )


def group_data():
    oid = np.array([[0, 7, 7, 7, 11, 11], [0, 7, 7, 7, 11, 11], [0, 0, 0, 0, 11, 11]], "i8")
    core = np.zeros(oid.shape, bool)
    core[0, 1:] = True
    count = np.bincount(oid.ravel())
    seeds = np.bincount(oid[core], minlength=len(count))
    size = count[oid].astype("u4")
    frac = np.divide(seeds, count, out=np.zeros(len(count), float), where=count > 0)[oid].astype(
        "f4"
    )
    return oid, size, frac, core, (oid != 0) & ~core


@pytest.mark.parametrize("kind", ["seed", "temporal"])
def test_selected_nan_object_identity_is_rejected(kind):
    oid = np.array([[np.nan]])
    size = np.array([[999999]], dtype=np.uint32)
    fraction = np.array([[0.001]], dtype=np.float32)
    selected = np.ones((1, 1), dtype=bool)
    with pytest.raises(ValueError, match="object identity"):
        if kind == "seed":
            validate_seed_objects(
                oid,
                size,
                fraction,
                ~selected,
                selected,
                maximum_object_gates=100,
                minimum_seed_gates=3,
                minimum_seed_fraction=0.3,
            )
        else:
            validate_temporal_objects(
                oid,
                size,
                fraction,
                selected,
                minimum_gates=3,
                maximum_gates=100,
                minimum_recurrence_fraction=0.3,
            )


def outcome(fn):
    try:
        fn()
        return True
    except (ValueError, OverflowError):
        return False


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "size",
        "later_size",
        "fraction",
        "nan",
        "seeds",
        "unselected",
        "sparse",
        "negative",
        "threshold",
    ],
)
def test_group_rules(fault):
    oid, size, frac, core, prop = group_data()
    if fault == "size":
        size[0, 1] += 1
    if fault == "later_size":
        size[1, 1] += 1  # old contract checks only first stored size
    if fault == "fraction":
        frac[1, 2] += 0.1
    if fault == "nan":
        frac[1, 2] = np.nan
    if fault == "seeds":
        core[0, 1:3] = False
    if fault == "unselected":
        prop[oid == 11] = False
        frac[oid == 11] = np.nan
    if fault == "sparse":
        oid[oid == 11] = 2**42
    if fault == "negative":
        oid[oid == 11] = -3
    if fault == "threshold":
        frac[oid == 7] = np.nextafter(np.float32(0.5), np.float32(1))
    arrays = [v.copy() for v in (oid, size, frac, core, prop)]
    def old():
        return legacy.loop_validate(
            oid, size, frac, core, prop, maximum=10, min_seeds=2, min_fraction=0.3
        )
    def new():
        return validate_seed_objects(
            oid,
            size,
            frac,
            core,
            prop,
            maximum_object_gates=10,
            minimum_seed_gates=2,
            minimum_seed_fraction=0.3,
        )
    assert outcome(old) == outcome(new)
    for a, b in zip(arrays, (oid, size, frac, core, prop)):
        np.testing.assert_array_equal(a, b)


@pytest.mark.parametrize(
    "fault",
    ["none", "size", "fraction", "boundary", "nan", "empty", "later_size", "later_fraction"],
)
def test_temporal_rules(fault):
    oid, size, frac, _, sel = group_data()
    frac[:] = 0.5
    if fault == "size":
        size[0, 1] += 1
    if fault == "fraction":
        frac[0, 1] = 0.1
    if fault == "boundary":
        frac[0, 1] = 0.5 - 1e-6
    if fault == "nan":
        frac[0, 1] = np.nan  # outer validator retains its separate finite check
    if fault == "empty":
        sel[:] = False
    if fault == "later_size":
        size[1, 1] = 100
    if fault == "later_fraction":
        frac[1, 1] = 0.1

    def old():
        for v in np.unique(oid[sel]):
            comp = oid == v
            n = int(comp.sum())
            if n != int(size[comp][0]):
                raise ValueError()
            if n < 2 or n > 10 or float(frac[comp][0]) + 1e-6 < 0.5:
                raise ValueError()

    def new():
        return validate_temporal_objects(
            oid, size, frac, sel, minimum_gates=2, maximum_gates=10, minimum_recurrence_fraction=0.5
        )
    assert outcome(old) == outcome(new)


@pytest.mark.parametrize("nr", [1, 7, 71, 360])
@pytest.mark.parametrize("geographic", [False, True])
@pytest.mark.parametrize("mode", ["raw", "uncertain", "actions", "both"])
def test_png_bytes(nr, geographic, mode):
    rng = np.random.default_rng(729)
    az = rng.choice(np.arange(36000) / 100, nr, replace=False)  # deliberately unsorted
    el = np.linspace(0.5, 1, nr)
    r = np.arange(125, 8125, 250.0)
    z = rng.uniform(-30, 75, (nr, len(r))).astype("f4")
    z[rng.random(z.shape) < 0.15] = np.nan
    action = rng.integers(0, 4, z.shape, dtype="u1")
    mapping = legacy.map_sampling(az, el, r, 48) if geographic else None
    kwargs = {}
    if mode in ("uncertain", "both"):
        kwargs["uncertain"] = action == 3
    if mode in ("actions", "both"):
        kwargs["actions"] = action
    old = legacy.polar_quicklook(
        az, r, z, size=48, map_sampling=mapping, elevation_deg=el, **kwargs
    )
    plan = prepare_polar_sampling(az, r, size=48, map_sampling=mapping, elevation_deg=el)
    new = render_polar_sampling(plan, z, legacy.LEVELS, legacy.COLORS, legacy.png, **kwargs)
    assert old == new
    assert not plan.rays.flags.writeable and not plan.support.flags.writeable


def test_reuse_field_masks_and_source_identity():
    az = np.arange(360.0)
    r = np.arange(125, 5000, 250.0)
    z = np.full((360, len(r)), 25.0, "f4")
    plan = prepare_polar_sampling(az, r, size=32)
    qc = z.copy()
    qc[:180] = np.nan
    for v in [z, qc, np.full_like(z, np.nan)]:
        assert render_polar_sampling(
            plan, v, legacy.LEVELS, legacy.COLORS, legacy.png
        ) == legacy.polar_quicklook(az, r, v, size=32)
    with pytest.raises(ValueError):
        render_polar_sampling(plan, z[:-1], legacy.LEVELS, legacy.COLORS, legacy.png)


@pytest.mark.parametrize("az", [[359, 0, 1], [90, 91, 93, 110], [0, 0.5, 1, 180, 181]])
def test_seams_and_gaps(az):
    az = np.array(az)
    r = np.array([125.0, 375.0, 625.0])
    z = np.arange(len(az) * 3.0).reshape(len(az), 3)
    plan = prepare_polar_sampling(az, r, size=32)
    assert render_polar_sampling(
        plan, z, legacy.LEVELS, legacy.COLORS, legacy.png
    ) == legacy.polar_quicklook(az, r, z, size=32)


@pytest.mark.parametrize("label_type", ["i4", "u4", "u8", "f8"])
def test_dense_and_compact_label_paths(label_type):
    rng = np.random.default_rng(572)
    oid = rng.choice([0, 2, 11, 51], (32, 51)).astype(label_type)
    core = rng.random(oid.shape) < 0.6
    prop = (oid > 0) & ~core
    size = np.zeros(oid.shape, "u4")
    frac = np.zeros(oid.shape, "f4")
    for value in np.unique(oid):
        selected = oid == value
        size[selected] = selected.sum()
        frac[selected] = (core & selected).sum() / selected.sum()
    for corrupt in (False, True):
        if corrupt:
            frac[oid == 11] = 0
        def old():
            return legacy.loop_validate(
                    oid, size, frac, core, prop, maximum=1000, min_seeds=1, min_fraction=0.1
                )
        def new():
            return validate_seed_objects(
                    oid,
                    size,
                    frac,
                    core,
                    prop,
                    maximum_object_gates=1000,
                    minimum_seed_gates=1,
                    minimum_seed_fraction=0.1,
                )
        assert outcome(old) == outcome(new)
