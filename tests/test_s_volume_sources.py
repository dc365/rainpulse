import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    's_volume_sources', Path(__file__).resolve().parents[1] / 'scripts/audit_s_volume_sources.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def snapshot(path, sweep, *, measured=True, time_offset=0, scan='same', flag_bit=None,
             ranges=(100125., 100375., 100625.)):
    shape = (3, len(ranges))
    meta = dict(scan_id=scan, radar_id='z9598', raw_artifact_sha256='raw-sha',
                normalized_uri='same-volume', sweep=sweep, elevation_deg=.5+sweep,
                profile_sha256='profile')
    if flag_bit is not None:
        meta['stored_qc_flag_definitions'] = {'RADIAL_INTERFERENCE': flag_bit}
    arrays = dict(RANGE=np.array(ranges), AZIMUTH=np.array([200., 201., 202.]),
                  RAY_TIME=np.full(3, 1787876280.+time_offset),
                  GEOMETRY_GOOD=np.ones(3, bool), RAW=np.ones(shape),
                  AVAILABLE_DBZH=np.full(shape, int(measured), 'uint8'),
                  BEFORE=np.ones(shape, bool), ADDED=np.zeros(shape, bool),
                  RV2_SOURCE_LEDGER_SEED_ID=np.ones(shape, 'uint32'),
                  RV2_SOURCE_LEDGER_KIND=np.ones(shape, 'uint8'),
                  STORED_QC_FLAGS=np.full(shape, flag_bit or 0, 'uint32'),
                  METADATA=np.array(json.dumps(meta)))
    np.savez(path, **arrays)
    return path


def test_missing_measurements_are_not_source_support(tmp_path):
    target = snapshot(tmp_path/'target.npz', 0)
    donor = snapshot(tmp_path/'donor.npz', 2, measured=False, flag_bit=8)
    result = module.audit(target, [donor])
    row = result['donors'][0]
    assert row['native_coverage_remaining_gates'] == 9
    assert row['native_measured_remaining_gates'] == 0
    assert set(row['mapped_source_counts'].values()) == {0}


def test_unknown_flag_definitions_remain_unknown(tmp_path):
    target = snapshot(tmp_path/'target.npz', 0)
    donors = [snapshot(tmp_path/f'donor{cut}.npz', cut) for cut in (2, 4)]
    result = module.audit(target, donors)
    assert result['remaining_supported_by_two_typed_radial_cuts'] is None
    assert all(row['mapped_source_counts']['stored_typed_radial_interference'] is None
               for row in result['donors'])


def test_independent_cuts_time_and_range_boundaries(tmp_path):
    target = snapshot(tmp_path/'target.npz', 0)
    first = snapshot(tmp_path/'first.npz', 2, flag_bit=8)
    second = snapshot(tmp_path/'second.npz', 4, flag_bit=8,
                      ranges=(100125., 100375.))
    assert module.audit(target, [first, second])['remaining_supported_by_two_typed_radial_cuts'] == 6
    late = snapshot(tmp_path/'late.npz', 4, time_offset=301, flag_bit=8)
    assert module.audit(target, [first, late])['remaining_supported_by_two_typed_radial_cuts'] == 0
    with pytest.raises(ValueError, match='unique'):
        module.audit(target, [first, first])
    mismatch = snapshot(tmp_path/'wrong.npz', 4, scan='other')
    with pytest.raises(ValueError, match='identity'):
        module.audit(target, [mismatch])
