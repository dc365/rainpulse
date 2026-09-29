"""Budget abstention must not promote suspicious gates back to composite input."""
import numpy as np
import pytest
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.experimental import experimental_fields
from rainpulse_algo.multiband.xqc_v2.core import Reason
from .helpers import config, fixture, station


@pytest.mark.parametrize('mode', ['quarantine', 'cr_only', 'audit'])
def test_budget_abstention_preserves_raw_but_withholds_active_candidates(mode):
    volume, _ = fixture('clutter', gates=180)
    raw = volume.sweeps[0].fields['DBZH'].copy()
    cfg = config(mode=mode, receiver_enabled=False, radial_objects_enabled=False,
                 isolation_enabled=False, maximum_new_exclusion_fraction=.001)
    out = x_qc(volume, station(cfg), 'b'*64).sweeps[0]
    fields = out.fields
    budget = (fields['XQC_REASON'] & int(Reason.ACTION_BUDGET)) != 0
    assert budget.any()
    assert out.xqc_diagnostics['status'] == 'ACTION_BUDGET_ABSTAINED'
    np.testing.assert_equal(fields['DBZH_RAW'], raw)
    np.testing.assert_equal(volume.sweeps[0].fields['DBZH'], raw)
    assert not fields['XQC_REJECTED_MASK'][budget].any()
    if mode == 'audit':
        assert not fields['XQC_WITHHELD_MASK'].any()
    else:
        assert fields['XQC_WITHHELD_MASK'][budget].all()
        assert not fields['REFLECTIVITY_ELIGIBLE_FOR_CR'][budget].any()
        assert np.isnan(fields['DBZH_QC_DISPLAY'][budget]).all()
        _, admitted, _ = experimental_fields(fields, 'X')
        assert not admitted[budget].any()
        assert not fields['QPE_ELIGIBLE_MASK'].any()


def test_regular_weather_remains_admitted_with_small_budget():
    volume, _ = fixture('weather', gates=180)
    cfg = config(mode='quarantine', receiver_enabled=False, radial_objects_enabled=False,
                 isolation_enabled=False, maximum_new_exclusion_fraction=.001)
    fields = x_qc(volume, station(cfg), 'b'*64).sweeps[0].fields
    assert not (fields['XQC_REASON'] & int(Reason.ACTION_BUDGET)).any()
    _, admitted, _ = experimental_fields(fields, 'X')
    assert admitted.any()
