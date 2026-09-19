"""Pure adapter contract tests. These are not a real Zarr/Worker integration run."""
from dataclasses import dataclass,replace
from types import SimpleNamespace as NS
import numpy as np
import pytest
from helpers import sweep,baseline,root_from_sweeps
from volume_review.config import VolumeReviewConfig as C
from volume_review.integration import review_result,root_attributes
from volume_review.validation import validate_serialized,LegacyView
from volume_review.disposition import DERIVED_FIELDS
from volume_review.receipts import write_snapshots,npz_bytes
from volume_review.bundle_validation import verify_qc_evidence


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
    def __init__(self,s,permute=False):
        self.name=s.name;self.shape=s.shape;self.fields=s.fields;self.field_available=s.available
        self.azimuth=s.azimuth;self.elevation=s.elevation;self.ranges=s.ranges;self.ray_time=s.ray_time_s
        self.geometry_good=s.good;self.gap_after=s.gap_after
        self.original_indices=np.roll(np.arange(s.shape[0]),7) if permute else np.arange(s.shape[0])
    def restore(self,x):
        y=np.empty_like(x);y[self.original_indices]=x;return y


def fixture(mode='experiment_quarantine',permute=False):
    source=[sweep(),sweep('sweep_001',el=1.5)];native=[Native(s,permute) for s in source];qs=[]
    for s,n in zip(source,native):
        a,f,q=baseline(s)
        for key in DERIVED_FIELDS:
            a[key]=s.observed.astype('uint8') if key.endswith('_MASK') else np.where(s.observed,2.,np.nan).astype('float32')
        a={k:n.restore(v) for k,v in a.items()}
        qs.append(Q(s.name,a,n.restore(s.fields['DBZH']),n.restore(s.fields['DBZH']),n.restore(f),n.restore(q),n.restore(s.observed.astype('uint8')),np.zeros(s.shape,'uint8'),{'QI_METEO':n.restore(q),'QI_INTERFERENCE':n.restore(q)}))
    profile=NS(volume_review=C(mode=mode),context=NS(strong_support=.7),flag_masks={'LOW_QUALITY':np.uint32(1024)},parameters_hash='a'*64,pipeline_version='qc-opensource-7.3.6')
    result=Result(profile,tuple(qs),{'sweeps':{s.name:{} for s in source}})
    return result,native


def group(q):
    return {**q.optional_qc_fields,'VALID_MASK':q.valid_mask,'QC_FLAGS':q.qc_flags,'QUALITY_INDEX':q.quality_index,
            'LOW_QUALITY_MASK':q.low_quality_mask,'DBZH_RAW':q.dbzh_raw,'DBZH_QC':q.dbzh_qc,**q.qi_components}


def assert_old(view,original):
    assert set(view)==set(original)
    for k in view:assert np.array_equal(view[k],original[k],equal_nan=True),k


@pytest.mark.parametrize('mode',['audit','experiment_quarantine'])
@pytest.mark.parametrize('permuted',[False,True])
def test_adapter_and_legacy_projection(mode,permuted):
    before,native=fixture(mode,permuted);after=review_result(before,native)
    assert after.summary['volume_review']['new_confirmed_gates']==0
    for old,new in zip(before.sweeps,after.sweeps):
        calls=[]
        def legacy(view,attrs):
            assert 'qc_volume_review_version' not in attrs
            assert_old(view,group(old));calls.append(1)
        validate_serialized(group(new),root_attributes(before.profile),legacy)
        assert calls==[1]
        assert np.array_equal(old.dbzh_raw,new.dbzh_raw,equal_nan=True)
        q=new.optional_qc_fields['VOR_QUARANTINE_MASK']==1
        assert q.any()==(mode=='experiment_quarantine')
        if q.any():
            assert not new.optional_qc_fields['KDP_OS_AVAILABLE_MASK'][q].any()
            assert np.isnan(new.optional_qc_fields['KDP_OS'][q]).all()
    assert after.volume_review_artifacts and 'reference_models' not in after.summary['volume_review'] or after.summary['volume_review']['reference_models']>=0


def test_no_extension_returns_identical_result():
    result,n=fixture();result.profile.volume_review=None
    assert review_result(result,n) is result
    assert root_attributes(result.profile)=={}


@pytest.mark.parametrize('field',['QC_ACTION','QC_FLAGS','QUALITY_INDEX','REFLECTIVITY_TRUST_MASK','QPE_ELIGIBLE_MASK','KDP_OS_AVAILABLE_MASK','KDP_OS'])
def test_serialized_validator_refuses_unexplained_edits(field):
    old,n=fixture();new=review_result(old,n);g=group(new.sweeps[0]);g={k:v.copy() for k,v in g.items()}
    g[field][8,60]=88
    with pytest.raises(ValueError):validate_serialized(g,root_attributes(old.profile),lambda view,attrs:assert_old(view,group(old.sweeps[0])))


def test_missing_baseline_is_not_invented():
    old,n=fixture();new=review_result(old,n);g=group(new.sweeps[0]);del g['VOR_BEFORE_QC_FLAGS']
    with pytest.raises(ValueError,match='pre-volume'):validate_serialized(g,root_attributes(old.profile),lambda *_:None)


def test_whole_phase_segment_is_invalidated_without_weather_environment():
    old,n=fixture();new=review_result(old,n);q=new.sweeps[0].optional_qc_fields
    derived=q['VOR_DERIVED_INVALIDATION_MASK']==1;added=q['VOR_QUARANTINE_MASK']==1
    assert np.all(derived[added]) and derived.sum()>=added.sum()
    assert np.isnan(q['DBZH_OS_ATTENUATION_CORRECTED'][derived]).all()
    assert not q['ATTENUATION_OS_AVAILABLE_MASK'][derived].any()


def test_snapshot_writer_and_bound_qc_verifier():
    old,n=fixture();new=review_result(old,n);root=root_from_sweeps([sweep(),sweep('sweep_001',el=1.5)])
    for i,q in enumerate(new.sweeps):root[f'sweep_{i:03d}'].update(group(q))
    store={};write_snapshots(root,store,new)
    manifest,snapshots=verify_qc_evidence(store)
    assert len(snapshots)==2 and manifest['flag_definitions']=={'LOW_QUALITY':1024}
    path=manifest['sweeps'][0]['path'];store[path]=npz_bytes({'DBZH_RAW':np.zeros((2,2))})
    with pytest.raises(ValueError):verify_qc_evidence(store)


def test_donor_index_values_are_restored_not_only_target_rows():
    import json
    old,n=fixture(permute=True);new=review_result(old,n)
    for q in new.sweeps:
        a=q.optional_qc_fields
        for donor in (0,1):
            hit=a['VOR_DONOR_SWEEP']==donor
            ray=a['VOR_DONOR_RAY'][hit];gate=a['VOR_DONOR_GATE'][hit]
            if len(ray):assert np.isfinite(old.sweeps[donor].dbzh_raw[ray,gate]).all()
    detail=json.loads(new.volume_review_artifacts['qc/volume_review/evidence.json'])
    for i,records in enumerate(detail['reference_models']):
        for rec in records:assert rec['ray']==int(n[i].original_indices[rec['native_sorted_ray']])
