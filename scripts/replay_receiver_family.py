"""Paired all-ray/all-sweep RAW evidence replay, not a full historical QC rerun.

Uses the frozen parent receiver config, never PNG colors or annotations for
inference. Report targets are read AFTER inference. Requires normalized object
hashes. Production parent weather/QC context is not fabricated from pictures.
"""
import argparse
import json
from pathlib import Path
import sys
import time
import numpy as np
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.receiver_domain.config import ReceiverDomainConfig as C, SourceFamilyConfig as F
from volume_review.receiver_domain.core import evaluate
from volume_review.receiver_domain.family_validation import validate_family_records
from volume_review.receiver_domain.disposition import check_evidence
from receiver_domain_case_io import load_sweep,read_array,path_under,content_digest


def run(cases,output,parent_path,*,portable=False,sites=None,save_masks=False,targets=None):
    output=Path(output)
    if output.exists():raise ValueError('output exists; do not overwrite prior experiments')
    output.mkdir(parents=True)
    parent_doc=yaml.safe_load(Path(parent_path).read_bytes())
    cfg=C.model_validate(parent_doc['volume_review']['receiver_domain'])
    if cfg.source_family is not None:raise ValueError('parent must not have source_family')
    new=cfg.model_copy(update={'source_family':F(mode='experiment',full_policy='quarantine')})
    report={'schema':'rainpulse.receiver-family-raw-replay-v1','status':'RUNNING',
            'kind':'raw_source_evidence_not_production_qc','parent_receiver_config':cfg.model_dump(mode='json'),
            'candidate_receiver_config':new.model_dump(mode='json'),'parent_hash':cfg.digest,'candidate_hash':new.digest,
            'independent_and_unattributed_weather':'absent_not_reconstructed',
            'net_production_isolation':None,'portable_reader':portable,'operational_eligible':False,'cases':[],'targets':[]}
    annotations=[] if targets is None else json.loads(Path(targets).read_text())
    for case in map(Path,cases):
        for line in (case/'metadata/cases.jsonl').read_text().splitlines():
            meta=json.loads(line)
            if sites and meta['site'] not in sites:continue
            root=path_under(case,meta['normalized_path']);sha=content_digest(root)
            if sha!=meta['normalized_sha256_scrubbed']:raise ValueError('normalized content hash mismatch')
            case_report={'case':case.name,'site':meta['site'],'case_id':meta['case_id'],
                         'input_sha256':sha,'hash_verified':True,'qc_output_included':bool(meta.get('qc_path')),'sweeps':[]}
            for number in read_array(root/'sweep_number',portable):
                name=f'sweep_{int(number):03d}';s,order=load_sweep(root/name,portable)
                if not s.observed.any():
                    case_report['sweeps'].append({'sweep':name,'status':'NO_REFLECTIVITY'});continue
                raw_digest=s.digest;tick=time.perf_counter()
                old=evaluate(s,cfg);candidate=evaluate(s,new)
                validate_family_records(candidate.models,s,new)
                check_evidence(candidate.arrays,s.observed,new)
                if s.digest!=raw_digest:raise AssertionError('raw input changed')
                own=old.arrays['RDR_MODEL_AVAILABLE_MASK']==1
                for key,value in old.arrays.items():
                    if not np.array_equal(value[own],candidate.arrays[key][own],equal_nan=True):
                        raise AssertionError('shared family overwrote an available parent model: '+key)
                stat={'sweep':name,'status':'EVALUATED','elevation_deg':float(np.median(s.elevation)),
                      'shape':list(s.shape),'seconds':round(time.perf_counter()-tick,3),
                      'family':candidate.summary.get('source_family',{})}
                for key in ('FULL_MATCH_MASK','PARTIAL_MATCH_MASK','SOURCE_MASK'):
                    a=old.arrays['RDR_'+key]==1;b=candidate.arrays['RDR_'+key]==1
                    if np.any(a&~b) or np.any(b&~s.observed):raise AssertionError('old match lost or invented observation')
                    stat[key]={'before':int(a.sum()),'after':int(b.sum()),'added':int((b&~a).sum())}
                added=(candidate.arrays['RDR_SOURCE_MASK']==1)&(old.arrays['RDR_SOURCE_MASK']==0)
                stat['new_source_by_ray']=[{'original_ray':int(order[rr]),'azimuth_deg':float(s.azimuth[rr]),'gates':int(added[rr].sum())}
                    for rr in np.flatnonzero(added.any(axis=1))]
                case_report['sweeps'].append(stat)
                for t in annotations:
                    if (t['case'],t['site'],t['sweep'])!=(case.name,meta['site'],name):continue
                    diff=abs((s.azimuth-t['azimuth']+180)%360-180);row=int(np.argmin(diff))
                    if diff[row]>.2:raise ValueError('report target azimuth mismatch')
                    selected=s.observed[row]&(s.ranges>=t['minimum_range_m'])&(s.ranges<=t['maximum_range_m'])
                    if 'minimum_dbzh' in t:selected &= s.fields['DBZH'][row]>=t['minimum_dbzh']
                    j=np.flatnonzero(selected);a=candidate.arrays
                    record={**t,'observed_gates':int(len(j)),'actual_azimuth_deg':float(s.azimuth[row]),'gates':[],
                            'before_full':int(old.arrays['RDR_FULL_MATCH_MASK'][row,j].sum()),
                            'after_full':int(a['RDR_FULL_MATCH_MASK'][row,j].sum()),
                            'family_full':int(((a['RDR_FULL_MATCH_MASK'][row,j]==1)&(a['RDR_FAMILY_REFERENCE_MASK'][row,j]==1)).sum()),
                            'after_partial':int(a['RDR_PARTIAL_MATCH_MASK'][row,j].sum())}
                    for gate in j:
                        record['gates'].append({'gate':int(gate),'range_m':float(s.ranges[gate]),'dbzh':float(s.fields['DBZH'][row,gate]),
                            'full':int(a['RDR_FULL_MATCH_MASK'][row,gate]),'partial':int(a['RDR_PARTIAL_MATCH_MASK'][row,gate]),
                            'family_reason':int(a['RDR_FAMILY_REASON'][row,gate])})
                    ids=np.unique(a['RDR_MODEL_ID'][row,j]);record['reference_models']=[candidate.models[i-1] for i in ids if i>0]
                    report['targets'].append(record)
                if save_masks:
                    folder=output/case.name/meta['site'];folder.mkdir(parents=True,exist_ok=True)
                    keys=('FULL_MATCH_MASK','PARTIAL_MATCH_MASK','SOURCE_MASK','MODEL_AVAILABLE_MASK')
                    np.savez_compressed(folder/(name+'.npz'),**{'old_'+k:old.arrays['RDR_'+k] for k in keys},
                        **{'new_'+k:candidate.arrays['RDR_'+k] for k in keys},
                        **{k:v for k,v in candidate.arrays.items() if k.startswith('RDR_FAMILY_')})
                print(case.name,meta['site'],name,'new',stat['SOURCE_MASK']['added'],'seconds',stat['seconds'],flush=True)
            report['cases'].append(case_report)
            (output/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    report['status']='COMPLETE'
    report['totals']={'volumes':len(report['cases']),'sweeps':sum(len(c['sweeps']) for c in report['cases']),
        'reflectivity_sweeps':sum(s['status']=='EVALUATED' for c in report['cases'] for s in c['sweeps'])}
    for key in ('FULL_MATCH_MASK','PARTIAL_MATCH_MASK','SOURCE_MASK'):
        report['totals'][key]={phase:sum(s.get(key,{}).get(phase,0) for c in report['cases'] for s in c['sweeps']) for phase in ('before','after','added')}
    (output/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',nargs='+',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    p.add_argument('--parent',required=True,type=Path);p.add_argument('--portable-reader',action='store_true')
    p.add_argument('--sites',nargs='+');p.add_argument('--save-masks',action='store_true');p.add_argument('--targets',type=Path)
    a=p.parse_args();run(a.cases,a.output,a.parent,portable=a.portable_reader,sites=a.sites,save_masks=a.save_masks,targets=a.targets)
