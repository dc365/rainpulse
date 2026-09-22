from io import BytesIO
import copy
import numpy as np
import pytest
from volume_review.receipts import npz_bytes,load_npz,png_bytes,image_binding,verify_binding
from volume_review.sampling import polar_pixels
from volume_review.composite import build_composite,trace_pixel
from helpers import sweep,root_from_sweeps,change


def bound():
    s=sweep();a={'DBZH_RAW':s.fields['DBZH'].copy(),'azimuth':s.azimuth,'range':s.ranges}
    rgba=np.zeros((*s.shape,4),'uint8');rgba[s.observed]=[100,100,100,255]
    row,gate,valid=polar_pixels(s.azimuth,s.ranges,96)
    image=rgba[row,gate].copy();image[~valid]=0
    png=png_bytes(image)
    identity={'volume_id':'anonymous','sweep_id':'sweep_000','qc_content_sha256':'a'*64,
              'config_sha256':'b'*64,'sampling_version':'native-footprint-v2','palette_version':'test-gray-v1'}
    snapshot,receipt=image_binding(a,'DBZH_RAW',s.observed,rgba,png,identity)
    return snapshot,png,receipt,a,rgba,identity


def test_deterministic_npz():
    a={'a':np.array([1.,np.nan]),'b':np.array([2,3])}
    assert npz_bytes(a)==npz_bytes(dict(reversed(list(a.items()))))
    assert np.array_equal(load_npz(npz_bytes(a))['a'],a['a'],equal_nan=True)


def test_bound_image_roundtrip():
    snap,png,receipt,*_=bound()
    assert verify_binding(snap,png,receipt)


@pytest.mark.parametrize('which',['snapshot','png','receipt'])
def test_bound_payload_tamper_rejected(which):
    snap,png,r,*_=bound()
    if which=='snapshot':snap=snap[:-20]+b'x'*20
    elif which=='png':png=png[:-20]+b'x'*20
    else:r={**r,'numeric_sha256':'f'*64}
    with pytest.raises(ValueError):verify_binding(snap,png,r)


def test_mispaired_png_fails():
    snap,png,r,a,rgba,identity=bound()
    other=np.zeros((96,96,4),'uint8');other[10:30,10:30]=255
    with pytest.raises(ValueError):image_binding(a,'DBZH_RAW',np.isfinite(a['DBZH_RAW']),rgba,png_bytes(other),identity)


def test_snapshot_contains_actual_array_values():
    snap,png,r,a,*_=bound()
    assert np.array_equal(load_npz(snap)['DBZH_RAW'],a['DBZH_RAW'],equal_nan=True)


def test_cr_winner_every_pixel_reconstructs():
    a=sweep();b=change(sweep('sweep_001',el=2),'DBZH',lambda x:x.__iadd__(5))
    root=root_from_sweeps([a,b]);p=build_composite([root],32768,maximum_size=96)
    rows,cols=np.nonzero(p.arrays['CR_VALID_MASK'])
    assert len(rows)>0
    for row,col in zip(rows[::max(1,len(rows)//100)],cols[::max(1,len(rows)//100)],strict=True):
        assert trace_pixel(p,[root],int(row),int(col))['status']=='RECONSTRUCTED'
    assert np.isnan(p.arrays['CR_TRUSTED'][p.arrays['CR_VALID_MASK']==0]).all()


def test_close_range_weak_echo_is_recorded_and_cannot_enter_cr():
    s=sweep();root=root_from_sweeps([s]);g=root['sweep_000'];near=s.ranges<=10_000
    g['DBZH_QC'][:,near]=10.;g['DBZH_QC'][:,~near]=30.
    p=build_composite([root],32768,maximum_size=96)
    assert np.isfinite(p.arrays['CR_NEAR_RANGE_WEAK_WITHHELD']).any()
    weak=np.isfinite(p.arrays['CR_NEAR_RANGE_WEAK_WITHHELD'])
    assert np.isnan(p.arrays['CR_TRUSTED'][weak]).all()
    assert np.nanmax(p.arrays['CR_TRUSTED']) >= 30.


def test_quarantined_high_echo_cannot_win():
    a=sweep();b=change(sweep('sweep_001'),'DBZH',lambda x:x.__iadd__(20))
    root=root_from_sweeps([a,b],[a.observed,np.zeros(b.shape,bool)])
    p=build_composite([root],32768,maximum_size=96)
    assert not (p.arrays['WINNER_SOURCE']==1).any()
    assert np.nanmax(p.arrays['CR_RAW'])>np.nanmax(p.arrays['CR_TRUSTED'])


def test_ties_first_source_and_runner_up_recorded():
    a=sweep();root=root_from_sweeps([a,a]);p=build_composite([root],32768,maximum_size=96)
    ok=p.arrays['CR_VALID_MASK']==1
    assert (p.arrays['WINNER_SOURCE'][ok]==0).all()
    assert (p.arrays['RUNNER_UP_SOURCE'][ok]==1).all()
    assert np.array_equal(p.arrays['CR_TRUSTED'][ok],p.arrays['CR_RUNNER_UP'][ok])


def test_unknown_only_not_zero():
    s=sweep();root=root_from_sweeps([s],[np.zeros(s.shape,bool)])
    root['sweep_000']['CR_UNCERTAIN_MASK']=s.observed.astype('uint8')
    p=build_composite([root],32768,maximum_size=96)
    assert not p.arrays['CR_VALID_MASK'].any() and p.arrays['CR_UNCERTAIN_COVERAGE_MASK'].any()
    assert np.isnan(p.arrays['CR_TRUSTED']).all()
    assert trace_pixel(p,[root],0,0)['status']=='NO_TRUSTED_OBSERVATION'


def test_mixed_generations_rejected():
    a=root_from_sweeps([sweep()]);b=copy.deepcopy(a);b.attrs['qc_volume_review_sha256']='c'*64
    with pytest.raises(ValueError):build_composite([a,b],32768,maximum_size=64)


def test_trace_rejects_changed_source():
    root=root_from_sweeps([sweep()]);p=build_composite([root],32768,maximum_size=96)
    row,col=np.argwhere(p.arrays['CR_VALID_MASK'])[0]
    root['sweep_000']['DBZH_QC'][8,10]+=1
    with pytest.raises(ValueError):trace_pixel(p,[root],int(row),int(col))


def test_p3_audit_emits_receipt_but_keeps_parent_pixels():
    from volume_review.composite import diagnostic_composite
    root=root_from_sweeps([sweep()]);store={};old=np.full((2,2),123.,'float32')
    values,bounds,meta=diagnostic_composite([root],0,objects=store,legacy_compositor=lambda *a,**kw:(old,[0,0,1,1]))
    assert np.array_equal(values,old) and meta['cr_applied'] is False
    assert 'volume_review/composite.npz' in store and 'volume_review/composite.json' in store


def test_p3_experiment_uses_new_qualification():
    from volume_review.composite import diagnostic_composite
    root=root_from_sweeps([sweep()]);root.attrs['qc_volume_review_mode']='experiment_quarantine'
    store={};values,_,meta=diagnostic_composite([root],0,objects=store,legacy_compositor=lambda *a,**kw:(np.zeros((2,2)),[0,0,1,1]))
    assert meta['cr_applied'] is True and np.isfinite(values).any()


def test_resource_failure_restores_exact_parent_inputs():
    from volume_review.composite import diagnostic_composite
    root=root_from_sweeps([sweep()]);root.attrs.update(qc_volume_review_mode='experiment_quarantine',qc_volume_review_status='RESOURCE_ABSTAINED')
    g=root['sweep_000'];g['VOR_BEFORE_QPE_ELIGIBLE_MASK']=g['VALID_MASK'].copy();g['QPE_ELIGIBLE_MASK']=np.zeros_like(g['VALID_MASK'])
    calls=[]
    def legacy(roots,*a,**kw):
        count=int(roots[0]['sweep_000']['QPE_ELIGIBLE_MASK'].sum());calls.append(count)
        return np.full((2,2),count),[0,0,1,1]
    values,_,meta=diagnostic_composite([root],0,objects={},legacy_compositor=legacy)
    assert calls[0]==0 and calls[1]>0 and np.all(values==calls[1])
    assert not meta['cr_applied'] and meta['cr_fallback_reason']=='RESOURCE_ABSTAINED_PARENT_RESTORED'
