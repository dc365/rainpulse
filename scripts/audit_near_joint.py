"""Audit unpacked full QC Zarr and optional CR receipts. Never uses PNG labels.

This runs in the actual RainPulse worker environment. Use the case packager to
unpack a published store first. Missing artifacts are explicit failures, not
zero clutter reports. All operations are read-only except the selected report.
"""
from pathlib import Path
import argparse,json,sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.receipts import snapshot_group
from volume_review.data import array_digest
from volume_review.clutter_fusion.config import ClutterFusionConfig


def audit(root):
    attrs=dict(root.attrs);out={'radar_id':attrs.get('radar_id'),'scan_id':attrs.get('scan_id'),
        'qc_parameters_sha256':attrs.get('qc_parameters_sha256'),
        'near_enabled_effective':attrs.get('qc_near_clutter_enabled','NOT_RECORDED'),
        'near_mode':attrs.get('qc_near_clutter_mode','NOT_RECORDED'),'sweeps':[]}
    cfg=ClutterFusionConfig.model_validate(attrs.get('qc_clutter_fusion_config',{}))
    if cfg.digest!=attrs.get('qc_clutter_fusion_sha256'):raise ValueError('QC effective configuration digest differs')
    out['background_bindings']={k:{'sha256':b.sha256,'path':b.path} for k,b in cfg.background.assets.items()}
    for number in root['sweep_number'][:]:
        group=root[f'sweep_{int(number):03d}'];a={k:np.asarray(group[k][:]) for k in group if hasattr(group[k],'shape')}
        obs=a['VALID_MASK']==1;cr=a['REFLECTIVITY_ELIGIBLE_FOR_CR']==1
        record={'sweep':int(number),'observed':int(obs.sum()),'cr_eligible':int(cr.sum()),'fields':{}}
        for name in ('NP_NEAR_CANDIDATE_MASK','NP_QUARANTINE_MASK','CF_BG_AVAILABLE_MASK','CF_BG_STABLE_MASK',
                     'CF_BG_MATCH_MASK','CF_BG_CURRENT_NONMET_MASK','CF_BG_ENHANCEMENT_MASK','CF_NR_READY_MASK',
                     'CF_NR_STRICT_MASK','CF_NR_TEMPORAL_SUPPORT_MASK','CF_NR_PROTECTED_MASK','CF_NR_DEM_AVAILABLE_MASK',
                     'CF_NR_DEM_ACTION_AVAILABLE_MASK','CF_NR_DEM_LOCAL_INTERCEPTION_MASK','CF_NR_DEM_SEVERE_MASK',
                     'CF_NR_CR_WITHHELD_MASK','CF_NR_PARTIAL_CR_WITHHELD_MASK','CF_NR_TEMPORAL_CR_WITHHELD_MASK','CF_NR_DEM_CR_WITHHELD_MASK'):
            record['fields'][name]=int((a[name]==1).sum()) if name in a else None
        for name in ('NP_QUARANTINE_MASK','NMR_CR_WITHHELD_MASK','RDR_CR_WITHHELD_MASK','CF_CR_WITHHELD_MASK','CF_NR_CR_WITHHELD_MASK'):
            if name in a and np.any((a[name]==1)&cr):raise ValueError('ineligible contribution leaked into CR: '+name)
        if 'CF_BG_STATE' in a:
            keys,count=np.unique(a['CF_BG_STATE'][obs],return_counts=True);record['background_state_counts']=dict(zip(map(str,keys),map(int,count)))
        if 'CF_NR_REASON' in a:
            from volume_review.clutter_fusion.near_joint import Reason
            record['near_reason_counts']={x.name:int(((a['CF_NR_REASON']&int(x))!=0).sum()) for x in Reason}
        out['sweeps'].append(record)
    out['status']='CHECKED_NO_ELIGIBILITY_LEAK';return out


def check_composite(roots,path,metadata_path):
    from volume_review.receipts import load_npz
    import hashlib
    payload=Path(path).read_bytes();meta=json.loads(Path(metadata_path).read_text())
    if hashlib.sha256(payload).hexdigest()!=meta['payload_sha256']:raise ValueError('CR payload identity mismatch')
    a=load_npz(payload)
    if array_digest(a)!=meta['numeric_sha256']:raise ValueError('CR numeric digest mismatch')
    count=0;winner_audit=[]
    for sid,source in enumerate(meta['sources']):
        root=roots[source['radar']];g=root[f"sweep_{source['sweep']:03d}"]
        if array_digest(snapshot_group(g))!=source['numeric_sha256']:raise ValueError('CR source numeric digest mismatch')
        m=a['WINNER_SOURCE']==sid
        rows=a['WINNER_RAY'][m];gates=a['WINNER_GATE'][m]
        value=g['DBZH_QC'][:][rows,gates];eligible=g['REFLECTIVITY_ELIGIBLE_FOR_CR'][:][rows,gates]
        if not np.array_equal(value,a['CR_TRUSTED'][m]) or np.any(eligible!=1):raise ValueError('winner reconstruction failed')
        item={'source':sid,'radar_id':source.get('radar_id'),'sweep':source['sweep'],'winner_cells':int(m.sum()),'evidence':{}}
        for key in ('CF_BG_AVAILABLE_MASK','CF_BG_STABLE_MASK','CF_BG_MATCH_MASK','CF_BG_CURRENT_NONMET_MASK',
                    'CF_BG_ENHANCEMENT_MASK','CF_NR_READY_MASK','CF_NR_STRICT_MASK','CF_NR_TEMPORAL_SUPPORT_MASK',
                    'CF_NR_PROTECTED_MASK','CF_NR_DEM_AVAILABLE_MASK','CF_NR_DEM_ACTION_AVAILABLE_MASK',
                    'NP_NEAR_CANDIDATE_MASK','NP_WEATHER_PROTECTED_MASK','NP_MIXED_MASK'):
            item['evidence'][key]=int((g[key][:][rows,gates]==1).sum()) if key in g else None
        for key in ('CF_BG_STATE','CF_NR_STATE'):
            if key in g:
                values,num=np.unique(g[key][:][rows,gates],return_counts=True)
                item[key+'_counts']=dict(zip(map(str,values),map(int,num)))
        winner_audit.append(item)
        count+=int(m.sum())
    if count!=int(np.isfinite(a['CR_TRUSTED']).sum()):raise ValueError('unidentified finite CR winner')
    return {'winner_reconstructed':count,'status':'RECONSTRUCTED','unavailable_is_zero_rain':False,'winner_evidence_by_source':winner_audit}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--qc-zarr',type=Path,nargs='+',required=True)
    p.add_argument('--composite-npz',type=Path);p.add_argument('--composite-json',type=Path);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if bool(args.composite_npz)!=bool(args.composite_json):p.error('both CR numeric and JSON receipt required')
    if args.output.exists():raise SystemExit('output exists')
    import zarr
    roots=[zarr.open_group(str(x),mode='r') for x in args.qc_zarr]
    report={'volumes':[audit(x) for x in roots],'source_files_unchanged':True}
    if args.composite_npz:report['composite']=check_composite(roots,args.composite_npz,args.composite_json)
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
