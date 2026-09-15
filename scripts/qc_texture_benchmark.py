"""Read-only benchmark: mount checkout /workspace, previous algorithms.py
/before.py, frozen case roots /frozen. Warm both paths, compare every array.
"""
import importlib.util,sys,time,json
from pathlib import Path
import numpy as np
from rainpulse_algo.radar.qc_engine.measurement_v8.case import FrozenCase
from rainpulse_algo.radar.qc_engine.algorithms import library_evidence
name='rainpulse_algo.radar.qc_engine.algorithms_before';spec=importlib.util.spec_from_file_location(name,'/before.py');old=importlib.util.module_from_spec(spec);sys.modules[name]=old;spec.loader.exec_module(old)
c=FrozenCase(next(Path('/frozen').glob('*/case.json')));n,_,_=c.sweep('sweep_000')
old.library_evidence(n,c.profile);library_evidence(n,c.profile)
times={'before':[],'after':[]}
for i in range(3):
 for key,fn in [('before',old.library_evidence),('after',library_evidence)]:
  t=time.perf_counter();r=fn(n,c.profile);times[key].append((time.perf_counter()-t)*1000)
  if key=='before':a=r
  else:
   assert a.arrays.keys()==r.arrays.keys()
   for k in a.arrays:np.testing.assert_equal(a.arrays[k],r.arrays[k])
print(json.dumps({'timings_ms':times,'equal_arrays':len(a.arrays),'scan':c.spec.expected_scan_id}))
