#!/usr/bin/env python3
"""Exercise small real-data comparison/PSD and a two-lead background report."""
import argparse
import json
import time
import urllib.request

parser=argparse.ArgumentParser()
parser.add_argument('--base-url',required=True)
parser.add_argument('--cycle-id',required=True)
args=parser.parse_args()
base=args.base_url.rstrip('/')+'/api/v1/workspace/verification'
def call(path,data=None):
    request=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:
        return json.load(response)

algorithms=['lk','steps','nowcastnet']
result=call('/compare',dict(cycle_id=args.cycle_id,algorithms=algorithms,lead_minutes=30,psd=True))
assert result['status']=='ready',result
counts=[m['valid_cells'] for m in result['metrics'].values()]
assert len(set(counts))==1 and counts[0]>0
assert result['psd']['status']=='ready',result['psd']
assert len(result['psd']['series'])==4
assert all(v>=0 for series in result['psd']['series'].values() for v in series)
print(json.dumps(dict(common_cells=counts[0],psd_shape=result['psd']['shape'],psd_bounds=result['psd']['bounds'])),flush=True)
job=call('/jobs',dict(cycle_ids=[args.cycle_id],algorithms=algorithms,leads=[30,110],threshold=5,window_km=10))
deadline=time.monotonic()+240
while job['status']=='running' and time.monotonic()<deadline:
    time.sleep(2)
    job=call('/jobs/'+job['id'])
assert job['status']=='complete',job
assert not job.get('persistence_error'),job.get('persistence_error')
assert job['summary']['matched_records']==2
assert job['summary']['overall']['lk']['csi']['n']==2
print(json.dumps(dict(job_id=job['id'],status=job['status'],completed=job['completed'],summary=job['summary']['overall'])),flush=True)
