import numpy as np
from .conftest import Native, load, evaluate


def case(dr=1000.):
    r=np.arange(0.,180000.,dr)
    z=np.full((9,len(r)),np.nan,'float32')
    source=np.zeros(z.shape,bool)
    source[4,(r>=20000)&(r<40000)]=True
    echo=((r>=20000)&(r<40000))|((r>=55000)&(r<60000))|((r>=85000)&(r<90000))
    z[4,echo]=30+r[echo]/10000
    return Native(z,dr=dr,start=0),source


def run(n,source,blocked=None):
    return load('radial_revision.residual_objects').detect(
        n,np.zeros(n.shape,bool) if blocked is None else blocked,source)[0]


def test_short_tail_links_without_recursive_growth_or_gap_filling():
    n,s=case();o=run(n,s)
    hit=o['RV2_RESIDUAL_LINK_MASK'].astype(bool)
    assert hit[4,55:60].all()
    assert not hit[4,85:90].any()  # must not extend from the newly linked tail
    assert not np.any(hit & ~n.field_available['DBZH'])
    assert not np.any(hit & s)


def test_missing_shoulders_alone_are_not_evidence():
    n,s=case();o=run(n,np.zeros(n.shape,bool))
    assert not o['RV2_RESIDUAL_LINK_MASK'].any()
    assert not o['RV2_RESIDUAL_DIRECT_MASK'].any()


def test_weather_barrier_cuts_source_association():
    n,s=case();b=np.zeros(n.shape,bool);b[:,45:48]=True
    assert not run(n,s,b)['RV2_RESIDUAL_LINK_MASK'].any()


def test_resolution_and_station_name_do_not_control_detection():
    for dr in (500.,1000.,2000.):
        n,s=case(dr);n.attrs['radar_id']='ANOTHER_SITE'
        hit=run(n,s)['RV2_RESIDUAL_LINK_MASK'][4].astype(bool)
        assert hit[(n.ranges>=55000)&(n.ranges<60000)].all()
        assert not hit[n.ranges>=85000].any()


def test_measured_short_line_and_broad_weather():
    z=np.full((9,100),5.,'float32');z[4,5:30]=30.
    n=Native(z,start=2000.);s=np.zeros(n.shape,bool)
    o=run(n,s)
    assert o['RV2_RESIDUAL_DIRECT_MASK'][4,10:20].any()
    n.fields['DBZH'][:]=30.
    assert not run(n,s)['RV2_RESIDUAL_DIRECT_MASK'].any()


def test_engine_audit_and_serialized_barriers():
    n,s=case()
    cfg=load('radial_revision.config').RadialRevisionConfig(
        mode='experiment_quarantine',fragment_line={'residual_objects_enabled':True})
    out,_=evaluate(n,cfg,s)
    assert out['RV2_RESIDUAL_LINK_MASK'][4,55:60].all()
    assert out['RV2_ACTION_PROPOSAL_MASK'][4,55:60].all()
    load('radial_revision.validation').validate_revision_fields(out,n.field_available['DBZH'],s,np.zeros(n.shape,bool))
    audit,_=evaluate(n,cfg.model_copy(update={'mode':'audit'}),s)
    assert not audit['RV2_ACTION_PROPOSAL_MASK'].any()


def test_adjacent_ray_variable_width_tracking_and_barriers():
    n,s=case()
    n.fields['DBZH'][4,55:60]=np.nan
    n.fields['DBZH'][5,45:60]=35.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    o=run(n,s)
    assert o['RV2_RESIDUAL_TRACK_MASK'][5,50:60].all()
    assert (o['RV2_RESIDUAL_TRACK_PARENT_ID'][5,50:60]>0).all()
    assert not o['RV2_RESIDUAL_LINK_MASK'][4,85:90].any()
    b=np.zeros(n.shape,bool);b[5,41:44]=True
    assert not run(n,s,b)['RV2_RESIDUAL_TRACK_MASK'][5,50:60].any()
    n.gap_after[4]=True
    assert not run(n,s)['RV2_RESIDUAL_TRACK_MASK'][5,50:60].any()


def test_tracker_rejects_broad_patch_and_unbounded_drift():
    n,s=case()
    n.fields['DBZH'][1:8,45:60]=35.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    assert not run(n,s)['RV2_RESIDUAL_TRACK_MASK'][:,45:60].any()
    n,s=case();n.fields['DBZH'][6,45:60]=35.
    n.field_available['DBZH']=np.isfinite(n.fields['DBZH'])
    assert not run(n,s)['RV2_RESIDUAL_TRACK_MASK'][6,45:60].any()


def span_case(*, length=40, neighbour_dbz=None, snr=4.):
    r=np.arange(0.,180000.,1000.)
    z=np.full((9,len(r)),np.nan,'float32')
    z[4,20:20+length]=25.
    if neighbour_dbz is not None:
        z[3,20:20+length]=neighbour_dbz
        z[5,20:20+length]=neighbour_dbz
    n=Native(z,dr=1000.,start=0,fields={'SNR': np.where(np.isfinite(z),snr,np.nan)})
    return n


def span(n, source=None):
    s=np.zeros(n.shape,bool) if source is None else source
    return load('radial_revision.residual_objects').detect(
        n,np.zeros(n.shape,bool),s,span_minimum_m=15000.)[0]


def test_long_isolated_ray_is_removed_as_one_object():
    n=span_case()
    o=span(n)
    hit=o['RV2_RESIDUAL_SPAN_MASK'].astype(bool)
    assert hit[4,20:60].all()
    assert (o['RV2_RESIDUAL_OBJECT_ID'][4,20:60]>0).all()
    assert np.array_equal(hit, o['RV2_RESIDUAL_LINK_MASK'].astype(bool))
    assert not np.any(hit & ~n.field_available['DBZH'])


def test_short_or_supported_rays_are_kept():
    assert not span(span_case(length=6))['RV2_RESIDUAL_SPAN_MASK'].any()
    assert not span(span_case(neighbour_dbz=25.))['RV2_RESIDUAL_SPAN_MASK'].any()
    assert not span(span_case(neighbour_dbz=24.))['RV2_RESIDUAL_SPAN_MASK'].any()


def test_measured_weather_support_keeps_long_ray():
    assert not span(span_case(snr=45.))['RV2_RESIDUAL_SPAN_MASK'].any()


def test_span_action_and_validation_contract():
    n=span_case()
    cfg=load('radial_revision.config').RadialRevisionConfig(
        mode='experiment_quarantine',
        fragment_line={'residual_objects_enabled':True,'residual_span_enabled':True})
    out,detail=evaluate(n,cfg)
    assert out['RV2_RESIDUAL_SPAN_MASK'][4,20:60].all()
    assert out['RV2_ACTION_PROPOSAL_MASK'][4,20:60].all()
    assert detail['fragment_line']['residual_objects']['span_gates']>=40
    load('radial_revision.validation').validate_revision_fields(
        out,n.field_available['DBZH'],np.zeros(n.shape,bool),np.zeros(n.shape,bool))
    off=load('radial_revision.config').RadialRevisionConfig(
        mode='experiment_quarantine',
        fragment_line={'residual_objects_enabled':True})
    assert not evaluate(n,off)[0]['RV2_RESIDUAL_SPAN_MASK'].any()
