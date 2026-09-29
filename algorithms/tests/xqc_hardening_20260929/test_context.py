from dataclasses import replace
import copy
from types import SimpleNamespace
import numpy as np
import pytest
from rainpulse_algo.multiband.xqc_v2.limited_context import (
    Donor, ContextBundle, context_for_cut, evaluate_context, GroupContextProvider, cut_bytes,
)
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.quality import x_qc
from .helpers import fixture,config,station


def inputs():
    v,_=fixture('weather',rays=120,gates=400)
    v.metadata['clutter_context_contract']={'beam_width_deg':1.,'beam_source':'verified-fixture'}
    donor=copy.deepcopy(v.sweeps[0]);donor.number=5;donor.elevation_deg[:]=2.5
    cfg=config(context={'mode':'audit'})
    s=adapt(v.sweeps[0],cfg).sweep
    return v,s,cfg,Donor(donor,copy.deepcopy(v.metadata))


def test_real_geometry_context_is_positive_only_and_traceable():
    v,s,c,d=inputs()
    positive,rec,measured,idx,row,gate=evaluate_context(s,v.metadata,c,ContextBundle((d,),{'status':'BOUND'}))
    assert positive.any() and measured.any()
    assert (idx[positive]==0).all() and (row[positive]>=0).all() and (gate[positive]>=0).all()
    assert rec['donors'][0]['asset_sha256']==v.metadata['asset_sha256']
    assert not rec['unknown_is_no_echo']


@pytest.mark.parametrize('case',['processing','station','asset','self','future','missingtime','overlap'])
def test_incompatible_donors_cannot_add_context(case):
    v,s,c,d=inputs();m=d.metadata
    if case=='processing':m['radar_config_version']='other'
    elif case=='station':m['radar_id']='another'
    elif case=='asset':m['asset_sha256']='b'*64
    elif case=='self':d.cut.number=0
    elif case=='future':m['available_at']='2026-08-28T00:02:00Z'
    elif case=='missingtime':del m['volume_end']
    elif case=='overlap':m['scan_id']='past';m['volume_end']='2026-08-28T00:00:30Z'
    assert not evaluate_context(s,v.metadata,c,ContextBundle((d,),{}))[0].any()


@pytest.mark.parametrize('case',['no_beam','missing_ref','low_snr','bad_zdr','same_level','bad_time','mask_invalid'])
def test_no_unsupported_weather_or_absence_inference(case):
    v,s,c,d=inputs()
    if case=='no_beam':d.metadata.pop('clutter_context_contract')
    elif case=='missing_ref':d.cut.fields['DBZH'][:]=np.nan;d.cut.fields['OBSERVED_MASK'][:]=0
    elif case=='low_snr':d.cut.fields['SNRH'][:]=2
    elif case=='bad_zdr':d.cut.fields['ZDR'][:]=6
    elif case=='same_level':d.cut.elevation_deg[:]=.5
    elif case=='bad_time':d.cut.ray_time_epoch[:]+=10000
    elif case=='mask_invalid':d.cut.fields['RHOHV_VALID_MASK']=np.zeros(s.shape,np.uint8)
    a=evaluate_context(s,v.metadata,c,ContextBundle((d,),{}))
    assert not a[0].any()
    if case=='no_beam':assert a[2].any() # measured is not action-grade


@pytest.mark.parametrize('key,val',[('maximum_pairs',1),('maximum_bytes',4096),('maximum_donors',1)])
def test_context_resource_limits_abstain_before_bulk_computation(key,val):
    v,s,c,d=inputs();more=copy.deepcopy(d);more.cut.number=7
    c=config(context={'mode':'audit',key:val})
    a=evaluate_context(s,v.metadata,c,ContextBundle((d,more),{}))
    assert not a[0].any() and 'BUDGET' in a[1]['status']


def test_disabled_context_never_calls_reader():
    v,_,_,_=inputs()
    class Provider:
        def load(self,*args):raise AssertionError('disabled read')
    v.xqc_context_provider=Provider()
    bundle=context_for_cut(v,v.sweeps[0],config())
    assert not bundle.donors and bundle.record['status']=='DISABLED'


def test_past_donor_must_be_strictly_causal():
    v,s,c,d=inputs();d.metadata.update(scan_id='previous',asset_sha256='b'*64,
        volume_start='2026-08-27T23:58:00Z',volume_end='2026-08-27T23:59:00Z',available_at='2026-08-27T23:59:01Z')
    d.cut.ray_time_epoch-=120
    out=evaluate_context(s,v.metadata,c,ContextBundle((d,),{}))
    assert out[0].any()
    assert out[1]['donors'][0]['scan_id']=='previous'


def test_positive_donor_mapping_not_overwritten_by_later_unrelated_measurement():
    v,s,c,d=inputs();bad=copy.deepcopy(d);bad.cut.number=9;bad.cut.fields['ZDR'][:]=6
    a=evaluate_context(s,v.metadata,c,ContextBundle((d,bad),{}))
    assert a[0].any() and (a[3][a[0]]==0).all()


def test_context_audit_leaves_actions_unchanged():
    v,_,c,d=inputs();v.sweeps.append(d.cut)
    off=config(mode='quarantine',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    on=off.model_copy(update={'context':c.context})
    a=x_qc(v,station(off),'a'*64);b=x_qc(v,station(on),'a'*64)
    for x,y in zip(a.sweeps,b.sweeps):
        for key in ['QC_ACTION','DBZH_QC','REFLECTIVITY_ELIGIBLE_FOR_CR','QPE_ELIGIBLE_MASK']:
            np.testing.assert_array_equal(x.fields[key],y.fields[key])
    assert b.sweeps[0].fields['XQC_CONTEXT_MEASURED_MASK'].any()


def test_group_provider_selects_actual_height_not_cut_number():
    v,_,c,d=inputs()
    wrong=copy.deepcopy(d.cut);wrong.number=1;wrong.elevation_deg[:]=5.
    lower=copy.deepcopy(d.cut);lower.number=2;lower.elevation_deg[:]=.2
    values={0:v.sweeps[0],1:wrong,2:lower,5:d.cut}
    class Group(dict):
        def array_keys(self):return list(self)
    root={f'sweep_{k:03d}':Group(elevation=x.elevation_deg,DBZH=x.fields['DBZH']) for k,x in values.items()}
    calls=[]
    def read(n):
        calls.append(n);value=copy.deepcopy(v);value.sweeps=[values[n]];return value
    provider=GroupContextProvider(SimpleNamespace(root=root,numbers=[0,1,2,5],read=read))
    policy=c.context.model_copy(update={'maximum_donors':1})
    out=provider.load(v.sweeps[0],v.metadata,policy)
    assert calls==[5] and out.donors[0].cut.number==5


def test_mixed_review_downgrades_rejection_but_never_restores_cr(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import radial_source
    v,_,audit,d=inputs();v.sweeps.append(d.cut)
    # Isolate finalizer behavior with a supplied source candidate, not a claim
    # that this synthetic weather fixture is a real source.
    def source(s,cfg,*,protected,details=None):
        proposed=np.zeros(s.shape,bool)
        if s.name=='sweep_000':proposed[:,80:250]=True
        if details is not None:details['source_kind']=proposed.astype(np.uint8)
        return proposed,{'status':'TEST_SUPPLIED_SOURCE','source_gates':int(proposed.sum())}
    monkeypatch.setattr(radial_source,'detect',source)
    base=dict(mode='quarantine',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    off=config(**base);on=config(**base,context={'mode':'mixed_review'})
    a=x_qc(v,station(off),'a'*64).sweeps[0].fields
    b=x_qc(v,station(on),'a'*64).sweeps[0].fields
    mixed=b['XQC_SOURCE_MIXED_MASK']==1
    assert mixed.any() and (a['QC_ACTION'][mixed]==2).all() and (b['QC_ACTION'][mixed]==3).all()
    np.testing.assert_array_equal(a['XQC_WITHHELD_MASK'],b['XQC_WITHHELD_MASK'])
    assert not b['REFLECTIVITY_ELIGIBLE_FOR_CR'][mixed].any() and not b['QPE_ELIGIBLE_MASK'].any()


def test_group_binding_is_lazy_and_keeps_metadata_plain():
    from rainpulse_algo.multiband.xqc_v2.limited_context import bind_group_context, use_context_streaming
    v,_,c,_=inputs();m=copy.deepcopy(v.metadata)
    cuts=SimpleNamespace(station=station(c))
    assert bind_group_context(v,cuts) is v and v.metadata==m
    assert isinstance(v.xqc_context_provider,GroupContextProvider)
    assert use_context_streaming(station(c))
    assert not use_context_streaming(station(config()))
