"""Synthetic unit-grid tests against the actual pinned STEPS numerical backend."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.ndimage import gaussian_filter

from rainpulse_algo.nowcast import pysteps_steps
from rainpulse_algo.nowcast.pysteps_lk import PystepsLKFields, run_pysteps_lk_fields
from rainpulse_algo.nowcast.pysteps_profile import load_pysteps_lk_profile
from rainpulse_algo.nowcast.steps_profile import load_pysteps_steps_profile

ROOT = Path(__file__).resolve().parents[2]


def native_setup(monkeypatch):
    profile = load_pysteps_steps_profile(ROOT / "configs/nowcast/prelaunch-pysteps-steps-v6.yaml")
    profile = replace(
        profile,
        ensemble=replace(profile.ensemble, member_count=4),
        support=replace(profile.support, minimum_valid_members=3),
    )
    motion = load_pysteps_lk_profile(ROOT / "configs/nowcast/prelaunch-pysteps-lk-v2.yaml")
    # Deliberately a synthetic square metre-grid, not an operational Fujian asset.
    grid = SimpleNamespace(
        shape=(48, 48),
        grid_id=profile.grid_id,
        config_version=profile.grid_config_version,
        metric=lambda: SimpleNamespace(
            x_spacing_m_by_latitude=np.full(48, 1000.0), y_spacing_m_by_latitude=np.full(48, 1000.0)
        ),
    )
    rng = np.random.default_rng(47)
    base = gaussian_filter(rng.uniform(0.2, 60, grid.shape), 1.0).astype("float32")
    base[:4] = 0
    base[-4:] = 0
    base[:, :4] = 0
    base[:, -4:] = 0
    rate = np.stack([np.roll(base, t, axis=1) for t in range(3)])
    dbzh = (10 * np.log10(np.maximum(rate, 0.01) * 200)).astype("float32")
    quality = np.ones_like(rate)
    valid = np.ones_like(rate, dtype="uint8")
    valid[:, 16:28, 20:24] = 0
    rate[valid == 0] = np.nan
    dbzh[valid == 0] = np.nan
    quality[valid == 0] = np.nan
    source = PystepsLKFields(
        reflectivity_dbz=dbzh,
        rate_mm_h=rate,
        quality_index=quality,
        valid_mask=valid,
        low_quality_mask=np.zeros_like(valid),
    )
    velocity = np.zeros((2, *grid.shape), dtype="float32")
    velocity[0] = 0.75

    # Isolate support propagation from feature detection, while retaining the
    # actual deterministic and stochastic semi-Lagrangian implementations.
    def fixed_motion(fields, *, profile, grid):
        return run_pysteps_lk_fields(
            fields, profile=profile, grid=grid, motion_estimator=lambda *_: velocity
        )

    monkeypatch.setattr(pysteps_steps, "run_pysteps_lk_fields", fixed_motion)
    return grid, profile, motion, source


def test_native_backend_receives_nan_source_support_instead_of_dry_floor(monkeypatch):
    grid, profile, motion, source = native_setup(monkeypatch)
    seen = []

    def spy(precip, velocity, leads, **kwargs):
        assert np.any(np.isnan(precip))
        assert kwargs["extrap_kwargs"] == {"interp_order": 1, "allow_nonfinite_values": True}
        seen.append(True)
        return np.broadcast_to(precip[-1], (4, leads, *grid.shape)).copy()

    result = pysteps_steps.run_pysteps_steps_fields(
        source, profile=profile, lk_profile=motion, grid=grid, backend=spy
    )
    assert seen
    assert not np.any(result.member_valid_mask[:, :, 18:24, 21:23])


def test_real_seeded_steps_propagates_native_holes_per_member(monkeypatch):
    grid, profile, motion, source = native_setup(monkeypatch)
    result = pysteps_steps.run_pysteps_steps_fields(
        source, profile=profile, lk_profile=motion, grid=grid
    )
    valid = result.member_valid_mask == 1
    assert not result.ensemble_fallback_used
    assert np.all(np.isnan(result.rain_rate[~valid]))
    assert np.all(np.isfinite(result.rain_rate[valid]))
    assert np.any(valid[0] != valid[1])
    count = valid.sum(axis=0)
    assert not np.any(result.output_valid_mask[count < 3])
    for probabilities in result.probability_exceedance.values():
        assert np.all(np.isnan(probabilities[count < 3]))
