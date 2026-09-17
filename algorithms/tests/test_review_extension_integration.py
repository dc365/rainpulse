from pathlib import Path

import numpy as np

from rainpulse_algo.radar.qc import apply_basic_qc, load_qc_profile
from rainpulse_algo.radar.qc_engine.review import run_manifest
from rainpulse_algo.radar.qc_zarr import build_validated_qc_zarr_store
from .test_qc_opensource import field_case
from .test_radar_qc import synthetic_normalized_fixture


def test_audit_preserves_baseline_actions_and_serializes():
    root = Path(__file__).resolve().parents[2]
    flags = root / 'configs/qc/flag-definitions-v2.yaml'
    baseline = load_qc_profile(root / 'configs/qc/fujian-qc-near-sector-v1.yaml', flags)
    audit = load_qc_profile(root / 'configs/qc/fujian-qc-review-20260917-audit.yaml', flags)
    z, moments = field_case()
    z[:3, :5] = np.nan
    objects = synthetic_normalized_fixture(z, moments=moments)
    old = apply_basic_qc(objects, baseline)
    new = apply_basic_qc(objects, audit)
    assert callable(run_manifest)
    for before, after in zip(old.sweeps, new.sweeps, strict=True):
        for name in ('QC_ACTION', 'QPE_ELIGIBLE_MASK', 'REFLECTIVITY_TRUST_MASK', 'RFI_QUARANTINE_MASK'):
            np.testing.assert_array_equal(before.optional_qc_fields[name], after.optional_qc_fields[name])
        np.testing.assert_array_equal(before.dbzh_qc, after.dbzh_qc)
        assert not after.optional_qc_fields['NP_QUARANTINE_MASK'].any()
    build_validated_qc_zarr_store(objects, new, asset_id='00000000-0000-4000-8000-000000000001', normalized_volume_uri='s3://rainpulse/test/normalized.zarr')
