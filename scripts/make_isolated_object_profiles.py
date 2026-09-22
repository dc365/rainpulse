#!/usr/bin/env python3
"""Create opt-in children of the REAL active parent; never change live selection."""
from pathlib import Path
import argparse,copy,hashlib,json,os,sys,tempfile
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms'))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.config import VolumeReviewConfig
from volume_review.clutter_fusion.isolation_config import IsolationConfig


def generate(parent_path,output,*,schema_only=False):
    parent_path,output=Path(parent_path),Path(output)
    source=parent_path.read_bytes();parent=yaml.safe_load(source)
    if (not isinstance(parent,dict) or parent.get('operational_eligible') is not False
        or parent.get('engine')!='open_source' or not isinstance(parent.get('profile_version'),str)):
        raise ValueError('versioned open-source non-operational parent required')
    vc=VolumeReviewConfig.model_validate(parent.get('volume_review',{}))
    if vc.phase!=3 or vc.clutter_fusion is None or vc.clutter_fusion.isolated_objects is not None:
        raise ValueError('existing P3/CF parent without isolation review required')
    if output.exists():raise FileExistsError(output)
    full_validator=None
    if not schema_only:
        # Missing dependencies are an explicit failure, not a silent schema-only fallback.
        from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
        full_validator=OpenSourceQCProfile.model_validate
        full_validator(parent)
    variants=[('audit','audit')]
    if vc.clutter_fusion.mode!='audit':variants.append(('cr','cr_withhold'))
    if vc.clutter_fusion.mode=='quarantine':variants.append(('quarantine','quarantine'))
    output.parent.mkdir(parents=True,exist_ok=True)
    records=[]
    with tempfile.TemporaryDirectory(prefix='.isolation-config-',dir=output.parent) as td:
        tmp=Path(td)
        for name,mode in variants:
            child=copy.deepcopy(parent)
            policy=IsolationConfig(mode=mode,echo_threshold_dbz=vc.clutter_fusion.no_rain_below_dbz)
            child['profile_version']+='-isolated-v1-'+name
            child['volume_review']['clutter_fusion']['isolated_objects']=policy.model_dump(mode='json')
            VolumeReviewConfig.model_validate(child['volume_review'])
            if full_validator:full_validator(child)
            compare=copy.deepcopy(child);compare['profile_version']=parent['profile_version']
            del compare['volume_review']['clutter_fusion']['isolated_objects']
            if compare!=parent:raise RuntimeError('unrelated parent settings changed')
            value=yaml.safe_dump(child,sort_keys=False,allow_unicode=True).encode()
            filename='isolated-objects-'+name+'.yaml';(tmp/filename).write_bytes(value)
            records.append({'file':filename,'sha256':hashlib.sha256(value).hexdigest()})
        (tmp/'generation.json').write_text(json.dumps({'schema':'isolation-profile-generation-v1',
            'parent_sha256':hashlib.sha256(source).hexdigest(),'profiles':records,
            'validation_scope':'P3 schema only' if schema_only else 'full OpenSourceQCProfile + P3 schema',
            'requires_runtime_flag_asset_validation':True,'parent_actions_changed':False,
            'operational_eligible':False,'deployed':False},indent=2))
        if output.exists():raise FileExistsError(output)
        tmp.chmod(0o755);os.rename(tmp,output)
    return records

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--schema-only',action='store_true',help='explicit offline P3-only validation; not full profile acceptance')
    args=p.parse_args()
    print(json.dumps(generate(args.parent,args.output,schema_only=args.schema_only),indent=2))
