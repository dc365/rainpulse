"""V3 mechanism regressions for measured peripheral review and multivariate decisions.

Synthetic data only: these tests prove code semantics, not real-weather acceptance.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from rainpulse_algo.radar.qc import load_qc_profile

from .test_rfi_objects_v2 import evaluate, scene

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/qc/fujian-qc-rfi-objects-v3.yaml"
FLAGS = ROOT / "configs/qc/flag-definitions-v2.yaml"


def profile_v3():
    return load_qc_profile(PROFILE, FLAGS)


def test_high_correlation_shape_is_reviewed_not_silently_quantitative():
    obj, target = scene(missing_background=True, rho=0.92)
    _, _, objects, result = evaluate(obj, configured=profile_v3())
    assert objects.candidate[target].all()
    assert not (result.arrays["QC_ACTION"][target] == 2).any()
    assert result.arrays["RFI_QUARANTINE_MASK"][target].all()
    assert not result.arrays["QPE_ELIGIBLE_MASK"][target].any()
    assert np.nanmedian(result.arrays["RFI_V3_EVIDENCE_SCORE"][target]) >= 1.5


def test_protected_high_correlation_weather_is_not_absorbed_by_high_shape_path():
    obj, target = scene(missing_background=True, rho=0.98)
    _, _, objects, result = evaluate(obj, configured=profile_v3())
    assert not objects.candidate[target].any()
    assert not result.arrays["RFI_QUARANTINE_MASK"][target].any()
    assert result.arrays["QPE_ELIGIBLE_MASK"][target].all()


def test_rough_radial_structure_no_longer_fails_the_candidate_entrance():
    obj, target = scene(rho=0.3)
    native, _, _, _ = evaluate(obj, configured=profile_v3())
    # Deterministic non-smooth modulation: V2's strict axial std entrance misses it.
    phase = np.sin(np.arange(native.shape[1]) / 3.0).astype("float32") * 5.0
    from .test_radar_qc import synthetic_normalized_fixture

    changed = synthetic_normalized_fixture(
        native.fields["DBZH"] + phase[None, :],
        azimuth_deg=native.azimuth,
        range_m=native.ranges,
        moments={key: value for key, value in native.fields.items() if key != "DBZH"},
    )
    _, _, objects, result = evaluate(changed, configured=profile_v3())
    observed_target = target & np.isfinite(native.fields["DBZH"])
    assert objects.candidate[observed_target].any()
    assert not result.arrays["QPE_ELIGIBLE_MASK"][observed_target].any()


def test_v2_identity_is_not_mutated_by_v3_profile_support():
    old = load_qc_profile(ROOT / "configs/qc/fujian-qc-rfi-objects-v2.yaml", FLAGS)
    assert old.pipeline_version == "qc-opensource-2.0.0"
    assert old.decision_version == "rfi-objects-v2"
    assert old.rfi_objects.rough_candidate_enabled is False
    assert old.rfi_objects.peripheral_review_enabled is False


def test_v3_quarantine_survives_worker_serialization():
    from rainpulse_algo.radar.qc import apply_basic_qc
    from rainpulse_algo.radar.qc_zarr import build_qc_zarr_store, validate_qc_zarr_store

    obj, _ = scene(missing_background=True, rho=0.92)
    result = apply_basic_qc(obj, profile_v3())
    assert result.sweeps[0].optional_qc_fields['RFI_QUARANTINE_MASK'].any()
    stored = build_qc_zarr_store(obj, result, asset_id='v3-review', normalized_volume_uri='local')
    assert validate_qc_zarr_store(stored)['valid_gate_count'] > 0


def test_v3_loads_radar_geometry(monkeypatch):
    from types import SimpleNamespace

    from rainpulse_algo.radar import qc_worker

    monkeypatch.setenv('RAINPULSE_RADAR_CONFIG_DIR', str(ROOT / 'configs/radars'))
    request = SimpleNamespace(payload=SimpleNamespace(radar_id='z9598'))
    beam, _, directory, _ = qc_worker._load_qc_geometry_resources(request, profile_v3())
    assert directory is not None
    assert beam is not None
