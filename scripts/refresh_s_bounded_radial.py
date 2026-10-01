#!/usr/bin/env python3
"""Sequential exact-Web S QC -> grid -> mosaic -> QPE -> image refresh on 105.
Run from the deployed repository; state and log remain reusable for review.
"""
import fcntl, hashlib, json, os, subprocess, time, uuid
from pathlib import Path
from s_web_identity import resolve_frames
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.build/s-bounded-radial-20261001-refresh'; OUT.mkdir(parents=True,exist_ok=True)
lock=(OUT/'lock').open('w'); fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
pid=subprocess.check_output(['systemctl','show','rainpulse','-p','MainPID','--value'],text=True).strip()
env=dict(os.environ)
for entry in Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
    if b'=' in entry:
        k,v=entry.split(b'=',1); env[k.decode()]=v.decode()
env['PGPASSWORD']=env['RAINPULSE_DATABASE_PASSWORD']
binary=str(ROOT/'.build/sx-priority1/sx-priority1-orchestrator')
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
expected='eeaf33514309ac00645722f77040220d7aa120347a7cb210aba076cc37404810'
if hashlib.sha256(Path(config['qc']).read_bytes()).hexdigest()!=expected: raise RuntimeError('active QC profile mismatch')
state_path=OUT/'state.json'
state=json.loads(state_path.read_text()) if state_path.exists() else {'completed':[],'jobs':[]}
def save():
    p=state_path.with_suffix('.tmp'); p.write_text(json.dumps(state,indent=2)); p.replace(state_path)
plan_path=OUT/'plan.json'
if plan_path.exists(): plan=json.loads(plan_path.read_text())
else:
    cases=[(s,t,0) for t in ('08:18','08:36','08:42','09:48','10:18','10:24','10:42','11:24') for s in ('z9591','z9593','z9598','z9599')]
    plan=resolve_frames('http://127.0.0.1:4173','2026-08-28',cases); plan_path.write_text(json.dumps(plan,indent=2))
try:
    state['status']='RUNNING'; save()
    for start in range(0,len(plan),4):
        group=plan[start:start+4]; slot=group[0]['issue_time']
        if slot in state['completed']: continue
        state['current']=slot; save(); scans=[]
        for frame in group:
            scan=frame['web_scan_id']; scans.append(scan)
            for stage,args in [('qc',('radar-qc-rebuild',scan,config['qc'],str(uuid.uuid4()))),('grid',('radar-grid',scan,config['grid']))]:
                receipt=request(*args); state['jobs'].append({'slot':slot,'stage':stage,**receipt}); save(); wait(receipt['job_id'])
        receipt=request('analysis-mosaic',slot,config['mosaic'],*scans); state['jobs'].append({'slot':slot,'stage':'mosaic',**receipt}); save(); wait(receipt['job_id'])
        analysis=receipt['analysis_id']
        for stage,command in [('qpe','analysis-qpe'),('diagnostics','analysis-diagnostics')]:
            receipt=request(command,analysis,config[stage]); state['jobs'].append({'slot':slot,'stage':stage,**receipt}); save(); wait(receipt['job_id'])
        state['completed'].append(slot); save(); print('PUBLISHED',slot,flush=True)
    state['status']='DONE'; save()
except Exception as exc:
    state['status']='ERROR'; state['error']=str(exc); save(); raise
