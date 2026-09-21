"""Create separately named shared-source children from a frozen active RDR parent."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.config import VolumeReviewConfig
from volume_review.receiver_domain.config import ReceiverDomainConfig, SourceFamilyConfig


def generate(parent_path, output):
    parent_path,output=Path(parent_path),Path(output)
    raw=parent_path.read_bytes();parent=yaml.safe_load(raw)
    if not isinstance(parent,dict) or parent.get('operational_eligible') is not False:
        raise ValueError('explicit non-operational parent required')
    if not isinstance(parent.get('profile_version'),str) or not parent['profile_version']:
        raise ValueError('parent profile identity missing')
    vc=VolumeReviewConfig.model_validate(parent.get('volume_review',{}))
    rc=vc.receiver_domain
    if vc.phase!=3 or vc.mode!='experiment_quarantine' or rc is None or rc.mode=='audit':
        raise ValueError('an active P3/RDR parent is required; preserve its actions')
    if rc.source_family is not None:
        raise ValueError('use the frozen parent without a source-family child')
    variants=[('audit','audit','cr_only','diagnostic_only'),
              ('full-cr','experiment','cr_only','diagnostic_only'),
              ('full-quarantine','experiment','quarantine','diagnostic_only'),
              ('partial-cr','experiment','cr_only','cr_withhold')]
    if output.exists():raise ValueError('output exists; refusing to mix generations')
    output.parent.mkdir(parents=True,exist_ok=True);records=[]
    with tempfile.TemporaryDirectory(prefix='.source-family-',dir=output.parent) as td:
        tmp=Path(td)
        for name,mode,policy,partial in variants:
            child=json.loads(json.dumps(parent))
            fc=SourceFamilyConfig(mode=mode,full_policy=policy,partial_policy=partial)
            child['volume_review']['receiver_domain']['source_family']=fc.model_dump(mode='json')
            child['profile_version']+='-source-family-v1-'+name
            cv=VolumeReviewConfig.model_validate(child['volume_review'])
            payload=yaml.safe_dump(child,allow_unicode=True,sort_keys=False).encode()
            filename='receiver-family-'+name+'.yaml';(tmp/filename).write_bytes(payload)
            records.append({'file':filename,'sha256':hashlib.sha256(payload).hexdigest(),
                            'receiver_sha256':cv.receiver_domain.digest,'volume_sha256':cv.digest})
        (tmp/'generation.json').write_text(json.dumps({'parent_sha256':hashlib.sha256(raw).hexdigest(),
            'source_family_version':SourceFamilyConfig().version,'profiles':records,
            'parent_actions_changed':False,'live_configuration_modified':False},indent=2))
        tmp.chmod(0o755)
        if output.exists():raise ValueError('output appeared concurrently')
        os.rename(tmp,output)
    return records


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(generate(a.parent,a.output),indent=2))
