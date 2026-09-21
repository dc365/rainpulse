from dataclasses import replace
import numpy as np
import pytest
from fusion_helpers import cfg,fixture,group,scene,baseline
from volume_review.integration import review_result,root_attributes
from volume_review.validation import validate_serialized
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.integration import attributes
from volume_review.clutter_fusion.validation import validate_serialized as validate_cf
from volume_review.clutter_fusion.disposition import apply,CR
from volume_review.receipts import snapshot_group,npz_bytes,load_npz
from volume_review.composite import build_composite,trace_pixel


@pytest.mark.parametrize('mode',['audit','cr_withhold','quarantine'])
@pytest.mark.parametrize('permuted',[False,True])
@pytest.mark.parametrize('near',[False,True])
@pytest.mark.parametrize('receiver',[False,True])
def test_complete_hook_and_exact_recursive_legacy_state(mode,permuted,near,receiver):
    c=cfg(mode=mode);before,native=fixture(c,permuted=permuted,near=near,receiver=receiver)
    out=review_result(before,native);calls=[]
    def legacy(g,attrs):
        expected=group(before.sweeps[0]);assert set(g)==set(expected)
        for k,v in expected.items():np.testing.assert_equal(g[k],v)
        assert 'qc_clutter_fusion_version' not in attrs;calls.append(1)
    actual=group(out.sweeps[0]);validate_serialized(actual,root_attributes(before.profile),legacy)
    assert calls==[1] and out.summary['clutter_fusion']['confirmed_gates']==0
    snap=snapshot_group(actual);back=load_npz(npz_bytes(snap))
    assert 'CF_CLASS' in back and not any(k.startswith('CF_BEFORE_') for k in back)
    np.testing.assert_equal(back['CF_CLASS'],actual['CF_CLASS'])
    assert 'qc/volume_review/clutter_fusion.json' in out.volume_review_artifacts


@pytest.mark.parametrize('fault',['raw','action','cr','before','evidence','identity','context'])
def test_corrupted_state_rejected(fault):
    c=cfg(mode='quarantine');s=scene(kind='ground',zdr=.2);ev=evaluate_volume([s],c)[0].arrays;b=baseline(s)
    out,_=apply(b,ev,c,low_quality_flag=1024);attrs=attributes(c,1024);attrs['operational_eligible']=False
    row,gate=np.argwhere(out['CF_NONMET_SUPPORTED_MASK']==1)[0]
    if fault=='raw':out['DBZH_RAW'][row,gate]+=1
    if fault=='action':out['QC_ACTION'][row,gate]=0
    if fault=='cr':out[CR][row,gate]=1
    if fault=='before':del out['CF_BEFORE_QC_FLAGS']
    if fault=='evidence':out['CF_NONMET_SUPPORTED_MASK'][row,gate]=0
    if fault=='identity':attrs['qc_clutter_fusion_sha256']='f'*64
    if fault=='context':out['CF_DOPPLER_AGE_S'][row,gate]=-1
    with pytest.raises((ValueError,KeyError)):validate_cf(out,attrs,lambda *_:None)


@pytest.mark.parametrize('limit',['maximum_volume_gates','maximum_sweep_gates','maximum_context_pairs'])
def test_hook_abstention_does_not_change_old_results(limit):
    before,native=fixture(cfg(mode='quarantine',**{limit:1}))
    original=before.profile.volume_review
    before.profile.volume_review=original.model_copy(update={'clutter_fusion':None})
    parent=review_result(before,native)
    before.profile.volume_review=original
    out=review_result(before,native)
    expected=group(parent.sweeps[0]);actual=group(out.sweeps[0])
    for k,v in expected.items():np.testing.assert_equal(actual[k],v)
    assert out.summary['clutter_fusion']['status']=='RESOURCE_LIMIT_ABSTAINED'


class Root(dict):
    def __init__(self,attrs):super().__init__();self.attrs=attrs


def root_fixture():
    before,native=fixture(cfg(mode='cr_withhold'));out=review_result(before,native)
    root=Root({**root_attributes(before.profile),'radar_id':'TEST','scan_id':'scan','asset_id':'asset',
        'site_longitude_deg':0.,'site_latitude_deg':0.,'qc_parameters_sha256':'a'*64})
    root['sweep_number']=np.array([0],dtype='int32')
    root['sweep_000']={**group(out.sweeps[0]),'azimuth':native[0].azimuth,'range':native[0].ranges,'elevation':native[0].elevation}
    return root


def test_composite_all_winners_trace_and_withheld_not_reintroduced():
    r=root_fixture();p=build_composite([r],0,maximum_size=64)
    assert np.isfinite(p.arrays['CR_CLUTTER_WITHHELD']).any()
    for row,col in np.argwhere(np.isfinite(p.arrays['CR_TRUSTED'])):
        assert trace_pixel(p,[r],int(row),int(col))['status']=='RECONSTRUCTED'
    assert 'WINNER_CLUTTER_CLASS' in p.arrays


def test_composite_checks_second_contributor_not_only_winner():
    r=root_fixture();a=r['sweep_000'];b={k:v.copy() for k,v in a.items()}
    b['DBZH_QC']=a['DBZH_QC']-1;b['DBZH_RAW']=a['DBZH_RAW']-1
    r['sweep_number']=np.array([0,1],dtype='int32');r['sweep_001']=b
    p=build_composite([r],0,maximum_size=64)
    mask=np.isfinite(p.arrays['CR_CLUTTER_WITHHELD'])
    assert not np.isfinite(p.arrays['CR_TRUSTED'][mask]).any()


def test_composite_rejects_leak_and_mixed_generations():
    r=root_fixture();a=r['sweep_000'];a[CR][a['CF_CR_WITHHELD_MASK']==1]=1
    with pytest.raises(ValueError,match='leaked'):build_composite([r],0,maximum_size=64)
    r=root_fixture();r2=Root(dict(r.attrs));r2.update(r);r2.attrs['qc_clutter_fusion_sha256']='c'*64
    with pytest.raises(ValueError,match='mixed'):build_composite([r,r2],0,maximum_size=64)
