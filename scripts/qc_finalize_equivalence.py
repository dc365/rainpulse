"""Compare a frozen lowest cut against an explicitly supplied previous runner.

Run in the measurement container: /workspace checkout, /frozen case roots,
/before.py previous runner. No external context; not published-product replay.
"""
import importlib.util,json
from pathlib import Path
import numpy as np
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.runner import run_open_source_qc
spec=importlib.util.spec_from_file_location('rainpulse_algo.radar.qc_engine.runner_before','/before.py')
old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
root=Path('/frozen'); case=next(root.glob('*/normalized.zarr'))
objects={p.relative_to(case).as_posix():p.read_bytes() for p in case.rglob('*')if p.is_file() and p.name!='.download-complete'}
p=load_qc_profile('/workspace/configs/qc/fujian-qc-evidence-graph-v7.yaml','/workspace/configs/qc/flag-definitions-v2.yaml')
import zarr
from zarr.storage import MemoryStore
store=MemoryStore();store.update(objects);group=zarr.open_group(store,mode='a')
group.create_dataset('sweep_number',data=np.array([0],dtype=group['sweep_number'].dtype),overwrite=True)
objects=dict(store)
a=old.run_open_source_qc(objects,p); timings={};b=run_open_source_qc(objects,p,timing_sink=timings)
assert a.summary==b.summary
count=0
for x,y in zip(a.sweeps,b.sweeps,strict=True):
 for key,value in vars(x).items():
  other=getattr(y,key)
  if isinstance(value,np.ndarray):np.testing.assert_equal(value,other);count+=1
  elif isinstance(value,dict):
   assert value.keys()==other.keys()
   for k in value:np.testing.assert_equal(value[k],other[k]);count+=1
  else:assert value==other
print(json.dumps({'arrays_equal':count,'summary_equal':True,'timings':timings,'scope':'one_frozen_lowest_cut_same_absent_external_context_not_published_baseline'}))
