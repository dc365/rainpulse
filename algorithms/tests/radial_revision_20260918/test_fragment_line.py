import numpy as np
from .conftest import Native, load, evaluate


def cfg(**kw):
    return load('radial_revision.config').RadialRevisionConfig(
        step=1, mode='experiment_quarantine', fragment_line=kw)


def fragmented():
    z = np.full((15, 240), 5., dtype='float32')
    for a, b in [(20, 32), (40, 52), (60, 72), (80, 92), (100, 112)]:
        z[7, a:b] = 45.
    return Native(z)


def test_broken_line_recognized_without_filling_gaps():
    n = fragmented()
    out, report = load('radial_revision.fragment_line').detect(n, cfg().fragment_line, np.zeros(n.shape, bool))
    hit = out['RV2_LINE_MASK'] == 1
    assert hit.sum() >= 48
    assert not np.any(hit & (n.fields['DBZH'] != 45))
    assert report['accepted_lines'] > 0


def test_morphology_alone_cannot_quarantine_and_audit_is_inert():
    n = fragmented()
    out, _ = evaluate(n, cfg())
    assert out['RV2_LINE_MASK'].sum() > 0
    assert out['RV2_ACTION_PROPOSAL_MASK'].sum() == 0
    source = n.fields['DBZH'] == 45
    out, _ = evaluate(n, cfg(), source)
    assert out['RV2_ACTION_PROPOSAL_MASK'].sum() > 0
    audit = cfg().model_copy(update={'mode': 'audit'})
    out, _ = evaluate(n, audit, source)
    assert out['RV2_ACTION_PROPOSAL_MASK'].sum() == 0


def test_weather_missing_geometry_gap_and_broad_echo_are_protected():
    n = fragmented(); source = n.fields['DBZH'] == 45
    out, _ = evaluate(n, cfg(), source, weather=source)
    assert out['RV2_LINE_MASK'].sum() == 0
    n.field_available['DBZH'][7, 20:32] = False
    out, _ = evaluate(n, cfg(), source)
    assert not out['RV2_LINE_MASK'][7, 20:32].any()
    n.gap_after[6] = True
    out, _ = evaluate(n, cfg(), source)
    assert not out['RV2_LINE_MASK'].any()
    n = Native(np.full((15, 240), 45.))
    out, _ = evaluate(n, cfg(), np.ones(n.shape, bool))
    assert not out['RV2_LINE_MASK'].any()


def test_source_validation_and_repeatability():
    n = fragmented(); source = n.fields['DBZH'] == 45
    a, _ = evaluate(n, cfg(), source); b, _ = evaluate(n, cfg(), source)
    assert np.array_equal(a['RV2_LINE_MASK'], b['RV2_LINE_MASK'])
    validate = load('radial_revision.validation').validate_revision_fields
    validate(a, n.field_available['DBZH'], source, np.zeros(n.shape, bool))
    a['RV2_LINE_MASK'][0, 0] = 1
    import pytest
    with pytest.raises(ValueError):
        validate(a, n.field_available['DBZH'], source, np.zeros(n.shape, bool))


def test_strong_source_has_independent_moment_and_reference_requirements():
    n = fragmented()
    # Raw support outside target blocks, independent of morphology.
    n.fields['DBZH'][7, 20:220] = 45.
    for k, v in dict(SNR=40., PHIDP=276., ZDR=-.5, RHOHV=.999).items():
        n.fields[k] = np.full(n.shape, v, 'float32')
        n.field_available[k] = np.ones(n.shape, bool)
    out, _ = evaluate(n, cfg())
    assert out['RV2_LINE_SOURCE_MASK'][7].sum() > 100
    load('radial_revision.validation').validate_revision_fields(
        out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))
    # A geometric line with evolving weather phase must not acquire this action.
    n.fields['PHIDP'][7] = np.linspace(260, 310, n.shape[1])
    out, _ = evaluate(n, cfg())
    assert not out['RV2_LINE_SOURCE_MASK'].any()
    n.fields['PHIDP'][7] = 276.
    n.fields['SNR'][7] = 5.
    out, _ = evaluate(n, cfg())
    assert not out['RV2_LINE_SOURCE_MASK'].any()


def test_target_cannot_supply_its_own_source_reference():
    n = fragmented()
    for k, v in dict(SNR=40., PHIDP=276., ZDR=-.5, RHOHV=.999).items():
        n.fields[k] = np.full(n.shape, np.nan, 'float32')
        n.fields[k][7, 30:50] = v
        n.field_available[k] = np.isfinite(n.fields[k])
    source = load('radial_revision.fragment_line').coherent_source(
        n, n.fields['DBZH'] == 45, np.zeros(n.shape, bool))
    assert not source['RV2_LINE_SOURCE_MASK'].any()


def test_resource_abstention_retains_legacy_and_config_is_opt_in():
    n = fragmented(); src = n.fields['DBZH'] == 45
    plain = load('radial_revision.config').RadialRevisionConfig(mode='experiment_quarantine')
    out, _ = evaluate(n, plain, src)
    assert 'RV2_LINE_MASK' not in out
    n.fields['DBZH'][3] = n.fields['DBZH'][7]
    out, report = evaluate(n, cfg(maximum_lines=1), src)
    assert report['fragment_line']['status'] == 'resource_limit_abstained'
    assert not out['RV2_LINE_MASK'].any()


def test_final_projection_does_not_restore_line_quarantine():
    from types import SimpleNamespace as S
    import importlib
    n = fragmented(); source = n.fields['DBZH'] == 45
    evidence, _ = evaluate(n, cfg(), source)
    isolation = evidence['RV2_ACTION_PROPOSAL_MASK'] == 1
    assert isolation.any()
    eligible = n.field_available['DBZH'] & ~isolation
    decision = S(arrays={'QPE_ELIGIBLE_MASK': eligible.astype('uint8')},
                 quality=np.ones(n.shape, 'float32'), flags=np.zeros(n.shape, 'uint32'))
    profile = S(generalization=None, health_gate=S(degraded_quality_multiplier=.8),
                quality_index=S(quantitative_minimum=.2, low_quality_threshold=.5),
                flag_masks={'LOW_QUALITY': 1})
    finalize = importlib.import_module('radial_revision_test.engine.finalize').finalize_decision
    finalize(n, decision, profile, {'health': 'HEALTHY'})
    assert not decision.arrays['QPE_ELIGIBLE_MASK'][isolation].any()
    assert np.isnan(decision.arrays['DBZH_USABLE'][isolation]).all()
    assert np.isfinite(decision.arrays['DBZH_USABLE'][eligible]).all()


def test_direct_morphology_quarantines_without_source():
    n = fragmented()
    c = cfg(version='fragment-line-v2', morphology_quarantine_enabled=True)
    out, _ = evaluate(n, c)
    assert out['RV2_LINE_MORPH_MASK'].sum() >= 48
    assert out['RV2_ACTION_PROPOSAL_MASK'].sum() >= 48
    assert not out['RV2_ACTION_PROPOSAL_MASK'][n.fields['DBZH'] != 45].any()
    load('radial_revision.validation').validate_revision_fields(
        out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))
    out, _ = evaluate(n, c, weather=n.fields['DBZH'] == 45)
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()
    out, _ = evaluate(n, c.model_copy(update={'mode': 'audit'}))
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()


def test_unknown_flanks_or_weak_contrast_cannot_supply_direct_action():
    c = cfg(version='fragment-line-v2', morphology_quarantine_enabled=True)
    n = fragmented(); n.field_available['DBZH'][6] = False
    out, _ = evaluate(n, c)
    assert not out['RV2_LINE_MORPH_MASK'].any()
    n = fragmented(); n.fields['DBZH'][n.fields['DBZH'] == 5] = 42.
    out, _ = evaluate(n, c)
    assert not out['RV2_LINE_MORPH_MASK'].any()


def test_production_broad_stage_keeps_direct_action_without_polar_moments():
    import importlib
    module = importlib.import_module('radial_revision_test.engine.broad_source')
    n = fragmented()
    profile = module.BroadSourceConfig(source_review={
        'mode': 'experiment_quarantine',
        'radial_revision': {'mode': 'experiment_quarantine', 'step': 1,
            'fragment_line': {'version': 'fragment-line-v2', 'morphology_quarantine_enabled': True}}})
    fields, report = module.infer_broad_source(n, profile)
    hit = fields['BWS_CANDIDATE_MASK'] == 1
    assert hit.sum() >= 48
    assert not (fields['BWS_REASON'][hit] & int(module.Reason.TARGET_MATCH)).any()
    assert report['status'] == 'missing_moments_morphology_evaluated'


def isolated_scene():
    z = np.full((15, 240), np.nan, 'float32')
    z[7, 20:200] = 20.
    z[7, 90:96] = np.nan
    return Native(z)


def test_isolated_line_without_measured_flanks_is_quarantined():
    n = isolated_scene()
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True)
    out, _ = evaluate(n, c)
    assert out['RV2_LINE_ISOLATED_MASK'].sum() >= 160
    assert out['RV2_ACTION_PROPOSAL_MASK'].sum() >= 160
    assert not out['RV2_ACTION_PROPOSAL_MASK'][~n.field_available['DBZH']].any()
    load('radial_revision.validation').validate_revision_fields(
        out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))
    out, _ = evaluate(n, c.model_copy(update={'mode': 'audit'}))
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()


def test_isolated_policy_preserves_precipitation_intersection_and_weather():
    n = isolated_scene()
    n.fields['DBZH'][3:12, 60:80] = 25.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    weather = np.zeros(n.shape, bool); weather[7, 130:140] = True
    out, _ = evaluate(n, cfg(version='fragment-line-v3', isolated_quarantine_enabled=True), weather=weather)
    assert out['RV2_LINE_ISOLATED_MASK'].sum() > 0
    assert not out['RV2_LINE_ISOLATED_MASK'][:, 60:80].any()
    assert not out['RV2_LINE_ISOLATED_MASK'][weather].any()


def test_geometry_missing_ray_and_short_spot_are_not_isolated_evidence():
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True)
    n = isolated_scene(); n.gap_after[5] = True
    out, _ = evaluate(n, c)
    assert not out['RV2_LINE_ISOLATED_MASK'].any()


def test_hough_partition_does_not_fragment_one_isolated_object(monkeypatch):
    module = load('radial_revision.fragment_line')
    monkeypatch.setattr(module, 'probabilistic_hough_line', lambda *a, **kw:
                       [((20, 7), (65, 7)), ((66, 7), (111, 7)), ((112, 7), (157, 7)), ((158, 7), (199, 7))])
    n = isolated_scene()
    out, _ = evaluate(n, cfg(version='fragment-line-v3', isolated_quarantine_enabled=True))
    assert out['RV2_LINE_ISOLATED_MASK'].sum() >= 160


def test_invalid_flank_and_short_isolated_object_are_preserved():
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True)
    n = isolated_scene(); n.geometry_good[5] = False
    out, _ = evaluate(n, c)
    assert not out['RV2_LINE_ISOLATED_MASK'].any()


def test_bounded_isolated_identity_links_real_fragments_not_empty_interval(monkeypatch):
    module = load('radial_revision.fragment_line')
    monkeypatch.setattr(module, 'probabilistic_hough_line', lambda *a, **kw:
                       [((20, 7), (65, 7)), ((91, 7), (136, 7))])
    n = isolated_scene(); n.fields['DBZH'][7] = np.nan
    n.fields['DBZH'][7, 20:66] = 20.; n.fields['DBZH'][7, 91:137] = 20.
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True)
    out, _ = evaluate(n, c)
    assert out['RV2_LINE_ISOLATED_MASK'].sum() == 92
    assert not out['RV2_ACTION_PROPOSAL_MASK'][7, 66:91].any()
    narrow_gap = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True, isolated_link_gap_m=10000.)
    out, _ = evaluate(n, narrow_gap)
    assert not out['RV2_LINE_ISOLATED_MASK'].any()
    n = isolated_scene(); n.fields['DBZH'][7, 60:] = np.nan
    n.field_available['DBZH'] = np.isfinite(n.fields['DBZH'])
    out, _ = evaluate(n, c)
    assert not out['RV2_LINE_ISOLATED_MASK'].any()


def test_sparse_weak_isolated_fragments_and_barriers():
    z = np.full((15, 400), np.nan, dtype='float32')
    for start in (20, 90, 160, 230, 300):
        z[7, start:start+4] = 5.
    n = Native(z)
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True,
            sparse_isolated_enabled=True)
    out, _ = evaluate(n, c)
    assert out['RV2_LINE_ISOLATED_MASK'].sum() == 20
    assert out['RV2_ACTION_PROPOSAL_MASK'].sum() == 20
    assert not np.any(out['RV2_ACTION_PROPOSAL_MASK'] & ~np.isfinite(z))
    out, _ = evaluate(n, c, weather=np.isfinite(z))
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()
    n.gap_after[6] = True
    out, _ = evaluate(n, c)
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()
    # A single short isolated weather echo must not qualify.
    z[7, 90:] = np.nan
    out, _ = evaluate(Native(z), c)
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()


def test_sparse_edge_requires_independent_core_and_preserves_weather():
    z = np.full((15, 400), np.nan, dtype='float32')
    z[5:8, 30:350] = np.linspace(45., 60., 320)
    z[8, 30:350] = np.linspace(3., 15., 320)
    n = Native(z)
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True,
            sparse_isolated_enabled=True)
    core = np.zeros(n.shape, bool); core[5:8, 30:350] = True
    out, _ = evaluate(n, c, core)
    assert out['RV2_LINE_ISOLATED_MASK'][8, 30:350].all()
    assert not np.any(out['RV2_LINE_ISOLATED_MASK'] & ~np.isfinite(z))
    out, _ = evaluate(n, c)
    assert not out['RV2_LINE_ISOLATED_MASK'][8].any()
    weather = np.zeros(n.shape, bool); weather[8, 100:150] = True
    out, _ = evaluate(n, c, core, weather=weather)
    assert not out['RV2_ACTION_PROPOSAL_MASK'][8, 100:150].any()
    z[9:12, 30:350] = 8.  # A broad echo cannot inherit the core's action.
    out, _ = evaluate(Native(z), c, core)
    assert not out['RV2_LINE_ISOLATED_MASK'][8].any()


def test_group_strip_needs_gatewise_polar_evidence_and_no_fill():
    z = np.full((25, 400), np.nan, dtype='float32')
    z[8:16, 60:350] = np.linspace(10., 30., 290)
    z[8:16, 150:152] = np.nan
    n = Native(z, fields={'SNR': 15., 'RHOHV': .6, 'ZDR': 0.})
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True,
            sparse_isolated_enabled=True)
    out, _ = evaluate(n, c)
    assert out['RV2_GROUP_MASK'].sum() > 1000
    assert out['RV2_GROUP_POLAR_MASK'].sum() > 1000
    assert not np.any(out['RV2_ACTION_PROPOSAL_MASK'] & ~np.isfinite(z))
    n.fields['RHOHV'][:] = .99
    out, _ = evaluate(n, c)
    assert out['RV2_GROUP_MASK'].any() and not out['RV2_GROUP_POLAR_MASK'].any()
    n.fields['RHOHV'][:] = .6
    n.field_available['RHOHV'][:] = False
    out, _ = evaluate(n, c)
    assert not out['RV2_GROUP_POLAR_MASK'].any()
    out, _ = evaluate(n, c, weather=np.isfinite(z))
    assert not out['RV2_GROUP_MASK'].any()


def test_group_strip_audit_and_short_geometry_partitions():
    z = np.full((25, 400), np.nan, dtype='float32')
    z[8:16, 60:350] = np.linspace(10., 30., 290)
    n = Native(z, fields={'SNR': 15., 'RHOHV': .6, 'ZDR': 0.})
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True,
            sparse_isolated_enabled=True).model_copy(update={'mode': 'audit'})
    out, _ = evaluate(n, c)
    assert out['RV2_GROUP_POLAR_MASK'].any()
    assert not out['RV2_ACTION_PROPOSAL_MASK'].any()
    load('radial_revision.validation').validate_revision_fields(
        out, n.field_available['DBZH'], np.zeros(n.shape, bool), np.zeros(n.shape, bool))
    # Short groups do not qualify, even if polar values look nonmeteorological.
    n.fields['DBZH'][:, 130:] = np.nan
    n.field_available['DBZH'][:] = np.isfinite(n.fields['DBZH'])
    out, _ = evaluate(n, c)
    assert not out['RV2_GROUP_MASK'].any()


def test_strong_group_morphology_independent_of_polar_and_preserves_barriers():
    z = np.full((25, 400), np.nan, dtype='float32')
    z[8:14, 30:350] = np.linspace(5., 25., 320)
    z[8:14, 150:152] = np.nan
    n = Native(z)
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True,
            sparse_isolated_enabled=True, group_morphology_enabled=True)
    out, _ = evaluate(n, c)
    assert out['RV2_GROUP_MORPH_MASK'].sum() > 1000
    assert not out['RV2_GROUP_POLAR_MASK'].any()
    assert not np.any(out['RV2_ACTION_PROPOSAL_MASK'] & ~np.isfinite(z))
    weather = np.zeros(n.shape, bool); weather[8:14, 180:200] = True
    out, _ = evaluate(n, c, weather=weather)
    assert not np.any(out['RV2_ACTION_PROPOSAL_MASK'] & weather)
    audit, _ = evaluate(n, c.model_copy(update={'mode':'audit'}))
    assert audit['RV2_GROUP_MORPH_MASK'].any() and not audit['RV2_ACTION_PROPOSAL_MASK'].any()
    # Equal measured flanks must not be mistaken for an isolated strip.
    z[:] = 20.
    z[8:14, :] = 25.
    out, _ = evaluate(Native(z), c)
    assert not out['RV2_GROUP_MORPH_MASK'].any()


def test_radial_track_survives_connected_weather_bridge_without_erasing_it():
    z = np.full((35, 450), np.nan, dtype='float32')
    z[10:13, 70:420] = np.linspace(5., 25., 350)
    z[4:29, 210:220] = 25.  # Connect into a component wider than12 rays.
    n = Native(z)
    c = cfg(version='fragment-line-v3', isolated_quarantine_enabled=True,
            sparse_isolated_enabled=True, group_morphology_enabled=True)
    out, _ = evaluate(n, c)
    assert out['RV2_GROUP_MORPH_MASK'][11, 80:200].all()
    assert out['RV2_GROUP_MORPH_MASK'][11, 230:410].all()
    assert not out['RV2_GROUP_MORPH_MASK'][:, 210:220].any()
    assert not np.any(out['RV2_GROUP_MORPH_MASK'] & ~np.isfinite(z))
    n.gap_after[10] = True
    out, _ = evaluate(n, c)
    assert not out['RV2_GROUP_MORPH_MASK'][11].any()


def test_radial_track_offcentre_edge_uses_asymmetric_flanks():
    z = np.full((25, 450), np.nan, dtype='float32')
    z[10:14, 60:430] = np.linspace(10., 25., 370)
    n = Native(z)
    track = load('radial_revision.fragment_line').radial_tracks(n, np.zeros(n.shape,bool))
    assert track[10,60:430].all()
    assert track[13,60:430].all()
    assert not np.any(track & ~np.isfinite(z))
    n.gap_after[9] = True
    track = load('radial_revision.fragment_line').radial_tracks(n, np.zeros(n.shape,bool))
    assert not track[10].any()


def test_window_tracks_short_physical_strip_and_geometry_resolution():
    for dr in (500., 1000.):
        z = np.full((25, int(160000/dr)), np.nan, dtype='float32')
        ranges = 50000+dr*np.arange(z.shape[1])
        target = (ranges >= 70000)&(ranges < 125000)
        z[10:13,target] = np.linspace(10.,25.,target.sum())
        n = Native(z,dr=dr)
        f = load('radial_revision.fragment_line').window_tracks
        hit = f(n,np.zeros(n.shape,bool))
        assert hit[11,target].mean() > .8
        assert not np.any(hit & ~np.isfinite(z))
        blocked=np.zeros(n.shape,bool);blocked[10:13,target]=True
        assert not f(n,blocked).any()
        n.gap_after[10]=True
        assert not f(n,np.zeros(n.shape,bool))[11].any()


def test_window_tracks_angular_resolution_and_broad_weather():
    f = load('radial_revision.fragment_line').window_tracks
    fractions=[]
    for step in (.5,1.):
        angles=np.arange(0,25,step)
        z=np.full((len(angles),320),np.nan,dtype='float32')
        hit=(angles>=10)&(angles<13)
        z[hit,40:150]=np.linspace(10.,25.,110)
        n=Native(z,dr=500.);n.azimuth=angles
        result=f(n,np.zeros(n.shape,bool))
        fractions.append(result[hit,50:140].mean())
        assert not np.any(result&~np.isfinite(z))
        n.fields['DBZH'][:]=20.
        n.field_available['DBZH'][:]=True
        assert not f(n,np.zeros(n.shape,bool)).any()
    assert min(fractions)>.8
    assert abs(fractions[0]-fractions[1])<.15


def test_variable_width_track_follows_edges_and_keeps_holes():
    z=np.full((25,400),np.nan,dtype='float32')
    for k in range(30,350):
        half=1+(k//15)%2
        z[12-half:13+half,k]=10.+k*.03
    z[:,180:184]=np.nan
    n=Native(z)
    f=load('radial_revision.fragment_line').variable_tracks
    out=f(n,np.zeros(n.shape,bool))
    assert out[12,40:170].all()
    assert out[12,200:340].all()
    assert not np.any(out&~np.isfinite(z))
    blocked=np.isfinite(z)
    assert not f(n,blocked).any()


def test_track_diagnostics_and_short_scale_stricter_support():
    z=np.full((25,180),np.nan,dtype='float32')
    z[12,20:100]=np.linspace(10.,25.,80)
    n=Native(z,dr=250.,start=20000.)
    f=load('radial_revision.fragment_line').window_tracks
    hit,e=f(n,np.zeros(n.shape,bool),beam_width=1.,details=True)
    assert hit.any()
    assert np.all(e['RV2_WINDOW_LEFT_DEG'][hit]<e['RV2_WINDOW_RIGHT_DEG'][hit])
    assert np.all(e['RV2_WINDOW_BEAM_PROXY_DEG'][hit]==1.)
    assert np.all(e['RV2_WINDOW_LEFT_MISSING_FRACTION'][hit]==1.)
    assert np.isnan(e['RV2_WINDOW_SCALE_M'][~hit]).all()
    # Sparse short fragments do not meet the tighter short-scale occupancy.
    n.fields['DBZH'][12,::2]=np.nan
    n.field_available['DBZH'][:]=np.isfinite(n.fields['DBZH'])
    assert not f(n,np.zeros(n.shape,bool)).any()
