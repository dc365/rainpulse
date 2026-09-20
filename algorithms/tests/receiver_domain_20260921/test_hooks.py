from dataclasses import dataclass,replace
from types import SimpleNamespace as NS
import numpy as np
import pytest
from test_receiver import scene,baseline,C,CR
from volume_review.config import VolumeReviewConfig
from volume_review.integration import review_result,root_attributes
from volume_review.validation import validate_serialized
from volume_review.near_measurement.config import NearMeasurementConfig
from volume_review.receiver_domain.integration import protection_masks
from volume_review.composite import build_composite,trace_pixel
from volume_review.receipts import snapshot_group,npz_bytes,load_npz


@dataclass(frozen=True)
class Q:
    name:str
    optional_qc_fields:dict
    dbzh_raw:np.ndarray
    dbzh_qc:np.ndarray
    qc_flags:np.ndarray
    quality_index:np.ndarray
    valid_mask:np.ndarray
    low_quality_mask:np.ndarray
    qi_components:dict


@dataclass(frozen=True)
class Result:
    profile:object
    sweeps:tuple
    summary:dict
    volume_review_artifacts:dict|None=None


class Native:
    def __init__(self,s,permuted=False):
        self.name=s.name;self.azimuth=s.azimuth;self.ranges=s.ranges;self.elevation=s.elevation
        self.fields=s.fields;self.field_available=s.available;self.geometry_good=s.good;self.gap_after=s.gap_after
        self.shape=s.shape;self.original_indices=np.roll(np.arange(s.shape[0]),3) if permuted else np.arange(s.shape[0])
        self.ray_time=np.full(s.shape[0],100.);self.gate_spacing_m=s.dr
    def restore(self,v):
        a=np.empty_like(v);a[self.original_indices]=v;return a


def group(q):
    return {**q.optional_qc_fields,**q.qi_components,"DBZH_RAW":q.dbzh_raw,"DBZH_QC":q.dbzh_qc,
        "QC_FLAGS":q.qc_flags,"QUALITY_INDEX":q.quality_index,"VALID_MASK":q.valid_mask,"LOW_QUALITY_MASK":q.low_quality_mask}


def fixture(mode='quarantine',permuted=False,near=False):
    s=scene();n=Native(s,permuted);b=baseline(s)
    for k in list(b):
        if k.startswith('CR_') or k==CR:del b[k]
    for k in ('SNR','RHOHV','ZDR','PHIDP','VR','SW'):
        b[k+'_TRUST_MASK']=s.moment(k)[1].astype('uint8') & s.observed
    b.update({k+'_RAW':v.copy() for k,v in s.fields.items()})
    b={k:n.restore(v) for k,v in b.items()}
    separate=('DBZH_RAW','DBZH_QC','QC_FLAGS','QUALITY_INDEX','VALID_MASK','LOW_QUALITY_MASK')
    q=Q(n.name,{k:v for k,v in b.items() if k not in separate},*[b[k] for k in separate],{})
    p=NS(volume_review=VolumeReviewConfig(mode='experiment_quarantine',unknown_cr_policy='retain_with_risk',
        receiver_domain=C(mode=mode,local_policy='source_joint_review'),
        near_measurement=NearMeasurementConfig(depolarization_backend='numpy_reference') if near else None),
        context=NS(strong_support=.7),echo=NS(no_rain_below_dbz=-10.),quality_index=NS(quantitative_minimum=.5),flag_masks={'LOW_QUALITY':np.uint32(1024)})
    return Result(p,(q,),{'sweeps':{n.name:{}}}),[n]


@pytest.mark.parametrize('mode',['audit','cr_only','quarantine'])
@pytest.mark.parametrize('permuted',[False,True])
@pytest.mark.parametrize('near',[False,True])
def test_full_hook_and_unchanged_parent_validator(mode,permuted,near):
    before,native=fixture(mode,permuted,near);out=review_result(before,native);calls=[]
    def parent(g,attrs):
        expected=group(before.sweeps[0]);assert set(g)==set(expected)
        for k,v in expected.items():np.testing.assert_equal(g[k],v)
        assert 'qc_receiver_domain_version' not in attrs;calls.append(1)
    a=group(out.sweeps[0]);validate_serialized(a,root_attributes(before.profile),parent)
    assert calls==[1]
    assert (a['RDR_QUARANTINE_MASK'].sum()>0)==(mode=='quarantine')
    assert out.summary['receiver_domain']['confirmed_gates']==0
    snap=snapshot_group(a);rt=load_npz(npz_bytes(snap))
    assert 'RDR_FULL_MATCH_MASK' in rt and not any(k.startswith('RDR_BEFORE_') for k in rt)
    for k,v in snap.items():np.testing.assert_equal(v,rt[k])
    # Every model and measured shoulder index is converted back to acquisition order.
    import json
    data=json.loads(out.volume_review_artifacts['qc/volume_review/receiver_domain.json'])
    assert data['models']
    for m in data['models']:
        assert m['ray']==int(native[0].original_indices[m['native_sorted_ray']])


class Root(dict):
    def __init__(self,attrs):super().__init__();self.attrs=attrs


def composite_fixture():
    before,native=fixture('cr_only');out=review_result(before,native)
    r=Root({**root_attributes(before.profile),'radar_id':'TEST','scan_id':'test-scan','asset_id':'test-asset',
        'site_longitude_deg':0.,'site_latitude_deg':0.,'qc_parameters_sha256':'a'*64})
    r['sweep_number']=np.array([0],dtype='int32')
    r['sweep_000']={**group(out.sweeps[0]),'azimuth':native[0].azimuth,'range':native[0].ranges,'elevation':native[0].elevation}
    return r


def test_composite_risk_and_all_winners_reconstruct():
    r=composite_fixture();p=build_composite([r],0,maximum_size=128)
    assert np.isfinite(p.arrays['CR_RECEIVER_WITHHELD']).any()
    assert not np.isfinite(p.arrays['CR_TRUSTED'])[np.isfinite(p.arrays['CR_RECEIVER_WITHHELD'])].any()
    for row,col in np.argwhere(np.isfinite(p.arrays['CR_TRUSTED'])):
        assert trace_pixel(p,[r],int(row),int(col))['status']=='RECONSTRUCTED'


def test_composite_rejects_leaked_receiver_gate():
    r=composite_fixture();a=r['sweep_000'];a[CR][a['RDR_CR_WITHHELD_MASK']==1]=1
    with pytest.raises(ValueError,match='leaked'):build_composite([r],0,maximum_size=128)


def test_composite_rejects_mixed_receiver_generation():
    r=composite_fixture();r2=Root(dict(r.attrs));r2.update(r);r2.attrs['qc_receiver_domain_sha256']='b'*64
    with pytest.raises(ValueError,match='mixed'):build_composite([r,r2],0,maximum_size=128)


def test_np_protections_not_reinterpreted():
    _,nn=fixture();n=nn[0];g={'VOR_REASON':np.full(n.shape,8,'uint16'),
        'VOR_STATE':np.full(n.shape,3,'uint8'),'NP_WEATHER_PROTECTED_MASK':np.ones(n.shape,'uint8')}
    hard,local,unknown=protection_masks(n,g,.7)
    assert local.all() and unknown.all() and not hard.any()


@pytest.mark.parametrize('limit',['maximum_volume_gates','maximum_folds'])
def test_resource_abstention_preserves_parent(limit):
    before,native=fixture('quarantine')
    cfg=before.profile.volume_review.receiver_domain.model_copy(update={limit:1})
    before.profile.volume_review=before.profile.volume_review.model_copy(update={'receiver_domain':None})
    parent=review_result(before,native)
    before.profile.volume_review=before.profile.volume_review.model_copy(update={'receiver_domain':cfg})
    out=review_result(before,native)
    expected=group(parent.sweeps[0]);actual=group(out.sweeps[0])
    for k,v in expected.items():np.testing.assert_equal(actual[k],v)
    assert out.summary['receiver_domain']['status']=='RESOURCE_ABSTAINED'
