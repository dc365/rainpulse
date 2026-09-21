import importlib.util
import json
from pathlib import Path
import copy
import numpy as np
import pytest
import yaml
from fusion_helpers import cfg, scene, baseline
from volume_review.config import VolumeReviewConfig
from volume_review.clutter_fusion.context import ContextMetadata
from volume_review.clutter_fusion.engine import evaluate_volume
from volume_review.clutter_fusion.disposition import apply

ROOT=Path(__file__).resolve().parents[3]
spec=importlib.util.spec_from_file_location('make_cf_profiles',ROOT/'scripts/make_clutter_fusion_profiles.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def parent():
    return {'engine':'open_source','pipeline_version':'qc-opensource-7.3.9','profile_version':'frozen-parent',
        'operational_eligible':False,'echo':{'no_rain_below_dbz':-10.},
        'generalization':{'broad_source':{'source_review':{'radial_revision':{'step':3}}}},
        'volume_review':VolumeReviewConfig(mode='experiment_quarantine').model_dump(mode='json')}


def test_generator_preserves_radial_and_parent_and_directory_permissions(tmp_path):
    p=tmp_path/'parent.yaml';value=parent();payload=yaml.safe_dump(value).encode();p.write_bytes(payload)
    result=module.generate(p,tmp_path/'children',backend='numpy_reference',pyart_check=True)
    assert len(result)==4 and p.read_bytes()==payload
    assert (tmp_path/'children').stat().st_mode&0o777==0o755
    for record in result:
        c=yaml.safe_load((tmp_path/'children'/record['file']).read_text())
        assert c['generalization']==value['generalization']
        assert c['volume_review']['clutter_fusion']['gatefilter_check']=='pyart'
        cc=copy.deepcopy(c);cc.pop('profile_version');cc['volume_review'].pop('clutter_fusion')
        vv=copy.deepcopy(value);vv.pop('profile_version');assert cc==vv
        VolumeReviewConfig.model_validate(c['volume_review'])
    with pytest.raises(FileExistsError):module.generate(p,tmp_path/'children',backend='numpy_reference')


@pytest.mark.parametrize('fault',['missing_volume','wrong_engine','operational','old_phase','audit_parent','has_fusion'])
def test_bad_parent_rejected_without_output(tmp_path,fault):
    v=parent()
    if fault=='missing_volume':del v['volume_review']
    if fault=='wrong_engine':v['engine']='other'
    if fault=='operational':v['operational_eligible']=True
    if fault=='old_phase':v['volume_review']['phase']=1;v['volume_review']['mode']='audit'
    if fault=='audit_parent':v['volume_review']['mode']='audit'
    if fault=='has_fusion':v['volume_review']['clutter_fusion']=cfg().model_dump(mode='json')
    p=tmp_path/'parent.yaml';p.write_text(yaml.safe_dump(v))
    with pytest.raises(ValueError):module.generate(p,tmp_path/'children')
    assert not (tmp_path/'children').exists()


def test_absent_extension_keeps_old_hash_payload():
    from hashlib import sha256
    v=VolumeReviewConfig();a=v.model_dump(mode='json');assert 'clutter_fusion' not in a
    expected=sha256(json.dumps(a,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert v.digest==expected


@pytest.mark.parametrize('kw',[{'doppler_pair_verified':'yes'}, {'beam_width_deg':'1'},
    {'nyquist_ms':float('nan')},{'nyquist_ms':0}, {'verification_id':''},{'waveform':123}, {'beam_width_deg':4}])
def test_malformed_context_metadata_rejected(kw):
    with pytest.raises(ValueError):ContextMetadata(**kw)


@pytest.mark.parametrize('flag',[0,-1,3,2**32])
def test_flag_is_single_unsigned_bit(flag):
    s=scene();c=cfg(mode='cr_withhold');e=evaluate_volume([s],c)[0]
    with pytest.raises(ValueError):apply(baseline(s),e.arrays,c,low_quality_flag=flag)


def test_synthetic_demo_full_build_holdout_fusion(tmp_path):
    spec=importlib.util.spec_from_file_location('demo_cf_bg',ROOT/'scripts/demo_clutter_fusion_background.py')
    demo=importlib.util.module_from_spec(spec);spec.loader.exec_module(demo)
    demo.main(tmp_path/'demo')
    report=json.loads((tmp_path/'demo/report.json').read_text())
    assert report['synthetic'] and not report['deployable_asset']
    assert report['training_scans']==20 and report['time_blocks']==4
    for sweep in report['sweeps']:
        assert sweep['with_background_candidates']>sweep['without_background_candidates']
        assert sweep['disposition']['qpe_loss_gates']==0
    with pytest.raises(FileExistsError):demo.main(tmp_path/'demo')
