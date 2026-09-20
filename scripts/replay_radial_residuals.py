"""Read-only residual proposals on stored QC; never publish or alter raw assets."""
import argparse
import importlib.util
import json
import numpy as np
from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment
from rainpulse_algo.diagnostics.renderer import _open_group
from rainpulse_algo.radar.qc_engine.adapters import NativeSweep


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs',help='JSON list of label, committed QC URI, sweep number')
    p.add_argument('--module',help='candidate residual_objects.py for isolated replay')
    args=p.parse_args()
    if args.module:
        name='rainpulse_algo.radar.qc_engine.review_extension.radial_revision.residual_candidate'
        spec=importlib.util.spec_from_file_location(name,args.module)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        detect=module.detect
    else:
        from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.residual_objects import detect
    reader=ArtifactObjectReader(minio_client_from_environment())
    for label,uri,sweep in json.load(open(args.inputs)):
        root=_open_group(reader.load(uri));g=root[f'sweep_{sweep:03d}']
        z=g['DBZH_RAW'][:];az=g['azimuth'][:];order=np.argsort(az,kind='stable');az=az[order]
        delta=(np.roll(az,-1)-az)%360
        positive=delta[(delta>0)&(delta<5)];spacing=float(np.median(positive)) if len(positive) else 1.
        gaps=(delta<=0)|(delta>1.5*spacing);good=np.isfinite(az)
        valid=(g['VALID_MASK'][:]==1)&np.isfinite(z)
        n=NativeSweep(str(sweep),az,g['elevation'][:][order],g['range'][:],np.arange(len(az)),
            {'DBZH':z[order]},{'DBZH':valid[order]},order,not gaps.any(),good,gaps,{}, {})
        source=np.zeros(z.shape,bool);blocked=source.copy()
        for key in ('SRC_REVIEW_SOURCE_MATCH_MASK','RV2_LINE_MORPH_MASK','RV2_LINE_ISOLATED_MASK','RV2_GROUP_MORPH_MASK'):
            if key in g:source |= g[key][:]==1
        for key in ('SRC_REVIEW_WEATHER_PROTECTED_MASK','SRC_REVIEW_CONFLICT_MASK','RV2_BARRED_MASK'):
            if key in g:blocked |= g[key][:]==1
        out,report=detect(n,blocked[order],source[order])
        target=(out['RV2_RESIDUAL_LINK_MASK']|out['RV2_RESIDUAL_DIRECT_MASK'])==1
        trust=g['REFLECTIVITY_TRUST_MASK'][:][order]==1
        print(json.dumps(dict(case=label,sweep=sweep,**report,new_trusted_gates=int((target&trust).sum()),
              protected_changes=int((target&blocked[order]).sum()),missing_changes=int((target&~valid[order]).sum()),
              verification='proposal replay only; not full worker or ROI efficacy')),flush=True)


if __name__=='__main__':main()
