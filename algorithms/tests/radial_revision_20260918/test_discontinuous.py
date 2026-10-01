import numpy as np
import pytest
from .conftest import Native, load, evaluate


def fragmented(dr=1000., spacing=1., measured=False):
    r = np.arange(0., 400000., dr)
    z = np.full((11, len(r)), 0. if measured else np.nan, 'float32')
    hit = np.zeros(z.shape, bool)
    for start in (120000., 170000., 220000., 270000., 320000.):
        hit[5, (r >= start) & (r < start + 2000.)] = True
    z[hit] = 30.
    n = Native(z, dr=dr, start=0., fields={'SNR': 5.})
    n.azimuth *= spacing
    return n, hit


def cfg():
    return load('radial_revision.config').RadialRevisionConfig(
        mode='experiment_quarantine', fragment_line={
            'residual_objects_enabled': True, 'discontinuous_tracks_enabled': True})


@pytest.mark.parametrize('dr,spacing', [(250., .5), (500., 1.), (1000., 1.)])
@pytest.mark.parametrize('measured', [False, True])
def test_sparse_native_chain_without_receiver_moments(dr, spacing, measured):
    n, hit = fragmented(dr, spacing, measured)
    raw = n.fields['DBZH'].copy()
    out, report = evaluate(n, cfg())
    assert np.array_equal(out['RV2_DISCONTINUOUS_CANDIDATE_MASK'] == 1, hit)
    assert np.all(out['RV2_DISCONTINUOUS_MASK'][hit] == int(measured))
    assert np.all(out['RV2_ACTION_PROPOSAL_MASK'][hit] == int(measured))
    assert np.all(out['RV2_GEOMETRY_ACTION_MASK'][hit] == int(measured))
    assert np.all(out['RV2_WEAK_CANDIDATE_MASK'][hit] == 1)
    assert report['geometry_weak_actions'] == (hit.sum() if measured else 0)
    assert not out['RV2_ACTION_PROPOSAL_MASK'][~hit].any()
    assert np.array_equal(n.fields['DBZH'], raw, equal_nan=True)
    load('radial_revision.validation').validate_revision_fields(
        out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))


@pytest.mark.parametrize('key', ['weather', 'conflicts'])
def test_barrier_between_fragments_prevents_object_association(key):
    n, hit = fragmented()
    barrier = np.zeros(n.shape, bool); barrier[:, 190:195] = True
    out, _ = evaluate(n, cfg(), **{key: barrier})
    assert not out['RV2_DISCONTINUOUS_MASK'].any()


def test_unknown_flanks_need_two_acquired_rays_and_no_geometry_gap():
    n, _ = fragmented(); n.gap_after[4] = True
    out, _ = evaluate(n, cfg())
    assert not out['RV2_DISCONTINUOUS_MASK'].any()


def test_broad_weather_short_object_and_excessive_gap_are_not_chains():
    for kind in ('broad', 'short', 'gap'):
        n, hit = fragmented()
        if kind == 'broad':
            n.fields['DBZH'][2:9, hit[5]] = 30.
        elif kind == 'short':
            n.fields['DBZH'][:, 200:] = np.nan
        else:
            n.fields['DBZH'][:, 169:273] = np.nan
        n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
        out, _ = evaluate(n, cfg())
        assert not out['RV2_DISCONTINUOUS_MASK'].any()


def test_audit_and_source_only_weak_hypothesis_have_no_action():
    n, hit = fragmented()
    out, _ = evaluate(n, cfg().model_copy(update={'mode': 'audit'}))
    assert out['RV2_DISCONTINUOUS_CANDIDATE_MASK'].any()
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()
    plain = load('radial_revision.config').RadialRevisionConfig(mode='experiment_quarantine')
    out, _ = evaluate(n, plain, hit)
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()


def test_serialized_geometry_action_cannot_exceed_evidence():
    n, hit = fragmented(); out, _ = evaluate(n, cfg())
    out['RV2_GEOMETRY_ACTION_MASK'][5, 10] = 1
    with pytest.raises(ValueError):
        load('radial_revision.validation').validate_revision_fields(
            out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))


def test_existing_strict_morphology_is_not_vetoed_by_receiver_weakness():
    from .test_fragment_line import fragmented as measured_line
    n = measured_line()
    config = load('radial_revision.config').RadialRevisionConfig(
        mode='experiment_quarantine', fragment_line={
            'version': 'fragment-line-v2', 'morphology_quarantine_enabled': True})
    out, report = evaluate(n, config)
    geometry = out['RV2_LINE_MORPH_MASK'] == 1
    assert geometry.sum() >= 48
    assert out['RV2_WEAK_CANDIDATE_MASK'][geometry].all()
    assert out['RV2_ACTION_PROPOSAL_MASK'][geometry].all()
    assert report['receiver_weak_actions'] == 0
    weather = n.fields['DBZH'] == 45.
    guarded, _ = evaluate(n, config, weather=weather)
    assert not guarded['RV2_ACTION_PROPOSAL_MASK'][weather].any()


def test_source_additions_and_persisted_source_contract_keep_geometry():
    n, hit = fragmented(measured=True)
    c = load('config').SourceReviewConfig(
        narrow_enabled=False, radial_revision=cfg())
    qualified, arrays, _ = load('source').source_additions(
        n, c, np.zeros(n.shape, bool), np.zeros(n.shape, 'float32'))
    assert np.array_equal(qualified, hit)
    arrays['SRC_REVIEW_REFERENCE_FOLD_ID'] = np.zeros(n.shape, 'uint32')
    load('source_validation').validate_source_fields(arrays, n.field_available['DBZH'])


def test_missing_one_flank_stays_candidate_even_with_radial_alignment():
    n, hit = fragmented(measured=True)
    n.field_available['DBZH'][4] = False
    n.field_available['DBZH'][3] = False
    out, _ = evaluate(n, cfg())
    assert out['RV2_DISCONTINUOUS_CANDIDATE_MASK'][hit].all()
    assert not out['RV2_DISCONTINUOUS_MASK'].any()


def test_unanchored_candidate_requires_two_measured_distance_scales():
    n, hit = fragmented(measured=True)
    # One measured target remains in each two-km fragment: insufficient local
    # bilateral coverage, though the original geometric chain still exists.
    n.field_available['DBZH'][4, hit[5]] = np.arange(hit[5].sum()) % 2 == 0
    n.field_available['DBZH'][3] = False
    out, _ = evaluate(n, cfg())
    assert out['RV2_DISCONTINUOUS_CANDIDATE_MASK'][hit].all()
    assert not out['RV2_DISCONTINUOUS_MASK'].any()


def test_independent_weather_support_vetoes_measured_unanchored_object():
    n, hit = fragmented(measured=True)
    out, _ = evaluate(n, cfg(), weather=hit, independent_weather_available=hit)
    assert not out['RV2_ACTION_PROPOSAL_MASK'][hit].any()
    assert out['RV2_INDEPENDENT_WEATHER_AVAILABLE_MASK'][hit].all()
