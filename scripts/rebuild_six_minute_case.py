#!/usr/bin/env python3
"""Six-minute historical case rebuild: analysis and forecast stages.

Stage 1 (radar grid) lives in scripts/rebuild_six_minute_scans.sh.  This script
takes the already grid-ready six-minute slots of one case date and drives

    mosaic -> QPE -> diagnostics -> NowcastInput -> pySTEPS-LK -> products

through explicit control-plane requests.  It never rewrites the legacy
five-minute analyses and needs no historical replay window, so the old case
stays readable until the new six-minute chain is published.  Every request is
idempotent: re-running the script resumes where it stopped.

Environment:
  REBUILD_DATE_UTC    case date (default 2026-08-28)
  REBUILD_BINARY      control binary with orchestrator subcommands
  REBUILD_DRY_RUN     set 1 to print requests without sending them
  REBUILD_POLL_SECONDS  progress poll interval (default 10)
"""
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATE = os.environ.get("REBUILD_DATE_UTC", "2026-08-28")
DRY = os.environ.get("REBUILD_DRY_RUN") == "1"
BINARY = Path(os.environ.get("REBUILD_BINARY", ROOT / ".build/linux-amd64/rainpulse-orchestrator-6m"))
POLL = float(os.environ.get("REBUILD_POLL_SECONDS", "10"))


def log(message):
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), message, flush=True)


def service_env():
    """Read the running service environment: the CLI needs the same contracts."""
    pid = subprocess.check_output(
        ["systemctl", "show", "rainpulse", "-p", "MainPID", "--value"], text=True
    ).strip()
    if not pid:
        sys.exit("rainpulse is not running")
    environment = dict(os.environ)
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            environment[key.decode()] = value.decode()
    return environment


ENV = service_env()
DB = {
    "host": ENV.get("RAINPULSE_DATABASE_HOST", "127.0.0.1"),
    "port": ENV.get("RAINPULSE_DATABASE_PORT", "5432"),
    "name": ENV.get("RAINPULSE_DATABASE_NAME", "rainpulse"),
    "password": ENV.get("RAINPULSE_DATABASE_PASSWORD", ""),
}


def host_path(path):
    """/opt/rainpulse/configs is the service bind mount of this repository."""
    prefix = "/opt/rainpulse/"
    return str(ROOT / path[len(prefix):]) if path.startswith(prefix) else path


def config(name, fallback):
    return host_path(ENV.get(name) or fallback)


CONFIGS = {
    "mosaic": config("RAINPULSE_PIPELINE_MOSAIC_CONFIG", "/opt/rainpulse/configs/mosaic/qc-opensource-mosaic-v1.yaml"),
    "qpe": config("RAINPULSE_PIPELINE_QPE_CONFIG", "/opt/rainpulse/configs/qpe/qc-opensource-zr-v1.yaml"),
    "diagnostics": config("RAINPULSE_PIPELINE_DIAGNOSTIC_CONFIG", "/opt/rainpulse/configs/diagnostics/qc-residual-diagnostics-v6.yaml"),
    "nowcast": config("RAINPULSE_PIPELINE_NOWCAST_INPUT_CONFIG", "/opt/rainpulse/configs/nowcast/rp043-realtime-shadow-6min-v2.yaml"),
    "lk": config("RAINPULSE_PIPELINE_PYSTEPS_CONFIG", "/opt/rainpulse/configs/nowcast/prelaunch-pysteps-lk-v2.yaml"),
    "products": config("RAINPULSE_PIPELINE_PRODUCT_CONFIG", "/opt/rainpulse/configs/products/rp015-application-products-v1.yaml"),
}
for label, path in CONFIGS.items():
    if not Path(path).is_file():
        sys.exit(f"missing readable {label} config: {path}")


def profile_version(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.startswith("profile_version:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    sys.exit(f"profile_version missing from {path}")


ANALYSIS_CONFIG = profile_version(CONFIGS["mosaic"])
RUN_CONFIG = profile_version(CONFIGS["nowcast"])
LK_CONFIG = profile_version(CONFIGS["lk"])


def query(sql):
    result = subprocess.run(
        ["psql", "-h", DB["host"], "-p", DB["port"], "-U", "rainpulse", "-d", DB["name"],
         "-At", "-F", "|", "-c", sql],
        env={**ENV, "PGPASSWORD": DB["password"]}, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"psql failed: {result.stderr.strip()}")
    return [line.split("|") for line in result.stdout.splitlines()]


def scalar(sql, default="0"):
    rows = query(sql)
    return rows[0][0] if rows and rows[0] else default


def request(*args):
    if DRY:
        log("dry-run " + " ".join(map(str, args)))
        return True
    result = subprocess.run([str(BINARY), *map(str, args)], env=ENV, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
        log("request failed: " + " ".join(map(str, args)) + " :: " + (detail[-1] if detail else "unknown"))
    return result.returncode == 0


def slots():
    """Nearest grid-ready volume per radar for every six-minute slot."""
    rows = query(f"""
        WITH ready AS (
          SELECT s.scan_id, r.radar_id, s.volume_end_time,
                 to_timestamp(round(extract(epoch from s.volume_end_time) / 360) * 360) AS slot
          FROM radar_scan_runs r JOIN radar_scans s ON s.scan_id = r.scan_id
          WHERE r.status = 'RADAR_GRID_READY'
            AND s.volume_end_time >= '{DATE}' AND s.volume_end_time < '{DATE}'::date + 1)
        SELECT DISTINCT ON (slot, radar_id)
               to_char(slot, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), scan_id::text
        FROM ready
        ORDER BY slot, radar_id,
                 abs(extract(epoch from volume_end_time) - extract(epoch from slot))""")
    grouped = defaultdict(list)
    for slot, scan in rows:
        grouped[slot].append(scan)
    return dict(sorted(grouped.items()))


def stage_mosaic(grouped):
    expected = sum(1 for scans in grouped.values() if len(scans) >= 2)
    log(f"mosaic: {expected} slots from {len(grouped)} buckets")
    for slot, scans in grouped.items():
        if len(scans) < 2:
            continue
        request("analysis-mosaic", slot, CONFIGS["mosaic"], *scans)
    while True:
        ready = int(scalar(f"""SELECT count(*) FROM analysis_cycles
            WHERE config_version = '{ANALYSIS_CONFIG}' AND analysis_time::date = '{DATE}'
              AND status IN ('QPE_RUNNING', 'ANALYSIS_READY')"""))
        log(f"mosaic: {ready}/{expected}")
        if ready >= expected or DRY:
            return expected
        time.sleep(POLL)


def stage_analysis(stage, expected):
    """QPE then diagnostics: re-request whatever is still waiting."""
    requested = set()
    while True:
        if stage == "qpe":
            pending = [row[0] for row in query(f"""SELECT analysis_id FROM analysis_cycles
                WHERE config_version = '{ANALYSIS_CONFIG}' AND analysis_time::date = '{DATE}'
                  AND status = 'QPE_RUNNING'""")]
            for analysis_id in pending:
                request("analysis-qpe", analysis_id, CONFIGS["qpe"])
            done = int(scalar(f"""SELECT count(*) FROM analysis_cycles
                WHERE config_version = '{ANALYSIS_CONFIG}' AND analysis_time::date = '{DATE}'
                  AND status = 'ANALYSIS_READY'"""))
            failed = int(scalar(f"""SELECT count(*) FROM analysis_cycles
                WHERE config_version = '{ANALYSIS_CONFIG}' AND analysis_time::date = '{DATE}'
                  AND status = 'FAILED'"""))
        else:
            pending = [row[0] for row in query(f"""SELECT analysis_id FROM analysis_cycles
                WHERE config_version = '{ANALYSIS_CONFIG}' AND analysis_time::date = '{DATE}'
                  AND status = 'ANALYSIS_READY'""") if row[0] not in requested]
            for analysis_id in pending:
                if request("analysis-diagnostics", analysis_id, CONFIGS["diagnostics"]):
                    requested.add(analysis_id)
            done = int(scalar(f"""SELECT count(*) FROM diagnostic_runs d
                JOIN analysis_cycles a ON a.analysis_id = d.analysis_id
                WHERE a.config_version = '{ANALYSIS_CONFIG}' AND a.analysis_time::date = '{DATE}'"""))
            failed = 0
        log(f"{stage}: done={done} pending={len(pending)} failed={failed}")
        if (not pending and done + failed >= expected) or DRY:
            return
        time.sleep(POLL)


def stage_nowcast(grouped):
    ready_slots = [row[0] for row in query(f"""SELECT DISTINCT to_char(analysis_time, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
        FROM analysis_cycles WHERE config_version = '{ANALYSIS_CONFIG}'
          AND analysis_time::date = '{DATE}' AND status = 'ANALYSIS_READY'
        ORDER BY 1""")]
    log(f"nowcast-input: {len(ready_slots)} candidate slots")
    for slot in ready_slots:
        request("nowcast-input", slot, CONFIGS["nowcast"])
    while True:
        states = query(f"""SELECT status, count(*) FROM forecast_runs
            WHERE config_version = '{RUN_CONFIG}' AND issue_time::date = '{DATE}'
            GROUP BY status ORDER BY status""")
        summary = " ".join(f"{state}={count}" for state, count in states) or "none"
        log(f"nowcast-input: {summary}")
        # Runs stay INPUT_READY until pySTEPS-LK is requested; only the
        # NowcastInput jobs still preprocessing must hold this stage back.
        preprocessing = int(scalar(f"""SELECT count(*) FROM forecast_runs
            WHERE config_version = '{RUN_CONFIG}' AND issue_time::date = '{DATE}'
              AND status = 'PREPROCESSING'"""))
        usable = int(scalar(f"""SELECT count(*) FROM forecast_runs
            WHERE config_version = '{RUN_CONFIG}' AND issue_time::date = '{DATE}'
              AND status NOT IN ('PREPROCESSING', 'FAILED', 'SKIPPED')"""))
        if (preprocessing == 0 and usable > 0) or DRY:
            return
        time.sleep(POLL)


def stage_runs(stage):
    if stage == "lk":
        predicate, status = "INPUT_READY", "BASELINE_READY"
    else:
        predicate, status = "BASELINE_READY", "PUBLISHED"
    requested = set()
    while True:
        pending = [row[0] for row in query(f"""SELECT run_id FROM forecast_runs
            WHERE config_version = '{RUN_CONFIG}' AND issue_time::date = '{DATE}'
              AND status = '{predicate}'""") if row[0] not in requested]
        for run_id in pending:
            command = "pysteps-lk" if stage == "lk" else "product-build"
            config = CONFIGS["lk"] if stage == "lk" else CONFIGS["products"]
            if request(command, run_id, config):
                requested.add(run_id)
        counts = scalar(f"""SELECT count(*) FROM forecast_runs
            WHERE config_version = '{RUN_CONFIG}' AND issue_time::date = '{DATE}'
              AND status IN ('{status}', 'FAILED', 'SKIPPED')""")
        log(f"{stage}: settled={counts} pending={len(pending)}")
        if (not pending and int(counts) >= 1) or DRY:
            return
        time.sleep(POLL)


def main():
    log(f"six-minute case rebuild date={DATE} binary={BINARY} analysis_config={ANALYSIS_CONFIG} run_config={RUN_CONFIG}")
    grouped = slots()
    if not grouped:
        sys.exit(f"no grid-ready six-minute slots for {DATE}")
    expected = stage_mosaic(grouped)
    stage_analysis("qpe", expected)
    stage_analysis("diagnostics", expected)
    stage_nowcast(grouped)
    stage_runs("lk")
    stage_runs("products")
    log("six-minute case rebuild finished; verify the workspace before retiring legacy data")


if __name__ == "__main__":
    main()
