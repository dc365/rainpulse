"""Create opt-in receiver-domain child profiles; never change parent or services."""
from pathlib import Path
import argparse
import copy
import sys
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.config import VolumeReviewConfig
from volume_review.receiver_domain.config import ReceiverDomainConfig


def generate(parent, output):
    if parent.get('engine')!='open_source' or parent.get('operational_eligible') is not False:
        raise ValueError('explicit non-operational open-source parent required')
    v=VolumeReviewConfig.model_validate(parent.get('volume_review',{}))
    if v.phase!=3 or v.mode!='experiment_quarantine' or v.receiver_domain is not None:
        raise ValueError('use a frozen experimental P3 parent without receiver-domain')
    output=Path(output)
    if output.exists():raise ValueError('output exists; do not overwrite profiles')
    products={}
    for label,mode,local,partial in (
        ('audit','audit','retain_conflict','diagnostic_only'),
        ('conservative-cr','cr_only','retain_conflict','diagnostic_only'),
        ('joint-cr','cr_only','source_joint_review','diagnostic_only'),
        ('joint-quarantine','quarantine','source_joint_review','diagnostic_only'),
        ('joint-quarantine-partial-cr','quarantine','source_joint_review','cr_withhold')):
        child=copy.deepcopy(parent)
        c=ReceiverDomainConfig(mode=mode,local_policy=local,partial_policy=partial,
            no_rain_below_dbz=parent['echo']['no_rain_below_dbz'])
        child['profile_version']=parent['profile_version']+'-rdr-20260921-'+label
        child['volume_review']['receiver_domain']=c.model_dump(mode='json')
        VolumeReviewConfig.model_validate(child['volume_review'])
        products['receiver-'+label+'.yaml']=yaml.safe_dump(child,sort_keys=False,allow_unicode=True)
    output.mkdir(parents=True)
    for name,content in products.items():(output/name).write_text(content)
    return products


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--parent',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    generate(yaml.safe_load(a.parent.read_text()),a.output)


if __name__=='__main__':main()
