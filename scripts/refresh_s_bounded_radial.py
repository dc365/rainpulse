#!/usr/bin/env python3
"""Sequential exact-Web S QC -> grid -> mosaic -> QPE -> image refresh on 105.
Run from the deployed repository; state and log remain reusable for review.
"""
import argparse, fcntl, hashlib, json, os, subprocess, time, uuid
from pathlib import Path
from s_web_identity import resolve_frames
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--profile',type=Path,default=ROOT/'configs/qc/s-bounded-radial-20261001-v4.yaml')
parser.add_argument('--output',type=Path,default=ROOT/'.build/s-bounded-radial-20261001-refresh')
parser.add_argument('--plan',type=Path)
parser.add_argument('--prioritize',help='Process this existing UTC issue time first')
args=parser.parse_args()
OUT=args.output.resolve(); OUT.mkdir(parents=True,exist_ok=True)
lock=(OUT/'lock').open('w'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
pid=subprocess.check_output(['systemctl','show','rainpulse','-p','MainPID','--value'],text=True).strip()
env=dict(os.environ)
for entry in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        k,v=entry.split(b'=',1); env[k.decode()]=v.decode()
env['PGPASSWORD']=env['RAINPULSE_DATABASE_PASSWORD']
binary=str(ROOT/'.build/s-bounded-radial-20261001-release/orchestrator')
def query(sql):
    return subprocess.check_output(['psql','-h',env.get('RAINPULSE_DATABASE_HOST','127.0.0.1'),'-p',env.get('RAINPULSE_DATABASE_PORT','5432'),'-U','rainpulse','-d',env.get('RAINPULSE_DATABASE_NAME','rainpulse'),'-At','-c',sql],env=env,text=True).strip()
def request(*args):
    output=subprocess.check_output([binary,*args],env=env,text=True)
    for line in reversed(output.splitlines()):
        try: return json.loads(line)
        except json.JSONDecodeError: pass
    raise RuntimeError('control command returned no JSON')
def wait(job):
    for _ in range(720):
        status=query("SELECT status FROM jobs WHERE job_id='"+str(uuid.UUID(job))+"'")
        if status in ('SUCCEEDED','COMPLETED'): return
        if status in ('FAILED','CANCELLED','DEAD'): raise RuntimeError('job '+job+' '+status)
        time.sleep(10)
    raise TimeoutError(job)
config={k:env['RAINPULSE_PIPELINE_'+v+'_CONFIG'] for k,v in [('qc','QC'),('grid','GRID'),('mosaic','MOSAIC'),('qpe','QPE'),('diagnostics','DIAGNOSTIC')]}
expected=hashlib.sha256(args.profile.read_bytes()).hexdigest()
if hashlib.sha256(Path(config['qc']).read_bytes()).hexdigest()!=expected: raise RuntimeError('active QC profile mismatch')
state_path=OUT/'state.json'
state=json.loads(state_path.read_text()) if state_path.exists() else {'completed':[],'jobs':[]}
def save():
    p=state_path.with_suffix('.tmp'); p.write_text(json.dumps(state,indent=2)); p.replace(state_path)
plan_path=args.plan or OUT/'plan.json'
if plan_path.exists(): plan=json.loads(plan_path.read_text())
else:
    cases=[(s,t,0) for t in ('08:18','08:36','08:42','09:48','10:18','10:24','10:42','11:24') for s in ('z9591','z9593','z9598','z9599')]
    plan=resolve_frames('http://127.0.0.1:4173','2026-08-28',cases); plan_path.write_text(json.dumps(plan,indent=2))
if len(plan)%4 or any(len({f['issue_time'] for f in plan[i:i+4]})!=1 or
                      len({f['site'] for f in plan[i:i+4]})!=4 for i in range(0,len(plan),4)):
    raise ValueError('plan must contain complete distinct four-station groups')
plan_sha=hashlib.sha256(plan_path.read_bytes()).hexdigest()
if state.get('plan_sha256',plan_sha)!=plan_sha:
    raise ValueError('persisted plan identity changed')
state['plan_sha256']=plan_sha
if args.prioritize:
    groups=[plan[i:i+4] for i in range(0,len(plan),4)]
    if args.prioritize not in {g[0]['issue_time'] for g in groups}:
        raise ValueError('priority time absent from frozen plan')
    groups.sort(key=lambda g:g[0]['issue_time']!=args.prioritize)
    plan=[frame for group in groups for frame in group]
def step(stage,target,*args):
    # Reattach to a persisted live/succeeded job; never replace it on a timeout.
    prior=[j for j in state['jobs'] if j.get('profile_sha256')==expected and
           j['slot']==slot and j['stage']==stage and j.get('target')==target and
           (stage=='qc' or j.get('submission_revision')==2)]
    for receipt in reversed(prior):
        status=query("SELECT status FROM jobs WHERE job_id='"+str(uuid.UUID(receipt['job_id']))+"'")
        if status not in ('FAILED','CANCELLED','DEAD'):
            wait(receipt['job_id']); return receipt
    receipt=request(*args)
    state['jobs'].append({'slot':slot,'stage':stage,'target':target,'profile_sha256':expected,'submission_revision':2,**receipt})
    save(); wait(receipt['job_id'])
    if stage=='grid':
        consumed=query("SELECT request_payload->'payload'->>'input_uri' FROM jobs WHERE job_id='"+str(uuid.UUID(receipt['job_id']))+"'")
        committed=query("SELECT qc_uri FROM radar_scan_runs WHERE scan_id='"+str(uuid.UUID(target))+"'")
        if consumed!=committed:raise RuntimeError('grid completed from another QC input')
    return receipt
try:
    if state.get('profile_sha256')!=expected: state['completed']=[]
    state['profile_sha256']=expected; state.pop('error',None)
    state['status']='RUNNING'; save()
    for start in range(0,len(plan),4):
        group=plan[start:start+4]; slot=group[0]['issue_time']
        if slot in state['completed']: continue
        state['current']=slot; save(); scans=[]
        for frame in group:
            scan=frame['web_scan_id']; scans.append(scan)
            for stage,args in [('qc',('radar-qc-rebuild',scan,config['qc'],str(uuid.uuid4()))),('grid',('radar-grid',scan,config['grid']))]:
                step(stage,scan,*args)
        receipt=step('mosaic',slot,'analysis-mosaic',slot,config['mosaic'],*scans)
        analysis=receipt['analysis_id']
        for stage,command in [('qpe','analysis-qpe'),('diagnostics','analysis-diagnostics')]:
            step(stage,analysis,command,analysis,config[stage])
        state['completed'].append(slot); save(); print('PUBLISHED',slot,flush=True)
    state['status']='DONE'; save()
except Exception as exc:
    state['status']='ERROR'; state['error']=str(exc); save(); raise
