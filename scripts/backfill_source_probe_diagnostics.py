"""Enqueue versioned diagnostics for the frozen 2026-08-28 history on host 105.

Run with the service's mount namespace and environment. Existing jobs are
idempotent; raw/QC inputs and old diagnostic assets remain unchanged.
"""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / 'deploy/diagnostic-source-probe-20260927.yaml'
BINARY = ROOT / '.build/sx-probes-b91b81b/orchestrator'
QUERY = """SELECT analysis_id FROM analysis_cycles
WHERE grid_id='fuzhou_118_123_25_27_0p01deg_v1'
AND analysis_time >= '2026-08-28T00:00:00Z'
AND analysis_time < '2026-08-29T00:00:00Z'
AND status='ANALYSIS_READY' ORDER BY analysis_time"""


def main():
    pid = subprocess.check_output(
        ['systemctl', 'show', 'rainpulse', '-p', 'MainPID', '--value'], text=True
    ).strip()
    environment = dict(os.environ)
    for item in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0'):
        if b'=' in item:
            key, value = item.split(b'=', 1)
            environment[key.decode()] = value.decode()
    ids = subprocess.check_output(
        ['docker', 'exec', 'rainpulse-postgres-1', 'psql', '-U', 'rainpulse',
         '-d', 'rainpulse', '-Atc', QUERY], text=True
    ).splitlines()
    if not ids or len(ids) > 240:
        raise RuntimeError('unexpected frozen history size')
    records = []
    for identity in ids:
        result = subprocess.run(
            ['nsenter', '-t', pid, '-m', '--', str(BINARY),
             'analysis-diagnostics', identity, str(PROFILE)],
            env=environment, cwd=ROOT, capture_output=True, text=True,
        )
        if result.returncode:
            raise RuntimeError(f'diagnostic request failed for {identity}; exit {result.returncode}')
        record = next(json.loads(line) for line in result.stdout.splitlines()
                      if line.startswith('{"analysis_id"'))
        records.append(record)
        (ROOT / '.build/sx-probes-b91b81b/backfill-requests.json').write_text(
            json.dumps(records, indent=2) + '\n'
        )
    print(json.dumps({'enqueued_or_existing': len(records)}))


if __name__ == '__main__':
    main()
