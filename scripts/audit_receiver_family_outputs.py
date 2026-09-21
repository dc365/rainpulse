"""Compare COMPLETE serialized parent/candidate QC stores, without publishing.

This checks content/eligibility invariants and net changes, not meteorological
truth. It does not replace the full existing QC validators or CR winner replay.
Stores must be unpacked logical Zarr roots, not a packs/*.bin publication path.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'algorithms/rainpulse_algo/radar/qc_engine'))
from volume_review.receiver_domain.config import ReceiverDomainConfig
from volume_review.receiver_domain.disposition import CR


def compare(parent,candidate):
    pc=ReceiverDomainConfig.model_validate(parent.attrs.get('qc_receiver_domain_config',{}))
    cc=ReceiverDomainConfig.model_validate(candidate.attrs.get('qc_receiver_domain_config',{}))
    plain=cc.model_copy(update={'source_family':None})
    if pc.source_family is not None or cc.source_family is None or pc.model_dump()!=plain.model_dump():
        raise ValueError('comparison must change only the receiver source-family configuration')
    for root,cfg in ((parent,pc),(candidate,cc)):
        if root.attrs.get('qc_receiver_domain_sha256')!=cfg.digest:
            raise ValueError('receiver config hash differs')
    for key in ('radar_id','scan_id','qc_clutter_fusion_sha256','qc_near_measurement_sha256'):
        if parent.attrs.get(key)!=candidate.attrs.get(key):
            raise ValueError('source or non-radial configuration differs: '+key)
    numbers=np.asarray(parent['sweep_number'][:])
    if not np.array_equal(numbers,np.asarray(candidate['sweep_number'][:])):
        raise ValueError('sweep identities differ')
    rows=[]
    for number in numbers:
        name=f'sweep_{int(number):03d}';a,b=parent[name],candidate[name]
        for key in ('azimuth','elevation','range','DBZH_RAW','VALID_MASK'):
            if not np.array_equal(np.asarray(a[key][:]),np.asarray(b[key][:]),equal_nan=True):
                raise ValueError('raw/geometry changed: '+name+'/'+key)
        cr_a,cr_b=np.asarray(a[CR][:])==1,np.asarray(b[CR][:])==1
        qa,qb=np.asarray(a['QPE_ELIGIBLE_MASK'][:])==1,np.asarray(b['QPE_ELIGIBLE_MASK'][:])==1
        if np.any(cr_b&~cr_a) or np.any(qb&~qa):
            raise ValueError('candidate restored an ineligible parent measurement')
        family=np.asarray(b['RDR_FAMILY_CR_WITHHELD_MASK'][:])==1
        quarantine=np.asarray(b['RDR_FAMILY_QUARANTINE_MASK'][:])==1
        if np.any(family&cr_b) or np.any(quarantine&qb):
            raise ValueError('source-family withheld measurement leaked into CR/QPE')
        rows.append({'sweep':name,'new_cr_excluded':int((cr_a&~cr_b).sum()),
            'new_qpe_excluded':int((qa&~qb).sum()),'family_stage_cr_withheld':int(family.sum()),
            'family_stage_quarantined':int(quarantine.sum()),
            'family_unresolved':int((np.asarray(b['RDR_FAMILY_UNRESOLVED_MASK'][:])==1).sum())})
    return {'schema':'rainpulse.receiver-family-paired-qc-audit-v1','status':'INVARIANTS_PASSED',
            'meteorological_acceptance':False,'full_weather_context_equivalence':'not_verified_by_this_tool',
            'parent_receiver_sha256':pc.digest,'candidate_receiver_sha256':cc.digest,
            'sweeps':rows,'total_new_cr_excluded':sum(x['new_cr_excluded'] for x in rows),
            'total_new_qpe_excluded':sum(x['new_qpe_excluded'] for x in rows)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent-qc',required=True,type=Path);p.add_argument('--candidate-qc',required=True,type=Path)
    p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    import zarr
    report=compare(zarr.open_group(str(a.parent_qc),mode='r'),zarr.open_group(str(a.candidate_qc),mode='r'))
    payload=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False).encode()
    a.output.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.family-audit-',dir=a.output.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(payload);f.flush();os.fsync(f.fileno())
        os.link(name,a.output)  # fail if destination exists; no silent overwrite
    finally:os.unlink(name)
    print(json.dumps({'output':str(a.output),'status':report['status']}))


if __name__=='__main__':main()
