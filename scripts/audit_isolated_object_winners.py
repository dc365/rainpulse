#!/usr/bin/env python3
"""Rebuild a frozen CR winner from explicitly bound original-source NPZ arrays.

Manifest source indices MUST be the real Composite.sources indices (not radar
numbers or guessed filenames). This read-only adapter does not accept screenshots.
"""
from pathlib import Path
import argparse,hashlib,json
import numpy as np


def check(composite,sources,*,reject_mask=0):
    if type(reject_mask) is not int or not 0<=reject_mask<2**32:
        raise ValueError("invalid frozen rejection bitmask")
    required=('CR_TRUSTED','WINNER_SOURCE','WINNER_RAY','WINNER_GATE')
    if any(k not in composite for k in required):raise ValueError('incomplete CR winner arrays')
    cr=np.asarray(composite['CR_TRUSTED']);shape=cr.shape
    if cr.ndim!=2 or np.isinf(cr).any():raise ValueError('invalid CR values')
    for k in required[1:]:
        v=np.asarray(composite[k])
        if v.shape!=shape or v.dtype.kind not in 'iu':raise ValueError('invalid winner indices')
    valid=np.isfinite(cr);sid=np.asarray(composite['WINNER_SOURCE'])
    if np.any(valid&(sid<0)) or np.any(~valid&(sid!=-1)):raise ValueError('winner/CR availability mismatch')
    for k in ('WINNER_RAY','WINNER_GATE'):
        if np.any(~valid&(np.asarray(composite[k])!=-1)):raise ValueError('missing CR has winner coordinates')
    counts={};total=0
    for i in np.unique(sid[valid]):
        if int(i) not in sources:raise ValueError('winner source not explicitly supplied')
        a=sources[int(i)];pick=valid&(sid==i)
        r=np.asarray(composite['WINNER_RAY'])[pick];g=np.asarray(composite['WINNER_GATE'])[pick]
        required_native=('DBZH_QC','VALID_MASK','REFLECTIVITY_ELIGIBLE_FOR_CR','CF_ISO_CR_WITHHELD_MASK','CF_ISO_OBJECT_ID','CF_ISO_REASON')
        if any(k not in a for k in required_native):raise ValueError('native source lacks isolation evidence')
        z=np.asarray(a['DBZH_QC'])
        if z.ndim!=2 or np.any((r<0)|(r>=z.shape[0])|(g<0)|(g>=z.shape[1])):raise ValueError('winner index out of range')
        for key in required_native:
            if np.asarray(a[key]).shape!=z.shape:raise ValueError('native shape mismatch')
        for key in ('VALID_MASK','REFLECTIVITY_ELIGIBLE_FOR_CR','CF_ISO_CR_WITHHELD_MASK'):
            if not np.isin(a[key],(0,1)).all():raise ValueError('invalid native mask')
        if np.any(np.asarray(a['CF_ISO_CR_WITHHELD_MASK'])[r,g]==1):raise ValueError('isolated withholding leaked into CR')
        if np.any(np.asarray(a['VALID_MASK'])[r,g]!=1) or np.any(np.asarray(a['REFLECTIVITY_ELIGIBLE_FOR_CR'])[r,g]!=1):
            raise ValueError('ineligible winner')
        if not np.array_equal(z[r,g],cr[pick]):raise ValueError('CR winner value differs from measured contribution')
        if reject_mask:
            if 'QC_FLAGS' not in a or np.asarray(a['QC_FLAGS']).shape!=z.shape:
                raise ValueError('flags required by rejection contract')
            if np.any((np.asarray(a['QC_FLAGS'])[r,g].astype('uint32')&np.uint32(reject_mask))!=0):
                raise ValueError('rejected flag leaked into CR')
        for optional in ('CF_CR_WITHHELD_MASK','NMR_CR_WITHHELD_MASK','RDR_CR_WITHHELD_MASK'):
            if optional in a and np.any(np.asarray(a[optional])[r,g]==1):raise ValueError('parent exclusion leaked into CR')
        obj=np.asarray(a['CF_ISO_OBJECT_ID'])[r,g];reasons=np.asarray(a['CF_ISO_REASON'])[r,g]
        counts[str(int(i))]={'winner_cells':int(pick.sum()),'original_objects':int(len(np.unique(obj[obj>0]))),
                            'reason_counts':{str(int(v)):int((reasons==v).sum()) for v in np.unique(reasons)}}
        total+=int(pick.sum())
    return {'schema':'isolated-winner-audit-v1','matched_winner_cells':total,'sources':counts,
            'eligibility_leaks':0,'value_mismatches':0,'truth_classification_evaluated':False}


def load_bound(root,entry):
    p=(root/entry['path']).resolve()
    if not p.is_relative_to(root):raise ValueError('manifest path escapes root')
    data=p.read_bytes()
    if hashlib.sha256(data).hexdigest()!=entry['sha256']:raise ValueError('bound numeric file hash mismatch')
    with np.load(p,allow_pickle=False) as archive:return {k:archive[k] for k in archive.files}


def run(manifest_path):
    manifest_path=Path(manifest_path).resolve();m=json.loads(manifest_path.read_text());root=manifest_path.parent
    if m.get('schema')!='isolated-winner-bundle-v1':raise ValueError('unsupported audit bundle')
    sources={}
    for entry in m['sources']:
        i=entry['source_index']
        if type(i) is not int or i<0 or i in sources:raise ValueError('duplicate/invalid source index')
        if not entry.get('qc_asset_sha256') or not entry.get('qc_parameters_sha256'):raise ValueError('source identity is missing')
        sources[i]=load_bound(root,entry)
    return check(load_bound(root,m['composite']),sources,reject_mask=m['reject_mask'])

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.output.exists():raise FileExistsError(a.output)
    a.output.write_text(json.dumps(run(a.manifest),indent=2))
