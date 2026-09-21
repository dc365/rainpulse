"""Opt-in near-clutter children of the ACTUAL active profile. No deployment side effects."""
from pathlib import Path
import argparse,copy,hashlib,json,os,sys,tempfile
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.config import VolumeReviewConfig
from volume_review.clutter_fusion.config import ClutterFusionConfig
from volume_review.clutter_fusion.near_revision_config import NearRevisionConfig,StrongNearConfig
from review_extension.config import NonPrecipConfig
from review_extension.near_reliability import NearReliabilityConfig


def generate(parent_path,output,*,backend=None,validate_full=True):
    parent_path=Path(parent_path);output=Path(output)
    raw=parent_path.read_bytes();parent=yaml.safe_load(raw)
    if not isinstance(parent,dict) or parent.get('operational_eligible') is not False:
        raise ValueError('explicit non-operational parent required')
    if not parent.get('profile_version'):raise ValueError('parent profile identity missing')
    vp=VolumeReviewConfig.model_validate(parent.get('volume_review',{}))
    if vp.phase!=3 or vp.clutter_fusion is None:raise ValueError('use actual P3+CF parent; preserve its background assets')
    if vp.clutter_fusion.mode=='audit':raise ValueError('select an actual active CF parent; do not silently enable older CF actions')
    if vp.clutter_fusion.near_revision is not None:raise ValueError('parent already includes near revision')
    if not isinstance(parent.get('nonprecip_review'),dict):raise ValueError('nonprecip parent contract required')
    full_validator=None
    if validate_full:
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms'))
        from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
        full_validator=OpenSourceQCProfile.model_validate
        full_validator(parent)
    echo=float(parent.get('echo',{}).get('no_rain_below_dbz',-10.))
    # Literal unchanged parent is the A0 baseline, not the default model config.
    variants=[('audit',False,'audit','audit',False,None),
              ('near-on',True,None,None,False,None),
              ('near-on-tuned',True,None,None,True,None),
              ('joint-cr',True,'cr_withhold','audit',False,None),
              ('joint-cr-dem',True,'cr_withhold','cr_withhold',False,None),
              ('strong-audit',True,'cr_withhold','audit',False,'audit'),
              ('strong-quarantine',True,'cr_withhold','audit',False,'quarantine')]
    if output.exists():raise ValueError('output exists; refusing mixed generations')
    output.parent.mkdir(parents=True,exist_ok=True);records=[]
    with tempfile.TemporaryDirectory(prefix='.near-joint-',dir=output.parent) as td:
        temp=Path(td)
        for name,on,mode,dem,tuned,strong_mode in variants:
            child=copy.deepcopy(parent)
            child['profile_version']+='-near-joint-v1-'+name
            # pipeline_version is a frozen Literal coordinated with old stages.
            # New identity lives in profile_version and hashed extension configs.
            if on:
                n=child['nonprecip_review'];n['near_enabled']=True;n['mode']='experiment_quarantine'
                n['quarantine_classes']=list(dict.fromkeys([*n.get('quarantine_classes',['fixed_ground']),'near_nonmet']))
                n['near_reliability']=NearReliabilityConfig(no_rain_below_dbz=echo).model_dump(mode='json')
                if tuned:n.update(near_maximum_rhohv=.85,near_minimum_coverage=.7,near_minimum_fraction=.6)
                child['volume_review']['mode']='experiment_quarantine'
            NonPrecipConfig.model_validate(child['nonprecip_review'])
            if mode:
                c=child['volume_review']['clutter_fusion']
                c['near_revision']=NearRevisionConfig(
                    mode=mode,dem_policy=dem,
                    **({} if strong_mode is None else
                       {'strong_near':StrongNearConfig(mode=strong_mode)}),
                ).model_dump(mode='json')
            if backend is not None:
                # Explicit diagnostic override creates a distinct config identity.
                child['volume_review']['clutter_fusion']['depolarization_backend']=backend
            VolumeReviewConfig.model_validate(child['volume_review'])
            if full_validator is not None:full_validator(child)
            data=yaml.safe_dump(child,sort_keys=False,allow_unicode=True).encode()
            filename='near-joint-'+name+'.yaml';(temp/filename).write_bytes(data)
            records.append({'file':filename,'sha256':hashlib.sha256(data).hexdigest(),
                 'near_enabled_effective':NonPrecipConfig.model_validate(child['nonprecip_review']).near_enabled,
                 'proposed_final_candidate':name=='joint-cr-dem',
                 'strong_near_mode':strong_mode,
                 'requires_independent_acceptance':True})
        (temp/'generation.json').write_text(json.dumps({'parent_sha256':hashlib.sha256(raw).hexdigest(),
             'validation_level':'full_OpenSourceQCProfile' if validate_full else 'explicit_subconfig_only',
             'variants':records,'live_configuration_modified':False,'operational_eligible':False},indent=2))
        temp.chmod(0o755)
        if output.exists():raise ValueError('output appeared concurrently')
        os.rename(temp,output)
    return records


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--parent',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--backend',choices=['wradlib','numpy_reference'])
    p.add_argument('--subconfig-only',action='store_true',help='offline reference tests only; NOT full repository acceptance')
    a=p.parse_args();print(json.dumps(generate(a.parent,a.output,backend=a.backend,validate_full=not a.subconfig_only),indent=2))
