#!/usr/bin/env python3
"""Read-only synthetic C/D mechanism benchmark, not operational throughput."""
from pathlib import Path
from dataclasses import replace
import argparse,hashlib,json,os,sys,tempfile,time,resource
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'algorithms'),str(ROOT/'algorithms/tests/performance_cd_20260927')]
from test_fusion_rdr import scene,original_stream,rdr_scene,before_rdr
from rainpulse_algo.multiband.stream_fusion import build_composite_streaming
from rainpulse_algo.multiband.execution import ExecutionOptions
from rainpulse_algo.radar.qc_engine.volume_review.receiver_domain.core import evaluate


def fingerprint(result):
    h=hashlib.sha256()
    for product in [result,result.band_comparisons['S'],result.band_comparisons['X']]:
        for name,a in sorted(product.arrays.items()):
            a=a.copy()
            if a.dtype.kind=='f':a[np.isnan(a)]=np.nan
            h.update(name.encode());h.update(a.tobytes())
    return h.hexdigest()


def run(mode,memory):
    v,n=scene(cuts=8);g=replace(n.products['demo'],width=120,height=96,tile_rows=8,
        levels_m_msl=(250.,500.,1000.,2000.,4000.,6000.))
    n=replace(n,products={'demo':g});o=ExecutionOptions(streaming=True,layer_memory_bytes=memory)
    module=original_stream();instances=[]
    class Tracking(module.LayerWorkspace):
        def __init__(self,*a,**kw):super().__init__(*a,**kw);instances.append(self)
    module.LayerWorkspace=Tracking
    metrics={}
    with tempfile.TemporaryDirectory(prefix='cd-benchmark-') as root:
        fn=module.build_composite_streaming if mode=='before' else build_composite_streaming
        started=time.perf_counter();result=fn(iter(v),n,'demo','2026-08-28T00:06:00Z','2026-08-28T00:06:00Z',
            options=o,directory=root,metrics=metrics,comparison=True)
        elapsed=(time.perf_counter()-started)*1000
    if mode=='before':
        metrics['aggregate_mapped_read_bytes']=sum(x.read_bytes for x in instances)
        metrics['aggregate_flushed_mapping_bytes']=sum(x.write_bytes for x in instances)
    return dict(mode=mode,elapsed_ms=elapsed,peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
                output_sha256=fingerprint(result),grid_shape=[96,120],levels=6,cuts=16,
                finite_cells=int(np.isfinite(result.arrays['CR_DBZH']).sum()),layer_memory_budget=memory,metrics=metrics)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=['before','after'],required=True)
    p.add_argument('--memory-mib',type=int,default=64);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=run(a.mode,a.memory_mib*1024**2)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
