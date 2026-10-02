"""Repeated original rays need all admissible choices, never first/last selection."""
from dataclasses import replace

import numpy as np
import pytest

from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.source_blocks import detect
from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .helpers import config
from .test_radial_source import narrow_source


def repeated(*, wet=False, seconds=31):
    volume, row = narrow_source()
    cut = volume.sweeps[0]
    other = row + 1
    fields = {k: np.concatenate([a, a[other:other + 1]], axis=0) for k, a in cut.fields.items()}
    if wet:
        for k in ('DBZH', 'SNRH'):
            fields[k][-1] = fields[k][row]
            fields[k][other + 1:other + 7] = fields[k][row]
    cut = replace(cut, fields=fields,
                  azimuth_deg=np.r_[cut.azimuth_deg, cut.azimuth_deg[other] + .005],
                  elevation_deg=np.r_[cut.elevation_deg, cut.elevation_deg[other]],
                  ray_time_epoch=np.r_[cut.ray_time_epoch, cut.ray_time_epoch[other] + seconds])
    return cut, row, other


def proposal(view, row, cfg):
    s = view.sweep
    domain = np.zeros(s.shape, bool)
    domain[view.native_indices == row] = True
    return detect(s, cfg, protected=np.zeros(s.shape, bool), fan=True,
                  family_width_deg=7., domain=domain)[0]


def test_repeated_quiet_shoulder_requires_every_original_choice():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives, consensus
    cut, row, other = repeated()
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    old = adapt(cut, cfg)
    old_mask = detect(old.sweep, cfg, protected=np.zeros(old.sweep.shape, bool),
                      fan=True, family_width_deg=7.)[0]
    assert not old.restore(old_mask)[row].any()  # Actual pre-fix geometry barrier.
    before = {k: a.copy() for k, a in cut.fields.items()}
    choices = alternatives(cut, cfg)
    assert choices.count == 2
    result = consensus(choices, ((v, proposal(v, row, cfg)) for v in choices.views(cut, cfg)))
    assert result[row, 400:].sum() > 200
    assert not result[[other, len(cut.azimuth_deg) - 1]].any()
    for k, a in before.items():
        np.testing.assert_array_equal(a, cut.fields[k])


def test_conflicting_original_shoulder_vetoes_consensus():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives, consensus
    cut, row, _ = repeated(wet=True)
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    choices = alternatives(cut, cfg)
    proposals = [(v, proposal(v, row, cfg)) for v in choices.views(cut, cfg)]
    assert proposals[0][1].any() and not proposals[1][1].any()
    assert not consensus(choices, proposals).any()


def test_old_choice_or_incomplete_choice_set_cannot_publish_partial_consensus():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives, consensus
    cut, row, _ = repeated()
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    choices = alternatives(cut, cfg)
    v = next(choices.views(cut, cfg))
    with pytest.raises(ValueError, match='complete'):
        consensus(choices, [(v, proposal(v, row, cfg))])
    with pytest.raises(ValueError, match='choice'):
        consensus(choices, [(v, proposal(v, row, cfg)), (v, proposal(v, row, cfg))])


def test_time_and_choice_caps_are_not_relaxed():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives
    cut, _, _ = repeated(seconds=301)
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    with pytest.raises(ResourceLimit, match='time'):
        alternatives(cut, cfg)
    cut, _, _ = repeated()
    with pytest.raises(ResourceLimit, match='choice'):
        alternatives(cut, cfg, maximum_views=1)


def test_views_bind_frozen_fields_and_geometry_policy():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives
    cut, _, _ = repeated()
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    choices = alternatives(cut, cfg)
    changed = replace(cut, fields={**cut.fields, 'DBZH':cut.fields['DBZH']+1})
    with pytest.raises(ValueError, match='identity'):
        list(choices.views(changed, cfg))
    with pytest.raises(ValueError, match='policy'):
        list(choices.views(cut, config(noise_censor_snr_db=4., radial_source_enabled=True)))


def test_rotation_and_native_reordering_preserve_identity():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives, consensus
    cut, row, _ = repeated()
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    choices = alternatives(cut, cfg)
    before = consensus(choices, ((v, proposal(v, row, cfg)) for v in choices.views(cut, cfg)))
    rotated = replace(cut, azimuth_deg=(cut.azimuth_deg + 359.999) % 360)
    choices = alternatives(rotated, cfg)
    after = consensus(choices, ((v, proposal(v, row, cfg)) for v in choices.views(rotated, cfg)))
    np.testing.assert_array_equal(before, after)


def test_repeated_action_targets_remain_unresolved():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives, consensus
    cut, row, _ = repeated()
    cut = replace(cut, azimuth_deg=np.r_[cut.azimuth_deg,cut.azimuth_deg[row]+.005],
                  elevation_deg=np.r_[cut.elevation_deg,cut.elevation_deg[row]],
                  ray_time_epoch=np.r_[cut.ray_time_epoch,cut.ray_time_epoch[row]+30],
                  fields={k:np.concatenate([a,a[row:row+1]]) for k,a in cut.fields.items()})
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    choices = alternatives(cut,cfg)
    assert choices.count == 4
    result=consensus(choices, ((v,np.ones(v.sweep.shape,bool))
                              for v in choices.views(cut,cfg)))
    assert not result[list(choices.repeated_rows)].any()


def test_protected_source_targets_do_not_gain_action():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives, consensus
    cut, row, _ = repeated()
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    choices = alternatives(cut,cfg)
    pairs=[]
    for v in choices.views(cut,cfg):
        protected=np.zeros(v.sweep.shape,bool)
        protected[v.native_indices==row]=True
        mask=detect(v.sweep,cfg,protected=protected,fan=True,family_width_deg=7.)[0]
        pairs.append((v,mask))
    assert not consensus(choices,pairs)[row].any()


def test_repeated_shoulder_across_north_seam_has_same_native_result():
    from rainpulse_algo.multiband.xqc_v2.native_alternatives import alternatives, consensus
    cut, row, other = repeated()
    cfg = config(noise_censor_snr_db=3., radial_source_enabled=True)
    original = alternatives(cut, cfg)
    baseline = consensus(original, ((v, proposal(v, row, cfg))
                                    for v in original.views(cut, cfg)))
    rotated = replace(cut, azimuth_deg=(cut.azimuth_deg-cut.azimuth_deg[other]-.0025)%360)
    choices = alternatives(rotated, cfg)
    assert choices.count == 2
    assert {other,len(cut.azimuth_deg)-1} == set(choices.groups[0])
    result = consensus(choices, ((v, proposal(v, row, cfg)) for v in choices.views(rotated, cfg)))
    np.testing.assert_array_equal(result, baseline)
