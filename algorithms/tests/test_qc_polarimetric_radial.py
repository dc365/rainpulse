from pathlib import Path

import numpy as np

from rainpulse_algo.radar.qc import _detect_radial_interference, load_qc_profile
from rainpulse_algo.radar.qc_polarimetric_radial import polarimetric_extent_masks


def fixture():
    r = np.arange(125.0, 460000.0, 250.0)
    z = np.tile(20 * np.log10(r / 1000) + (-15), (4, 1)).astype("float32")
    return r, z, np.full(z.shape, 0.3, dtype="float32")


def test_broad_contamination_without_azimuth_contrast_preserves_weather_and_missing():
    r, z, rho = fixture()
    z[0, 900:920] = np.nan
    rho[1, 1000:1040] = 0.99
    rho[2] = 0.99
    rho[3] = np.nan
    candidate, hard = polarimetric_extent_masks(z, np.isfinite(z), rho, r)
    assert candidate[1].any() and hard[1].sum() > 1000
    assert not hard[1, 998:1042].any()
    assert not candidate[2:].any()
    assert not hard[2:].any()
    assert not hard[~np.isfinite(z)].any()
    assert not hard[:, r < 50000].any()


def test_short_radial_and_uniform_reflectivity_do_not_promote():
    r, z, rho = fixture()
    z[0, r > 250000] = np.nan
    z[1] = 35
    z[2, ::2] = np.nan
    _, hard = polarimetric_extent_masks(z, np.isfinite(z), rho, r)
    assert not hard[:3].any()
    assert hard[3].any()


def test_new_evidence_respects_cross_radar_veto_and_old_profile():
    root = Path(__file__).resolve().parents[2]
    flags = root / "configs/qc/flag-definitions.yaml"
    r, z, rho = fixture()
    v2 = load_qc_profile(root / "configs/qc/fujian-qc-evidence-v2.yaml", flags)
    v3 = load_qc_profile(root / "configs/qc/fujian-qc-evidence-v3.yaml", flags)
    assert v3.radial_interference.polarimetric_extent_radars == ("z9598",)
    old = _detect_radial_interference(
        z, np.isfinite(z), v2.radial_interference, ranges_m=r, rhohv=rho
    )
    new = _detect_radial_interference(
        z, np.isfinite(z), v3.radial_interference, ranges_m=r, rhohv=rho
    )
    veto = _detect_radial_interference(
        z,
        np.isfinite(z),
        v3.radial_interference,
        ranges_m=r,
        rhohv=rho,
        cross_radar_consistency=np.ones(4),
    )
    assert np.count_nonzero(new.probability >= 0.8) > np.count_nonzero(old.probability >= 0.8)
    assert not np.any(veto.probability >= 0.8)


def test_small_dropouts_do_not_hide_long_interference_or_become_valid():
    r, z, rho = fixture()
    z[:, 800:802] = np.nan
    z[:, 1200] = np.nan
    _, hard = polarimetric_extent_masks(z, np.isfinite(z), rho, r)
    assert np.all(hard.sum(axis=1) > 1000)
    assert not hard[~np.isfinite(z)].any()


def test_worker_core_scopes_new_evidence_to_allowlist_and_preserves_raw():
    from dataclasses import replace

    from rainpulse_algo.radar.qc import apply_basic_qc

    from .test_radar_qc import synthetic_normalized_fixture

    root = Path(__file__).resolve().parents[2]
    flags = root / "configs/qc/flag-definitions.yaml"
    v2 = load_qc_profile(root / "configs/qc/fujian-qc-evidence-v2.yaml", flags)
    v3 = load_qc_profile(root / "configs/qc/fujian-qc-evidence-v3.yaml", flags)
    r, z, rho = fixture()
    objects = synthetic_normalized_fixture(z, range_m=r, moments={"RHOHV": rho})
    original = dict(objects)
    old = apply_basic_qc(objects, v2).sweeps[0]
    excluded = apply_basic_qc(objects, v3).sweeps[0]
    enabled = replace(
        v3,
        radial_interference=replace(v3.radial_interference, polarimetric_extent_radars=("z9999",)),
    )
    new = apply_basic_qc(objects, enabled).sweeps[0]
    assert objects == original
    np.testing.assert_array_equal(old.qc_flags, excluded.qc_flags)
    np.testing.assert_array_equal(old.quality_index, excluded.quality_index)
    np.testing.assert_array_equal(new.dbzh_raw, old.dbzh_raw)
    mask = v3.flag_masks["RADIAL_INTERFERENCE"]
    assert np.count_nonzero(new.qc_flags & mask) > np.count_nonzero(old.qc_flags & mask)
