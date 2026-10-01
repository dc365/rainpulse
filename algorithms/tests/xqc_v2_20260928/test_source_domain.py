"""RAW family bounds constrain held-out references without changing thresholds."""
import numpy as np
import pytest
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.source_blocks import detect
from .helpers import config
from .test_radial_source import narrow_source


def setup():
    v, row = narrow_source()
    cfg = config(noise_censor_snr_db=3, radial_source_enabled=True)
    s = adapt(v.sweeps[0], cfg).sweep
    return v, row, cfg, s


def test_full_domain_preserves_every_baseline_mask_and_model():
    _, _, cfg, s = setup()
    kw = dict(protected=np.zeros(s.shape, bool), fan=True)
    before = s.digest
    baseline, record = detect(s, cfg, **kw)
    bounded, bounded_record = detect(s, cfg, domain=np.ones(s.shape, bool), **kw)
    np.testing.assert_array_equal(baseline, bounded)
    assert record == bounded_record
    assert before == s.digest


def test_domain_limits_targets_and_all_reference_gates():
    _, row, cfg, s = setup()
    domain = np.zeros(s.shape, bool)
    domain[row] = (s.ranges >= 10000) & (s.ranges < 60000)
    mask, record = detect(s, cfg, protected=np.zeros(s.shape, bool), fan=True, domain=domain)
    assert mask.any()
    assert not mask[~domain].any()
    allowed_blocks = set((s.ranges[domain[row]] // cfg.receiver.block_m).astype(int))
    for model in record['models']:
        assert set(model['reference_blocks']) <= allowed_blocks
        assert model['target_block'] not in model['reference_blocks']
        assert all(abs(i-model['target_block']) > cfg.receiver.guard_blocks for i in model['reference_blocks'])


def test_short_domain_cannot_borrow_external_source_support():
    _, row, cfg, s = setup()
    domain = np.zeros(s.shape, bool)
    domain[row] = (s.ranges >= 35000) & (s.ranges < 45000)
    assert not detect(s, cfg, protected=np.zeros(s.shape, bool), fan=True, domain=domain)[0].any()


@pytest.mark.parametrize('invalid', ['wrong_shape', 'not_boolean'])
def test_invalid_domain_is_rejected_before_fitting(invalid):
    _, _, cfg, s = setup()
    domain = np.ones(s.shape[1], bool) if invalid == 'wrong_shape' else np.ones(s.shape, np.uint8)
    with pytest.raises(ValueError, match='source domain'):
        detect(s, cfg, protected=np.zeros(s.shape, bool), fan=True, domain=domain)


def test_sparse_family_does_not_build_empty_reference_lists_for_other_rays():
    from rainpulse_algo.multiband.xqc_v2.source_summary import SourceStatistics
    _,row,cfg,s=setup()
    class Counted(tuple):
        traversals=0
        def __iter__(self):
            self.traversals+=1
            return super().__iter__()
    stats=SourceStatistics.build(s,cfg)
    stats.indices=Counted(stats.indices)
    domain=np.zeros(s.shape,bool);domain[row]=True
    mask,record=detect(s,cfg,protected=np.zeros(s.shape,bool),fan=True,domain=domain,prepared=stats)
    assert mask.any() and record['models']
    assert stats.indices.traversals==1
