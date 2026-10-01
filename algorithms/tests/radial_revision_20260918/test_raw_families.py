import numpy as np
import pytest
from .conftest import Native, load, evaluate


def short_family(dr=250., spacing=.5):
    r = np.arange(0., 240000., dr)
    z = np.full((13, len(r)), np.nan, 'float32')
    hit = np.zeros(z.shape, bool)
    for j, start in enumerate((50000., 80000., 110000., 140000.)):
        hit[6+j%2, (r >= start) & (r < start+500.)] = True
    z[hit] = 25.
    n = Native(z, dr=dr, start=0.)
    n.azimuth *= spacing
    return n, hit


def detect(n, blocked=None):
    return load('radial_revision.raw_families').detect(
        n, np.zeros(n.shape, bool) if blocked is None else blocked)


@pytest.mark.parametrize('dr,spacing', [(250., .5), (500., 1.)])
def test_subkilometre_fragments_cross_native_rays_and_keep_raw(dr, spacing):
    n, hit = short_family(dr, spacing)
    raw = n.fields['DBZH'].copy()
    a, report = detect(n)
    assert np.array_equal(a['RV2_RAW_FAMILY_MASK']==1, hit)
    assert np.unique(a['RV2_RAW_FAMILY_ID'][hit]).size == 1
    assert np.all(a['RV2_RAW_FAMILY_FRAGMENT_M'][hit] == 500.)
    assert np.all(a['RV2_RAW_FAMILY_SUPPORT_M'][hit] == 2000.)
    assert np.all(a['RV2_RAW_FAMILY_WINDOW_BITS'][hit] == 0)
    assert np.all(a['RV2_RAW_FAMILY_HOLD_REASON'][hit] & 1)
    assert report['action_gates'] == report['filled_gates'] == 0
    assert np.array_equal(n.fields['DBZH'], raw, equal_nan=True)


def test_weather_in_empty_gap_and_acquired_ray_gap_split_family():
    n, hit = short_family()
    barrier = np.zeros(n.shape, bool); barrier[:, 370:380] = True
    a, _ = detect(n, barrier)
    assert np.unique(a['RV2_RAW_FAMILY_ID'][hit]).size >= 2
    n.gap_after[6] = True
    a, _ = detect(n)
    ids6 = a['RV2_RAW_FAMILY_ID'][6][a['RV2_RAW_FAMILY_MASK'][6]==1]
    ids7 = a['RV2_RAW_FAMILY_ID'][7][a['RV2_RAW_FAMILY_MASK'][7]==1]
    assert not np.intersect1d(ids6, ids7).size


def test_fixed_reference_prevents_recursive_ray_and_range_growth():
    n, _ = short_family()
    n.fields['DBZH'][:] = np.nan
    for row, start in ((5, 30000.), (6, 60000.), (7, 90000.), (8, 120000.)):
        n.fields['DBZH'][row, int(start/250.):int(start/250.)+2] = 25.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    a, _ = detect(n)
    assert a['RV2_RAW_FAMILY_ID'][5, 120] != a['RV2_RAW_FAMILY_ID'][7, 360]
    assert not a['RV2_RAW_FAMILY_MASK'][~n.field_available['DBZH']].any()


def test_broad_weather_has_no_narrow_nomination():
    n, _ = short_family()
    n.fields['DBZH'][:] = 25.
    n.field_available['DBZH'][:] = True
    a, _ = detect(n)
    assert not a['RV2_RAW_FAMILY_MASK'].any()


def test_nomination_does_not_change_existing_actions_or_fits():
    n, hit = short_family()
    cfg = load('radial_revision.config').RadialRevisionConfig(mode='experiment_quarantine', fragment_line={})
    prior, _ = evaluate(n, cfg)
    newer = cfg.model_copy(update={'fragment_line':cfg.fragment_line.model_copy(update={'raw_fragment_families_enabled':True})})
    out, _ = evaluate(n, newer)
    assert out['RV2_RAW_FAMILY_MASK'][hit].all()
    for key in prior:
        assert np.array_equal(out[key], prior[key], equal_nan=True), key
    validate = load('radial_revision.validation').validate_revision_fields
    validate(out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))
    out['RV2_RAW_FAMILY_ID'][hit] = 0
    with pytest.raises(ValueError):
        validate(out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))


def test_resource_abstention_is_diagnostic_only_and_explicit():
    n, _ = short_family()
    module = load('radial_revision.raw_families')
    a, report = module.detect(n, np.zeros(n.shape, bool), maximum_fragments=1)
    assert report['status'] == 'resource_abstained'
    assert not a['RV2_RAW_FAMILY_MASK'].any()
    module.validate(a,n.field_available['DBZH'],np.zeros(n.shape,bool))


def test_long_range_chain_cannot_extend_frozen_extent():
    r=np.arange(0.,480000.,250.)
    z=np.full((11,len(r)),np.nan,'float32')
    for start in np.arange(30000.,450000.,30000.):
        z[5,(r>=start)&(r<start+500.)]=25.
    n=Native(z,dr=250.,start=0.)
    a,_=detect(n)
    assert np.max(a['RV2_RAW_FAMILY_SPAN_M'][a['RV2_RAW_FAMILY_MASK']==1])<=180000.
    assert len(np.unique(a['RV2_RAW_FAMILY_ID'][5][a['RV2_RAW_FAMILY_MASK'][5]==1]))>=3


def test_measured_flanks_are_evidence_but_not_independent_action():
    n,hit=short_family()
    n.fields['DBZH'][np.isnan(n.fields['DBZH'])]=0.
    n.field_available['DBZH'][:]=True
    a, _=detect(n)
    assert np.all(a['RV2_RAW_FAMILY_WINDOW20_FRACTION'][hit]==1.)
    assert np.all(a['RV2_RAW_FAMILY_WINDOW60_FRACTION'][hit]==1.)
    assert np.all(a['RV2_RAW_FAMILY_HOLD_REASON'][hit]&1)


def test_continuous_long_raw_fragment_is_tiled_without_losing_origin_length():
    z=np.full((11,2000),np.nan,'float32');z[5]=25.
    n=Native(z,dr=250.,start=0.)
    a,_=detect(n)
    hit=a['RV2_RAW_FAMILY_MASK']==1
    assert hit.sum()>1900
    assert np.max(a['RV2_RAW_FAMILY_SPAN_M'][hit])<=180000.
    assert np.min(a['RV2_RAW_FAMILY_ORIGINAL_FRAGMENT_M'][hit])>450000.
    load('radial_revision.raw_families').validate(a,n.field_available['DBZH'],np.zeros(n.shape,bool))


def test_persisted_source_preserves_nomination_without_source_claim():
    n,hit=short_family()
    config=load('config').SourceReviewConfig(narrow_enabled=False,radial_revision={
        'mode':'experiment_quarantine','fragment_line':{'raw_fragment_families_enabled':True}})
    qualified,arrays,_=load('source').source_additions(n,config,np.zeros(n.shape,bool),np.zeros(n.shape,'float32'))
    assert arrays['RV2_RAW_FAMILY_MASK'][hit].all()
    assert not qualified.any()
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID']=np.zeros(n.shape,'uint32')
    load('source_validation').validate_source_fields(arrays,n.field_available['DBZH'])
