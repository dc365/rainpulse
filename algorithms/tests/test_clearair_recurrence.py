"""Unit tests for the clear-air recurrence exclusion module (v3.1)."""

from __future__ import annotations

import numpy as np
import pytest

from rainpulse_algo.radar.qc import QCSweep
from rainpulse_algo.radar.qc_engine.adapters import NativeSweep
from rainpulse_algo.radar.qc_engine.clearair_recurrence import (
    AZ_BINS,
    GATE_LIMIT,
    ClearairBackground,
    ClearairRecurrenceParams,
    accumulate_anchor_counts,
    anchor_mask,
    footprint_indices,
    parse_background,
    prone_mask,
    review_result,
    sweep_exclusion,
)
from rainpulse_algo.radar.qc_engine.profile import (
    ClearairRecurrenceConfig,
    OpenSourceQCProfile,
)

PARAMS = ClearairRecurrenceParams()


def make_background(fraction=None, *, volumes=10, station="z9591"):
    if fraction is None:
        fraction = np.zeros((AZ_BINS, GATE_LIMIT), np.float32)
    return ClearairBackground(station=station, recurrence_fraction=fraction,
                              source_volumes=volumes, built_at_utc="2026-09-18T12:00:00Z")


def background_arrays(fraction, *, volumes=10, station="z9591",
                      contract="qc-clearair-recurrence-v1"):
    return {
        "RECURRENCE_FRACTION": np.asarray(fraction, np.float32),
        "STATION": station,
        "SOURCE_VOLUMES": volumes,
        "BUILT_AT_UTC": "2026-09-18T12:00:00Z",
        "CONTRACT": contract,
    }


def test_parse_background_requires_contract_fields():
    arrays = background_arrays(np.zeros((AZ_BINS, GATE_LIMIT), np.float32))
    bg = parse_background(arrays)
    assert bg.station == "z9591" and bg.source_volumes == 10
    with pytest.raises(ValueError):
        parse_background({k: v for k, v in arrays.items() if k != "STATION"})
    with pytest.raises(ValueError):
        parse_background(background_arrays(np.zeros((10, 10), np.float32)))
    with pytest.raises(ValueError):
        parse_background(background_arrays(np.zeros((AZ_BINS, GATE_LIMIT), np.float32),
                                           contract="other-v9"))


def test_background_rejects_bad_shape_and_empty_sources():
    with pytest.raises(ValueError):
        make_background(np.zeros((100, 100), np.float32))
    with pytest.raises(ValueError):
        make_background(volumes=0)


def test_anchor_mask_thresholds():
    dbzh = np.array([[5.0, 15.0, 20.0, 20.0]])
    snr = np.array([[20.0, 20.0, 5.0, 20.0]])
    rho = np.array([[0.99, 0.99, 0.99, 0.5]])
    mask = anchor_mask(dbzh, snr, rho)
    assert mask.tolist() == [[False, True, False, False]]


def test_footprint_indices_wrap_and_clip():
    rows, _ = footprint_indices(np.array([0.0, 359.9, 400.0, -0.6]), 400)
    assert rows.tolist() == [0, 359, 40, 359]


def test_accumulate_anchor_counts_per_volume_maximum():
    volume = {
        "sweep_a": {"azimuth": np.array([10.0]), "DBZH": np.array([[30.0, 30.0]]),
                    "SNR": np.array([[30.0, 30.0]]), "RHOHV": np.array([[0.99, 0.99]])},
        "sweep_b": {"azimuth": np.array([10.2]), "DBZH": np.array([[30.0, 30.0]]),
                    "SNR": np.array([[30.0, 30.0]]), "RHOHV": np.array([[0.99, 0.99]])},
    }
    counts = accumulate_anchor_counts([volume, volume])
    # both sweeps anchor the same footprint cell: one hit per volume, two volumes
    assert counts[10, 0] == 2 and counts[10, 1] == 2
    partial = {
        "sweep_a": {"azimuth": np.array([10.0]), "DBZH": np.array([[30.0, 3.0]]),
                    "SNR": np.array([[30.0, 30.0]]), "RHOHV": np.array([[0.99, 0.99]])},
    }
    counts2 = accumulate_anchor_counts([partial, partial])
    assert counts2[10, 0] == 2 and counts2[10, 1] == 0


def _recurrent_region(fraction=0.9, az_lo=20, az_hi=30, gate_lo=20, gate_hi=40):
    frac = np.zeros((AZ_BINS, GATE_LIMIT), np.float32)
    frac[az_lo:az_hi, gate_lo:gate_hi] = fraction
    return frac


def test_prone_mask_requires_region_size_and_anchor_freedom():
    frac = _recurrent_region()
    bg = make_background(frac)
    anchors = np.zeros((AZ_BINS, GATE_LIMIT), np.uint16)
    mask, diag = prone_mask(bg, anchors, PARAMS)
    assert mask.sum() > 0  # 10 az x 20 gates region survives
    # a single anchor hit does not protect (threshold is two hits)
    anchors2 = anchors.copy()
    anchors2[25, 30] = 1
    mask1, _ = prone_mask(bg, anchors2, PARAMS)
    assert mask1[25, 30] and mask1[24, 30]
    anchors3 = anchors.copy()
    anchors3[25, 30] = 2
    mask2, _ = prone_mask(bg, anchors3, PARAMS)
    assert not mask2[25, 30] and not mask2[24, 30] and not mask2[26, 30]
    # tiny region below min_region_gates is dropped
    tiny = np.zeros((AZ_BINS, GATE_LIMIT), np.float32)
    tiny[100:102, 100:103] = 0.9  # 4 cells < 8
    mask3, _ = prone_mask(make_background(tiny), anchors, PARAMS)
    assert mask3.sum() == 0


def test_prone_mask_azimuth_wrap_connectivity():
    frac = np.zeros((AZ_BINS, GATE_LIMIT), np.float32)
    frac[357:, 50:54] = 0.9
    frac[:3, 50:54] = 0.9  # wraps the seam: 6 x 4 = 24 cells connected
    mask, _ = prone_mask(make_background(frac), np.zeros((AZ_BINS, GATE_LIMIT), np.uint16), PARAMS)
    assert mask[359, 51] and mask[1, 51]


def _frame(n_rays=8, n_gates=60):
    az = np.arange(n_rays) * 0.9  # rays map to footprint rows 0..7
    rng = np.arange(n_gates) * 250.0 + 125.0
    dbzh = np.full((n_rays, n_gates), np.nan)
    elig = np.zeros((n_rays, n_gates), bool)
    cls = np.zeros((n_rays, n_gates), np.uint8)
    prot = np.zeros((n_rays, n_gates), bool)
    return az, rng, dbzh, elig, cls, prot


def test_sweep_exclusion_rules():
    frac = _recurrent_region(az_lo=0, az_hi=8, gate_lo=10, gate_hi=50)
    bg = make_background(frac)
    prone, diag = prone_mask(bg, np.zeros((AZ_BINS, GATE_LIMIT), np.uint16), PARAMS)
    az, rng, dbzh, elig, cls, prot = _frame()
    dbzh[:, 12:20] = 0.0        # weak clutter band on recurrent footprint
    dbzh[:, 25:30] = 20.0       # strong echo in the same region: never excluded
    elig[:, 12:30] = True
    excl = sweep_exclusion(dbzh_raw=dbzh, eligible=elig, protected=prot, cf_class=cls,
                           prone=prone, diag=diag, azimuth_deg=az, elevation_deg=0.5,
                           params=PARAMS)
    assert excl[:, 12:20].all() and not excl[:, 25:30].any()

    # production protection vetoes
    prot[:, 12:14] = True
    excl2 = sweep_exclusion(dbzh_raw=dbzh, eligible=elig, protected=prot, cf_class=cls,
                            prone=prone, diag=diag, azimuth_deg=az, elevation_deg=0.5,
                            params=PARAMS)
    assert not excl2[:, 12:14].any() and excl2[:, 14:20].all()

    # elevation cap
    excl3 = sweep_exclusion(dbzh_raw=dbzh, eligible=elig, protected=prot, cf_class=cls,
                            prone=prone, diag=diag, azimuth_deg=az, elevation_deg=9.9,
                            params=PARAMS)
    assert not excl3.any()

    # CF1 override allowed only with strict clear recurrence (0.9 >= 0.7 here)
    cls[:, 12:20] = 1
    excl4 = sweep_exclusion(dbzh_raw=dbzh, eligible=elig, protected=prot, cf_class=cls,
                            prone=prone, diag=diag, azimuth_deg=az, elevation_deg=0.5,
                            params=PARAMS)
    assert excl4[:, 14:20].all() and not excl4[:, 12:14].any()  # protection still vetoes
    frac_strict = np.zeros((AZ_BINS, GATE_LIMIT), np.float32)
    frac_strict[0:8, 10:50] = 0.6  # above t_clear, below strict
    bg2 = make_background(frac_strict)
    prone2, diag2 = prone_mask(bg2, np.zeros((AZ_BINS, GATE_LIMIT), np.uint16), PARAMS)
    excl5 = sweep_exclusion(dbzh_raw=dbzh, eligible=elig, protected=prot, cf_class=cls,
                            prone=prone2, diag=diag2, azimuth_deg=az, elevation_deg=0.5,
                            params=PARAMS)
    assert not excl5[:, 12:20].any()


def test_sweep_exclusion_weak_band_bounds():
    frac = _recurrent_region(az_lo=0, az_hi=8, gate_lo=10, gate_hi=50)
    bg = make_background(frac)
    prone, diag = prone_mask(bg, np.zeros((AZ_BINS, GATE_LIMIT), np.uint16), PARAMS)
    az, rng, dbzh, elig, cls, prot = _frame()
    dbzh[:, 12] = -10.0   # lower bound inclusive
    dbzh[:, 13] = -10.1   # below band
    dbzh[:, 14] = 4.9     # inside band
    dbzh[:, 15] = 5.0     # upper bound exclusive
    elig[:, 12:16] = True
    excl = sweep_exclusion(dbzh_raw=dbzh, eligible=elig, protected=prot, cf_class=cls,
                           prone=prone, diag=diag, azimuth_deg=az, elevation_deg=0.5,
                           params=PARAMS)
    assert excl[:, 12].all() and not excl[:, 13].any()
    assert excl[:, 14].all() and not excl[:, 15].any()


# ---- engine extension integration ----


class _ProfileStub:
    clearair_recurrence = ClearairRecurrenceConfig()


def _native(name="sweep_000", n_rays=8, n_gates=60, elevation=0.5):
    az = np.arange(n_rays) * 0.9
    rng = np.arange(n_gates) * 250.0 + 125.0
    fields = {"DBZH": np.full((n_rays, n_gates), np.nan)}
    return NativeSweep(
        name=name, azimuth=az, elevation=np.full(n_rays, elevation), ranges=rng,
        ray_time=np.arange(n_rays).astype("datetime64[s]"), fields=fields,
        field_available={"DBZH": np.zeros((n_rays, n_gates), bool)},
        original_indices=np.arange(n_rays), full_ppi=True,
        geometry_good=np.ones(n_rays, bool), gap_after=np.zeros(n_rays, bool),
        attrs={}, audit={},
    )


def _sweep(native, dbzh, eligible, cf_class=None, protections=None):
    n_rays, n_gates = dbzh.shape
    arrays = {
        "REFLECTIVITY_ELIGIBLE_FOR_CR": eligible.astype("uint8"),
        "CF_CLASS": (cf_class if cf_class is not None
                     else np.zeros((n_rays, n_gates), np.uint8)),
    }
    for key, mask in (protections or {}).items():
        arrays[key] = mask.astype("uint8")
    return QCSweep(
        name=native.name, dbzh_raw=dbzh, dbzh_qc=dbzh.copy(),
        optional_qc_fields=arrays,
        quality_index=np.full((n_rays, n_gates), 0.8, "float32"),
        qi_components={}, qc_flags=np.zeros((n_rays, n_gates), "uint32"),
        valid_mask=np.ones((n_rays, n_gates), "uint8"),
        low_quality_mask=np.zeros((n_rays, n_gates), "uint8"),
        p_meteo=np.zeros((n_rays, n_gates), "float32"),
        p_ap=np.zeros((n_rays, n_gates), "float32"),
        p_sea_clutter=np.zeros((n_rays, n_gates), "float32"),
        p_radial_interference=np.zeros((n_rays, n_gates), "float32"),
        p_meteo_dual_pol=np.zeros((n_rays, n_gates), "float32"),
        p_vertical_consistency=np.zeros((n_rays, n_gates), "float32"),
        interference_type=np.zeros((n_rays, n_gates), "uint8"),
    )


def _result(sweeps):
    from datetime import UTC, datetime

    from rainpulse_algo.radar.qc import QCResult

    return QCResult(
        profile=_ProfileStub(), sweeps=tuple(sweeps), modules=(), health={"health": "HEALTHY"},
        summary={"sweeps": {}}, created_at=datetime.now(UTC),
    )


def _frame_weak(n_rays=8, n_gates=60):
    dbzh = np.full((n_rays, n_gates), np.nan)
    dbzh[:, 12:20] = 0.0    # weak clutter on recurrent footprint rows 0..7
    dbzh[:, 25:30] = 20.0   # strong rain
    eligible = np.zeros((n_rays, n_gates), bool)
    eligible[:, 12:30] = True
    return dbzh, eligible


def test_review_result_applies_exclusion_to_cr_eligibility():
    frac = np.zeros((AZ_BINS, GATE_LIMIT), np.float32)
    frac[0:8, 10:50] = 0.9
    bg = make_background(frac)
    native = _native()
    dbzh, eligible = _frame_weak()
    sweep = _sweep(native, dbzh, eligible)
    result = _result([sweep])
    anchors = [{"sweep_000": {"azimuth": np.array([300.0]),
                              "DBZH": np.array([[25.0, 25.0]])}}]
    out = review_result(result, [native], background=bg, anchor_volumes=anchors)
    arrays = out.sweeps[0].optional_qc_fields
    mask = arrays["CLEARAIR_RECURRENCE_EXCLUDE_MASK"].astype(bool)
    assert mask[:, 12:20].all() and not mask[:, 25:30].any()
    cr = arrays["REFLECTIVITY_ELIGIBLE_FOR_CR"].astype(bool)
    assert not cr[:, 12:20].any() and cr[:, 25:30].all()
    assert arrays["CLEARAIR_RECURRENCE_BEFORE_CR_MASK"].astype(bool)[:, 12:20].all()
    assert out.summary["clearair_recurrence"]["status"] == "APPLIED"
    assert out.summary["clearair_recurrence"]["excluded_gates"] == int(mask.sum())


def test_review_result_respects_protections_and_cf1_strict():
    frac_strict = np.full((AZ_BINS, GATE_LIMIT), 0.6, np.float32)  # above t_clear, below strict
    bg = make_background(frac_strict)
    native = _native()
    dbzh, eligible = _frame_weak()
    protections = {"CF_HARD_WEATHER_MASK": np.zeros(dbzh.shape, bool)}
    protections["CF_HARD_WEATHER_MASK"][:, 12:14] = True
    cf1 = np.zeros(dbzh.shape, np.uint8)
    cf1[:, 14:20] = 1  # CF1 with non-strict background: must stay eligible
    sweep = _sweep(native, dbzh, eligible, cf_class=cf1, protections=protections)
    out = review_result(_result([sweep]), [native], background=bg,
                        anchor_volumes=[{"sweep_000": {"azimuth": np.array([300.0]),
                                                        "DBZH": np.array([[25.0]])}}])
    arrays = out.sweeps[0].optional_qc_fields
    mask = arrays["CLEARAIR_RECURRENCE_EXCLUDE_MASK"].astype(bool)
    assert not mask.any()  # protections veto 12:14, CF1+non-strict vetoes the rest


def test_review_result_abstains_without_inputs():
    native = _native()
    dbzh, eligible = _frame_weak()
    sweep = _sweep(native, dbzh, eligible)
    out = review_result(_result([sweep]), [native], background=None, anchor_volumes=None)
    assert out.summary["clearair_recurrence"]["status"] == "ABSTAINED_NO_BACKGROUND"
    assert "CLEARAIR_RECURRENCE_EXCLUDE_MASK" not in out.sweeps[0].optional_qc_fields
    out2 = review_result(_result([sweep]), [native], background=make_background(
        np.zeros((AZ_BINS, GATE_LIMIT), np.float32)), anchor_volumes=None)
    assert out2.summary["clearair_recurrence"]["status"] == "ABSTAINED_NO_ANCHOR_WINDOW"


def test_profile_block_preserves_frozen_identity_when_absent():
    base = OpenSourceQCProfile()
    assert base.clearair_recurrence is None
    assert base.parameters_hash == OpenSourceQCProfile().parameters_hash
    enabled = OpenSourceQCProfile(clearair_recurrence=ClearairRecurrenceConfig())
    assert enabled.parameters_hash != base.parameters_hash
