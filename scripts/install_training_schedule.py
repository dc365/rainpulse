"""Install the user-authorized weekday/night and weekend training schedule on GPU host."""
from pathlib import Path
import subprocess

home = Path.home()
lib = home / '.local/libexec'
units = home / '.config/systemd/user'
guard = lib / 'rainpulse-nowcastnet-generative-formal-guard.sh'
old = 'if (( 10#$hour >= 8 && 10#$hour < 20 )); then'
new = 'weekday=$(TZ=Asia/Taipei date +%u)\nif (( 10#$weekday <= 5 && 10#$hour >= 8 && 10#$hour < 20 )); then'
content = guard.read_text()
if old in content:
    backup = guard.with_suffix('.sh.before-weekly-schedule')
    if not backup.exists():
        backup.write_text(content)
    guard.write_text(content.replace(old, new))
elif new not in content:
    raise SystemExit('Unexpected guard: refusing to change it')

launcher = '''#!/usr/bin/env python3
import datetime, fcntl, json, os, pathlib, subprocess, tempfile
from zoneinfo import ZoneInfo
now = datetime.datetime.now(ZoneInfo("Asia/Taipei"))
if now.weekday() < 5 and 8 <= now.hour < 20:
    raise SystemExit(0)
lock = open(pathlib.Path.home()/".config/rainpulse/weekly-training.lock", "w")
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
unit = "rainpulse-nowcastnet-generative.service"
state = subprocess.check_output(["systemctl","--user","show",unit,"-p","ActiveState","--value"],text=True).strip()
if state in ("active", "activating", "deactivating"):
    raise SystemExit(0)
e = os.environ
latest = json.loads((pathlib.Path(e["RAINPULSE_GENERATIVE_RUN_DIR"])/"LATEST.json").read_text())
if latest["global_step"] >= int(e["RAINPULSE_GENERATIVE_TARGET_STEP"]):
    print("Target reached; no further training")
    raise SystemExit(0)
used = pathlib.Path(e["RAINPULSE_GENERATIVE_REPORT_DIR"])/"consumed-permits"
numbers = [int(p.stem.split("-")[-1]) for p in used.glob("window-*.json")]
window = f"window-{max(numbers, default=0)+1:04d}"
permit = dict(schema_version="rainpulse.nowcastnet-generative-formal-permit/1.0",status="approved",mode="resume",approval_source="user_weekly_schedule_2026-09-10",approved_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),window_id=window,training_code_revision=e["RAINPULSE_GENERATIVE_CODE_REVISION"],profile_sha256=e["RAINPULSE_GENERATIVE_PROFILE_SHA256"],evolution_checkpoint_sha256=e["RAINPULSE_GENERATIVE_PARENT_SHA256"],checkpoint_global_step=latest["global_step"],checkpoint_sha256=latest["sha256"],target_global_step=int(e["RAINPULSE_GENERATIVE_TARGET_STEP"]),independent_holdout_opened=False)
p = pathlib.Path(e["RAINPULSE_GENERATIVE_PERMIT_FILE"])
fd,tmp = tempfile.mkstemp(dir=p.parent,prefix=".weekly-permit-")
with os.fdopen(fd,"w") as f: json.dump(permit,f)
os.replace(tmp,p)
print(window, latest["global_step"], flush=True)
subprocess.run(["systemctl","--user","start",unit],check=True)
'''
(lib / 'rainpulse-training-weekly.py').write_text(launcher)
(lib / 'rainpulse-training-weekly.py').chmod(0o700)
(units / 'rainpulse-training-weekly.service').write_text('''[Unit]
Description=RainPulse authorized weekly checkpoint continuation
[Service]
Type=oneshot
EnvironmentFile=%h/.config/rainpulse/nowcastnet-generative-formal.env
ExecStart=/usr/bin/python3 %h/.local/libexec/rainpulse-training-weekly.py
TimeoutStartSec=6min
UMask=0077
''')
(units / 'rainpulse-training-weekly.timer').write_text('''[Unit]
Description=RainPulse weekday nights and weekend start
[Timer]
OnCalendar=Mon..Fri *-*-* 20:00:00 Asia/Taipei
OnCalendar=Sat,Sun *-*-* 00:00:00 Asia/Taipei
OnBootSec=2min
Persistent=true
AccuracySec=1s
[Install]
WantedBy=timers.target
''')
(units / 'rainpulse-training-weekly-stop.timer').write_text('''[Unit]
Description=RainPulse weekday 08:00 checkpoint stop and Qwen recovery
[Timer]
OnCalendar=Mon..Fri *-*-* 08:00:00 Asia/Taipei
Unit=rainpulse-nowcastnet-generative-stop.service
Persistent=false
AccuracySec=1s
[Install]
WantedBy=timers.target
''')
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
subprocess.run(['systemctl','--user','enable','--now','rainpulse-training-weekly.timer','rainpulse-training-weekly-stop.timer'],check=True)
