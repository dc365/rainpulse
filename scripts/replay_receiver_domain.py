"""Read-only source-stage replay. Missing current QC is NOT fabricated as trusted.

Saves coherent model evidence for every sweep. Optional target annotations are
used only in reporting, never given to the detector. Does not publish products.
"""
from pathlib import Path
import argparse
import json
import sys
import time
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.config import VolumeReviewConfig
from volume_review.receiver_domain.config import ReceiverDomainConfig
from volume_review.receiver_domain.core import evaluate
from volume_review.source import local_weather
from receiver_domain_case_io import load_sweep,read_array,path_under,content_digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',nargs='+',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path)
    p.add_argument('--portable-reader',action='store_true')
    p.add_argument('--targets',type=Path)
    p.add_argument('--restrict-snr-to-dbzh',action='store_true',help='ablation only; never the default')
    a=p.parse_args()
    if a.output.exists():raise ValueError('output exists; choose a new directory')
    a.output.mkdir(parents=True)
    annotations=[] if a.targets is None else json.loads(a.targets.read_text())
    cfg=ReceiverDomainConfig(mode='quarantine',local_policy='source_joint_review')
    report={'kind':'raw_source_stage_not_current_QC_replay','config':cfg.model_dump(mode='json'),
        'config_sha256':cfg.digest,'portable_reader':a.portable_reader,
        'restriction_ablation':a.restrict_snr_to_dbzh,'cases':[],'targets':[],
        'current_pipeline_net_changes':None,'operational_eligible':False}
    for case in a.cases:
        for line in (case/'metadata/cases.jsonl').read_text().splitlines():
            meta=json.loads(line);src=path_under(case,meta['normalized_path'])
            sha=content_digest(src)
            if sha!=meta['normalized_sha256_scrubbed']:raise ValueError('normalized content hash differs')
            record={'case':case.name,'site':meta['site'],'case_id':meta['case_id'],
                'input_sha256':sha,'hash_verified':True,'qc_output_included':bool(meta.get('qc_path')),'sweeps':[]}
            numbers=read_array(src/'sweep_number',a.portable_reader)
            for number in numbers:
                name=f'sweep_{int(number):03d}';start=time.perf_counter()
                s,order=load_sweep(src/name,a.portable_reader)
                if not s.observed.any():
                    record['sweeps'].append({'sweep':name,'status':'NO_REFLECTIVITY','full_matches':0,'partial_matches':0});continue
                # Recompute local coherence, but do not invent absent independent
                # weather/QC masks. Therefore this remains an EVIDENCE replay.
                local=local_weather(s,VolumeReviewConfig())
                ev=evaluate(s,cfg,local_coherence=local,restrict_snr_to_dbzh=a.restrict_snr_to_dbzh)
                outdir=a.output/case.name/meta['site'];outdir.mkdir(parents=True,exist_ok=True)
                np.savez_compressed(outdir/(name+'.npz'),**ev.arrays)
                (outdir/(name+'-models.json')).write_text(json.dumps(ev.models,ensure_ascii=False))
                summary={'sweep':name,'shape':list(s.shape),'elevation':float(np.median(s.elevation)),
                    **ev.summary,'elapsed_seconds':round(time.perf_counter()-start,3),
                    'independent_weather_status':'not_reconstructed_from_case'}
                record['sweeps'].append(summary)
                for t in annotations:
                    if t['case']!=case.name or t['site']!=meta['site'] or t['sweep']!=name:continue
                    angle=np.abs((s.azimuth-t['azimuth']+180)%360-180);row=int(np.argmin(angle))
                    if angle[row]>.2:raise ValueError('annotation angle no longer matches source')
                    domain=(s.ranges>=t.get('min_m',50000))&(s.ranges<=t.get('max_m',240000))&s.observed[row]&(s.fields['DBZH'][row]>=t.get('minimum_dbzh',10))
                    masks={k:ev.arrays['RDR_'+k][row]==1 for k in ['FULL_MATCH_MASK','PARTIAL_MATCH_MASK','SOURCE_MASK']}
                    stat={**t,'azimuth_actual':float(s.azimuth[row]),'domain_gates':int(domain.sum()),
                        **{k:int((v&domain).sum()) for k,v in masks.items()},
                        'local_coherence_full_overlap':int((local[row]&masks['FULL_MATCH_MASK']&domain).sum())}
                    if t.get('qc_png'):
                        from PIL import Image
                        from volume_review.sampling import polar_pixels
                        png=np.array(Image.open(path_under(case,t['qc_png'])).convert('RGBA'))
                        # Numeric polar rows are acquisition-order in packaged images.
                        az_raw=read_array(src/name/'azimuth',a.portable_reader)
                        rr,gg,ok=polar_pixels(az_raw,s.ranges,png.shape[0])
                        visible=(png[:,:,3]>0)&ok&(rr==int(order[row]))
                        gates=np.unique(gg[visible]);gates=gates[domain[gates]]
                        stat['old_png_visible_sample_gates']=int(len(gates))
                        stat['old_png_full_match_samples']=int(masks['FULL_MATCH_MASK'][gates].sum())
                        stat['old_png_partial_match_samples']=int(masks['PARTIAL_MATCH_MASK'][gates].sum())
                    report['targets'].append(stat)
                print(case.name,meta['site'],name,ev.summary['full_matches'],ev.summary['partial_matches'],flush=True)
            report['cases'].append(record)
            (a.output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    report['totals']={k:sum(s.get(k,0) for c in report['cases'] for s in c['sweeps']) for k in ['full_matches','partial_matches','source_qualified','local_reviewed']}
    report['sweeps']=sum(len(c['sweeps']) for c in report['cases'])
    (a.output/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
