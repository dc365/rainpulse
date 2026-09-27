"""Finish the bounded source-index backfill and remove its three extra replicas."""
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.build/sx-probes-b91b81b'
QUERY = """SELECT status, count(*) FROM jobs
WHERE job_type='analysis.diagnostics'
AND config_version='qc-full-range-source-probe-v16' GROUP BY status"""


def main():
    expected = len(json.loads((STATE / 'backfill-requests.json').read_text()))
    while True:
        lines = subprocess.check_output(
            ['docker', 'exec', 'rainpulse-postgres-1', 'psql', '-U', 'rainpulse',
             '-d', 'rainpulse', '-Atc', QUERY], text=True
        ).splitlines()
        counts = {line.split('|')[0]: int(line.split('|')[1]) for line in lines}
        terminal = sum(counts.get(key, 0) for key in ('SUCCEEDED', 'FAILED', 'SKIPPED'))
        report = {'expected': expected, 'counts': counts, 'complete': counts.get('SUCCEEDED', 0) >= expected, 'settled': terminal >= expected}
        (STATE / 'backfill-status.json').write_text(json.dumps(report, indent=2) + '\n')
        if terminal >= expected:
            # Extra workers may already have taken another request. Wait for
            # diagnostics globally to be idle before graceful scale-down.
            active = subprocess.check_output(
                ['docker', 'exec', 'rainpulse-postgres-1', 'psql', '-U', 'rainpulse',
                 '-d', 'rainpulse', '-Atc', "SELECT count(*) FROM jobs WHERE "
                 "job_type='analysis.diagnostics' AND status='RUNNING'"], text=True
            ).strip()
            if active == '0':
                command = json.loads((STATE / 'backfill-compose-command.json').read_text())
                subprocess.run(command + ['up', '-d', '--no-recreate', '--no-deps',
                                          '--scale', 'analysis-diagnostics-worker=1',
                                          'analysis-diagnostics-worker'], check=True)
                report['replicas_restored'] = 1
                (STATE / 'backfill-status.json').write_text(json.dumps(report, indent=2) + '\n')
                print(json.dumps(report), flush=True)
                return
        time.sleep(30)


if __name__ == '__main__':
    main()
