# ruff: noqa: E501, I001
from copy import deepcopy
from dataclasses import replace
import numpy as np
import pytest
from rainpulse_algo.multiband.quality import x_qc, accept_s_qc
from rainpulse_algo.multiband.fusion import build_composite
from rainpulse_algo.multiband.fusion_quality import prepare_source, InputReason
from rainpulse_algo.multiband.model import Sweep
from .helpers import volume, station, network, STAMP, TIME, SHA

def compute(vs, net):
    return build_composite(vs, net, 'sx', STAMP, STAMP)

def test_x_alone_and_s_alone_are_valid_no_global_s_requirement():
    for band in ('X', 'S'):
        st = station(band)
        v = volume(st)
        qc = x_qc(v, st, SHA) if band == 'X' else accept_s_qc(v, st, SHA)
        out = compute([qc], network(st))
        assert np.isfinite(out.arrays['CR_DBZH']).any()
        assert out.metadata['operational_eligible'] is False and out.metadata['qpe_eligible'] is False
        assert out.arrays['WINNER_BAND'][0, 0] == (2 if band == 'X' else 1)

def test_network_never_changes_standalone_x_values_and_raw():
    x, s = (station(), station('S'))
    vx = volume(x)
    xq = x_qc(vx, x, SHA)
    before = deepcopy(xq)
    alone = compute([xq], network(x))
    both = compute([accept_s_qc(volume(s, age=120), s, SHA), xq], network(x, s))
    assert np.isfinite(alone.arrays['CR_DBZH']).all()
    for k in xq.sweeps[0].fields:
        np.testing.assert_array_equal(xq.sweeps[0].fields[k], before.sweeps[0].fields[k])
    assert both.metadata['standalone_correction_before_fusion'] is True

def test_unqualified_x_cannot_override_qualified_s():
    x, s = (station(), station('S'))
    v = volume(x, value=90.0)
    v.metadata['radome_status'] = 'unknown'
    xq = x_qc(v, x, SHA)
    sq = accept_s_qc(volume(s, value=30.0), s, SHA)
    out = compute([xq, sq], network(x, s))
    assert out.arrays['CR_DBZH'][0, 0] == pytest.approx(30.0)
    assert out.arrays['WINNER_BAND'][0, 0] == 1
    assert out.arrays['S_SELECTED_WITH_X_AVAILABLE_MASK'][0, 0] == 1
    assert out.arrays['INPUT_QUALITY_REASON'][0, 0] & int(InputReason.RADOME_UNQUALIFIED)

def test_s_missing_and_x_unqualified_produces_no_fake_zero():
    x = station()
    v = volume(x)
    v.metadata['radome_status'] = 'unknown'
    out = compute([x_qc(v, x, SHA)], network(x))
    assert np.isnan(out.arrays['CR_DBZH']).all()
    assert out.arrays['NO_ECHO_MASK'].sum() == 0
    assert out.metadata['uncertain_only_cells'] == 1

def test_s_not_ground_truth_calibration_applies_to_both():
    s = station('S', calibration_verified=False)
    sq = accept_s_qc(volume(s, value=65.0), s, SHA)
    out = compute([sq], network(s))
    assert np.isnan(out.arrays['CR_DBZH']).all()
    assert out.arrays['INPUT_QUALITY_REASON'][0, 0] & int(InputReason.CALIBRATION_UNQUALIFIED)

def test_trust_not_display_and_hidden_candidate_remains_diagnostic():
    x = station()
    v = x_qc(volume(x), x, SHA)
    f = v.sweeps[0].fields
    f['QC_ACTION'][:] = 3
    f['REFLECTIVITY_ELIGIBLE_FOR_CR'][:] = 1
    f['XQC_WITHHELD_MASK'] = np.ones((2, 101), 'uint8')
    f['DBZH_QC'][:] = np.nan
    f['DBZH_QC_DISPLAY'][:] = 90
    out = compute([v], network(x))
    assert np.isnan(out.arrays['CR_DBZH']).all()
    assert out.arrays['CR_UNCERTAIN_DBZH'][0, 0] == 40
    assert out.arrays['NO_ECHO_MASK'].sum() == 0

def test_same_height_quality_selection_not_maximum_z():
    x, s = (station(), station('S'))
    xq = x_qc(volume(x, value=30.0), x, SHA)
    sq = accept_s_qc(volume(s, value=60.0, age=240), s, SHA)
    out = compute([sq, xq], network(x, s))
    assert out.arrays['WINNER_BAND'][0, 0] == 2
    assert out.arrays['CR_DBZH'][0, 0] < 40

def test_s_high_level_can_win_column_despite_newer_x_low_level():
    x, s = (station(), station('S'))
    xq = x_qc(volume(x, value=30.0), x, SHA)
    vs = volume(s, value=60.0)
    vs.sweeps[0].elevation_deg[:] = 20
    sq = accept_s_qc(vs, s, SHA)
    out = compute([sq, xq], network(x, s, levels=(150.0, 1000.0)))
    assert out.arrays['WINNER_BAND'][0, 0] == 1
    assert out.arrays['CR_DBZH'][0, 0] == 60
    assert out.arrays['WINNER_HEIGHT_MSL_M'][0, 0] > 900

def test_expired_s_is_not_retimestamped():
    s = station('S')
    sq = accept_s_qc(volume(s, age=301), s, SHA)
    out = compute([sq], network(s))
    assert np.isnan(out.arrays['CR_DBZH']).all()
    assert out.metadata['skipped'][0]['reason'] == 'expired'

def test_ray_age_not_only_volume_end_controls_use():
    s = station('S')
    v = volume(s, age=0)
    v.metadata['volume_start'] = TIME.replace(hour=7, minute=50).isoformat()
    v.sweeps[0].ray_time_epoch[:] = TIME.timestamp() - 301
    sq = accept_s_qc(v, s, SHA)
    out = compute([sq], network(s))
    assert np.isnan(out.arrays['CR_DBZH']).all()

def test_source_order_and_tile_partition_invariant():
    x, s = (station(), station('S'))
    xq = x_qc(volume(x), x, SHA)
    sq = accept_s_qc(volume(s), s, SHA)
    a = compute([xq, sq], network(x, s, height=3, tile_rows=1))
    b = compute([sq, xq], network(x, s, height=3, tile_rows=3))
    for key in a.arrays:
        np.testing.assert_array_equal(a.arrays[key], b.arrays[key])

def test_missing_path_contract_is_downgraded_not_raw_admitted():
    x = station()
    v = x_qc(volume(x), x, SHA)
    v.sweeps[0].fields.pop('PATH_STATE')
    out = compute([v], network(x))
    assert np.isnan(out.arrays['CR_DBZH']).all()
    assert out.arrays['INPUT_QUALITY_REASON'][0, 0] & int(InputReason.MISSING_PATH_CONTRACT)

def test_malformed_path_contract_is_rejected():
    x = station()
    v = x_qc(volume(x), x, SHA)
    v.sweeps[0].fields['PATH_VALID_MASK'][0, 0] = 2
    with pytest.raises(ValueError):
        compute([v], network(x))

def test_measured_no_echo_and_censored_path_not_collapsed():
    s, x = (station('S'), station())
    sv = volume(s)
    sf = sv.sweeps[0].fields
    sf['NO_ECHO_MASK'][:] = 1
    sf['DBZH'][:] = np.nan
    sf['DBZH_QC'][:] = np.nan
    sq = accept_s_qc(sv, s, SHA)
    out = compute([sq], network(s))
    assert out.arrays['NO_ECHO_MASK'][0, 0] == 1
    xv = volume(x)
    xf = xv.sweeps[0].fields
    xf['OBSERVED_MASK'][:] = 0
    xf['DBZH'][:] = np.nan
    xf['ATTENUATION_UNRELIABLE_MASK'] = np.ones((2, 101), 'uint8')
    xq = x_qc(xv, x, SHA)
    out = compute([sq, xq], network(s, x))
    assert out.arrays['NO_ECHO_MASK'][0, 0] == 0 and out.arrays['UNRESOLVED_MASK'][0, 0] == 1
    assert np.isnan(out.arrays['CR_DBZH']).all()
