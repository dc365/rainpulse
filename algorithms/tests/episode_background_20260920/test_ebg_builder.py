import json
from dataclasses import replace
from datetime import timedelta
import numpy as np
import pytest
from episode_helpers import *
from volume_review.episode_background.io import sample_bytes,read_sample,background_bytes,load_background,atomic_file
from volume_review.episode_background.data import sha
from volume_review.episode_background.cli import cross_validate


def test_builder_raw_counts_temporal_support_and_immutability():
    seq=episode(); before=[s.raw_digest for s in seq]; bg=model(seq);p='s000__'
    assert before==[s.raw_digest for s in seq]
    assert bg.metadata['grade']=='short_episode'
    assert (bg.arrays[p+'DBZH_n']==20).all()
    assert (bg.arrays[p+'stable_measured_core']==1).all()
    assert np.isnan(bg.arrays[p+'echo_occurrence_fraction']).all()
    assert (bg.arrays[p+'occurrence_available']==0).all()
    assert bg.arrays[p+'DBZH_block_counts'].shape==(4,8,40)
    assert not bg.arrays[p+'DBZH_n'].flags.writeable


def test_decidable_denominator_includes_noecho_not_missing():
    seq=[]
    for i,s in enumerate(episode()):
        f={k:v.copy() for k,v in s.fields.items()}; a={k:v.copy() for k,v in s.available.items()}
        acq=np.ones(s.shape,bool); ne=np.zeros(s.shape,bool)
        if i>=10:
            f['DBZH'][:,0]=np.nan;a['DBZH'][:,0]=False;ne[:,0]=True
        # Gate 1 is actually unacquired in ten scans: not a no-rain vote.
        if i>=10:
            f['DBZH'][:,1]=np.nan;a['DBZH'][:,1]=False;acq[:,1]=False
        seq.append(replace(s,fields=f,available=a,acquired=acq,no_echo=ne))
    bg=model(seq);p='s000__'
    assert (bg.arrays[p+'n_decidable_acquisition'][:,0]==20).all()
    assert np.allclose(bg.arrays[p+'echo_occurrence_fraction'][:,0],.5)
    assert (bg.arrays[p+'n_decidable_acquisition'][:,1]==10).all()
    assert np.isnan(bg.arrays[p+'echo_occurrence_fraction'][:,1]).all()
    assert not bg.arrays[p+'stable_measured_core'][:,:2].any()


@pytest.mark.parametrize('missing',['RHOHV','ZDR','PHIDP','SNR','VR','SW'])
def test_partial_background_is_representable(missing):
    bg=model(episode(missing=(missing,)))
    assert np.isnan(bg.arrays['s000__'+missing+'_median']).all()
    assert (bg.arrays['s000__'+missing+'_n']==0).all()


def test_tail_is_category_never_precise_zdr():
    bg=model(episode(zdr=8.))
    assert (bg.arrays['s000__zdr_tail_positive_fraction']==1).all()
    assert np.isnan(bg.arrays['s000__ZDR_median']).all()
    assert (bg.arrays['s000__ZDR_n']==0).all()


def test_phase_circular_center_wrap():
    seq=episode()
    for i,s in enumerate(seq):s.fields['PHIDP'][:]=359. if i%2 else 1.
    bg=model(seq)
    assert (abs(bg.arrays['s000__PHIDP_median'])<1e-3).all()
    assert np.allclose(bg.arrays['s000__PHIDP_mad'],1.,atol=1e-3)


def test_dynamic_background_is_not_stable_core():
    seq=episode()
    for i,s in enumerate(seq):s.fields['DBZH'][:]=float(i//5*6)
    bg=model(seq)
    assert not bg.arrays['s000__stable_measured_core'].any()


@pytest.mark.parametrize('fault',['review','duplicate','processor','station','content','time','short','few','budget'])
def test_invalid_reference_rejected(fault):
    seq=episode();kw={};review=True
    if fault=='review':review=False
    if fault=='duplicate':seq.append(seq[0])
    if fault=='processor':seq[1]=replace(seq[1],processing_id='different')
    if fault=='station':seq[1]=replace(seq[1],radar_id='site_b')
    if fault=='content':seq[1]=replace(seq[1],source_sha256=seq[0].source_sha256)
    if fault=='time':seq[1]=replace(seq[1],observed_at=seq[0].observed_at)
    if fault=='short':seq=[replace(s,observed_at=(T0+timedelta(seconds=i)).isoformat()) for i,s in enumerate(seq)]
    if fault=='few':seq=seq[:8]
    if fault=='budget':kw={'maximum_stack_elements':1000}
    with pytest.raises(ValueError):build_episode(seq,BuildConfig(**kw),review_receipt=RECEIPT,reviewed_no_precipitation=review)


def test_atomic_deterministic_asset_and_raw_roundtrip(tmp_path):
    s=sample();raw=sample_bytes(s);p=tmp_path/'raw.npz';atomic_file(p,raw)
    back=read_sample(p,sha(raw));assert np.array_equal(back.fields['DBZH'],s.fields['DBZH'])
    with pytest.raises(FileExistsError):atomic_file(p,raw)
    bg=model();payload=background_bytes(bg);assert payload==background_bytes(model())
    dest=tmp_path/'model.zip';atomic_file(dest,payload)
    loaded=load_background(dest,sha(payload))
    assert loaded.metadata['arrays_sha256']
    for k in bg.arrays:assert np.array_equal(loaded.arrays[k],bg.arrays[k],equal_nan=True)
    with pytest.raises(ValueError):load_background(dest,'0'*64)
    with pytest.raises(ValueError):load_background(dest,sha(payload),maximum_bytes=100)


def test_contiguous_cv_whole_volume_no_overlap():
    seq=episode()+[replace(s,sweep_id='sweep_002',source_sha256=sha(('other'+s.scan_id).encode())) for s in episode()]
    manifest={'reviewed_no_precipitation':True,'review_receipt':RECEIPT}
    report=cross_validate(manifest,seq,BuildConfig())
    assert len(report['folds'])==4
    assert not report['independent_weather_validation']
    for fold in report['folds']:
        assert fold['status']=='EVALUATED'
        assert not set(fold['train_scans'])&set(fold['heldout_scans'])
        assert fold['background_match_fraction']>.99


def test_low_snr_reference_polarization_not_promoted_by_statistics():
    seq=episode(zdr=8.)
    for s in seq:s.fields['SNR'][:]=3.
    bg=model(seq)
    assert (bg.arrays['s000__RHOHV_n']==0).all()
    assert (bg.arrays['s000__PHIDP_n']==0).all()
    # Numeric tail category is retained, but not a precise reliable ZDR feature.
    assert (bg.arrays['s000__zdr_tail_positive_fraction']==1).all()


def test_total_background_arrays_resource_bound():
    with pytest.raises(ValueError,match='output arrays'):
        build_episode(episode(),BuildConfig(maximum_asset_array_bytes=1024),
                      review_receipt='e'*64,reviewed_no_precipitation=True)


@pytest.mark.parametrize('q',[0.,.1,.5,.9,1.])
def test_vectorized_gate_quantiles_match_numpy(q):
    import warnings
    from volume_review.episode_background.builder import _quantile
    rng=np.random.default_rng(372);v=rng.normal(size=(20,14,27)).astype('float32')
    v[rng.random(v.shape)<.35]=np.nan;v[:,0,0]=np.nan
    with warnings.catch_warnings():
        warnings.simplefilter('ignore',RuntimeWarning)
        expected=np.nanquantile(v,q,axis=0)
    assert np.allclose(_quantile(v,q),expected,atol=1e-7,equal_nan=True)
