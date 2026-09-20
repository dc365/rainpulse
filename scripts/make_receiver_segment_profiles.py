"""Append only the finite-reference option to a real receiver-domain parent.

Does not change the parent/global RDR modes, partial policy, NMR, clutter, weather
protections, deployments or any historical file. Complete provenance is written
last. No station, time, ROI, angle or image threshold is introduced.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.config import VolumeReviewConfig
from volume_review.receiver_domain.config import ReceiverDomainConfig, SegmentReferenceConfig


def generate(parent_path, output):
    parent_path,output=Path(parent_path),Path(output)
    payload=parent_path.read_bytes();parent=yaml.safe_load(payload)
    if not isinstance(parent,dict) or parent.get('operational_eligible') is not False:
        raise ValueError('explicit non-operational parent required')
    if not isinstance(parent.get('profile_version'),str) or not parent['profile_version']:
        raise ValueError('parent profile identity required')
    volume=VolumeReviewConfig.model_validate(parent.get('volume_review',{}))
    old=volume.receiver_domain
    if volume.phase!=3 or old is None or old.segment_reference is not None:
        raise ValueError('use the real P3 receiver-domain parent without segment_reference')
    # Do not turn an audit parent into an active configuration implicitly.
    if old.mode=='audit' or volume.mode!='experiment_quarantine':
        raise ValueError('use an explicitly selected cr_only/quarantine parent; modes are preserved')
    variants=[('audit','audit','diagnostic_only'),('experiment','experiment','diagnostic_only'),
              ('experiment-partial-cr','experiment','cr_withhold')]
    outputs={};records=[]
    for label,mode,partial in variants:
        child=copy.deepcopy(parent)
        raw=copy.deepcopy(parent['volume_review']['receiver_domain'])
        raw['segment_reference']=SegmentReferenceConfig(mode=mode,partial_policy=partial).model_dump(mode='json')
        cfg=ReceiverDomainConfig.model_validate(raw)
        child['volume_review']['receiver_domain']=raw
        VolumeReviewConfig.model_validate(child['volume_review'])
        child['profile_version']+='-receiver-segment-v1-'+label
        name='receiver-segment-'+label+'.yaml'
        data=yaml.safe_dump(child,sort_keys=False,allow_unicode=True).encode('utf-8')
        outputs[name]=data
        records.append({'file':name,'sha256':hashlib.sha256(data).hexdigest(),'receiver_config_sha256':cfg.digest})
    output.parent.mkdir(parents=True,exist_ok=True)
    output.mkdir(mode=0o755)  # no overwriting or mixing profile generations
    try:
        for name,data in outputs.items():
            with (output/name).open('xb') as f:f.write(data)
        receipt={'schema':'rainpulse.receiver-segment-profiles-v1',
                 'parent_sha256':hashlib.sha256(payload).hexdigest(),
                 'profiles':records,'parent_mode_preserved':True,'live_configuration_changed':False}
        with (output/'generation.json').open('x',encoding='utf-8') as f:json.dump(receipt,f,indent=2)
    except Exception:
        # Only remove our own files; preserve any concurrent/unrelated file.
        for name in outputs:
            p=output/name
            if p.is_file() and p.read_bytes()==outputs[name]:p.unlink()
        if not any(output.iterdir()):output.rmdir()
        raise
    return records


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(generate(a.parent,a.output),indent=2))
