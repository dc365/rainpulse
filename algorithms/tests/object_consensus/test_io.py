import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.object_consensus import (Config, Policy, RawScan, WeatherSupport, infer)
from rainpulse_algo.radar.qc_engine.object_consensus.cli import run_manifest
from rainpulse_algo.radar.qc_engine.object_consensus.io import sha_file, read_case, write_npz, write_json
from test_oc1 import scene, raw, baseline


def fixture(tmp_path):
    input_dir=tmp_path/'input';input_dir.mkdir()
    d=scene();r=raw(d)
    d.update({'baseline_'+k:v for k,v in baseline(r).items()})
    write_npz(input_dir/'S01.npz',d)
    entry={'case':'S01','shape':list(r.shape),'full_ppi':False,'phase_period_deg':360.,'gate_spacing_m':250.,
           'sha256':sha_file(input_dir/'S01.npz'),
           'arrays':{k:{'shape':list(v.shape),'dtype':str(v.dtype)} for k,v in d.items()}}
    write_json(input_dir/'manifest.json',[entry])
    return input_dir,entry


def test_manifest_run_atomic_and_repeat_numerical_identity(tmp_path):
    inp,item=fixture(tmp_path)
    p=Policy(mode='experiment_quarantine',acknowledge_uncalibrated_model=True,maximum_new_eligible_loss_fraction=1)
    s=run_manifest(inp,tmp_path/'a',Config(),p)
    run_manifest(inp,tmp_path/'b',Config(),p)
    assert s[0]['roi_added_quarantine']>0
    for name in ('evidence.npz','outcome.npz','folds.json.gz','identity.json','roi_gates.csv'):
        assert sha_file(tmp_path/'a'/'S01'/name)==sha_file(tmp_path/'b'/'S01'/name)
    assert sha_file(inp/'S01.npz')==item['sha256']
    with pytest.raises(ValueError,match='exists'):run_manifest(inp,tmp_path/'a',Config(),p)


def test_bad_later_case_does_not_publish_partial(tmp_path):
    inp,item=fixture(tmp_path);second={**item,'case':'S02','sha256':'b'*64}
    (inp/'S02.npz').write_bytes((inp/'S01.npz').read_bytes())
    write_json(inp/'manifest.json',[item,second])
    with pytest.raises(ValueError,match='checksum'):run_manifest(inp,tmp_path/'out',Config(),Policy())
    assert not (tmp_path/'out').exists()
    assert not list(tmp_path.glob('.oc1-*'))


def test_input_dir_never_output(tmp_path):
    inp,_=fixture(tmp_path)
    with pytest.raises(ValueError):run_manifest(inp,inp/'out',Config(),Policy())


def test_extra_field_not_declared_rejected(tmp_path):
    inp,item=fixture(tmp_path);item['arrays'].pop('raw_ZDR')
    with pytest.raises(ValueError,match='inventory'):read_case(inp,item)


def test_unsafe_case_path_rejected(tmp_path):
    inp,item=fixture(tmp_path);item['case']='../evil'
    with pytest.raises(ValueError,match='unsafe'):read_case(inp,item)


def test_no_truth_metrics_without_labels(tmp_path):
    inp,_=fixture(tmp_path);s=run_manifest(inp,tmp_path/'audit',Config(),Policy())[0]
    assert s['precision'] is None and s['recall'] is None and s['true_weather_loss'] is None
    assert s['roi_added_quarantine']==0


def test_missing_snr_is_nonfatal_inference_but_not_zero_evidence(tmp_path):
    inp,item=fixture(tmp_path);d=read_case(inp,item)
    d['available_SNR'][:]=False
    r=RawScan.from_arrays(d,full_ppi=False);e=infer(r)
    assert not e.arrays['family_code'].any()
    assert np.isnan(e.arrays['source_compatibility_score']).all()
    assert np.any(e.arrays['reason'] & 4)


def test_external_support_uint8_mask():
    r=raw(scene());w=WeatherSupport(np.ones(r.shape),np.ones(r.shape,'uint8'),'a'*64,True)
    e=infer(r,weather=w);assert not np.any(e.arrays['state']==5)
