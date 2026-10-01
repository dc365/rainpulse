#!/usr/bin/env python3
"""Four bounded read-only product auditors; persist handles and failure matrix."""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import os
from pathlib import Path
import subprocess
import threading
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest")
    parser.add_argument("audit_script")
    parser.add_argument("output")
    parser.add_argument("--mode", choices=("audit", "pilot"), default="audit")
    args = parser.parse_args()
    manifest = Path(args.manifest).resolve()
    script = Path(args.audit_script).resolve()
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    c = json.loads(subprocess.check_output(["docker", "inspect", "rainpulse-ops-multiband-worker-1"]))[0]
    frozen = json.loads(manifest.read_text())
    if c["Image"] != frozen["image_id"]:
        raise ValueError("worker image changed after scope freeze")
    environment = dict(os.environ)
    environment.update(item.split("=", 1) for item in c["Config"]["Env"] if "=" in item)
    stations = sorted({s["radar_id"] for s in frozen["scans"]})
    groups = [stations[i::4] for i in range(4)]
    lock = threading.Lock()
    state = {"status": "RUNNING", "pid": os.getpid(), "manifest_sha256": frozen["manifest_sha256"],
             "audit_script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(), "mode": args.mode,
             "started_at": datetime.now(timezone.utc).isoformat(), "workers": {},
             "counts": {}, "meteorological_acceptance": "NOT_COMPLETED"}
    counters = collections.Counter()

    def persist():
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        state["counts"] = dict(counters)
        temp = folder / "state.json.tmp"
        temp.write_text(json.dumps(state, sort_keys=True))
        temp.replace(folder / "state.json")

    with lock:
        persist()

    def run(index, selected):
        name = "rainpulse-xqc-" + args.mode + "-" + frozen["manifest_sha256"][:8] + "-" + str(index)
        command = ["docker", "run", "--rm", "--name", name, "--cpus", "1", "--memory", "3g",
                   "--network", next(iter(c["NetworkSettings"]["Networks"])),
                   "--read-only", "--tmpfs", "/tmp:rw,size=64m", "-i",
                   "-v", str(script) + ":/opt/xqc_acceptance.py:ro", "--entrypoint", "python"]
        for key in ("RAINPULSE_OBJECT_STORE_ENDPOINT", "RAINPULSE_OBJECT_STORE_ACCESS_KEY",
                    "RAINPULSE_OBJECT_STORE_SECRET_KEY"):
            command += ["-e", key]
        command += [frozen["image_id"], "/opt/xqc_acceptance.py", args.mode]
        for station in selected:
            command += ["--station", station]
        output = folder / ("audit-" + str(index) + ".jsonl")
        if output.exists():
            raise ValueError("refusing to overwrite audit evidence")
        with manifest.open("rb") as source, output.open("x") as target:
            process = subprocess.Popen(command, env=environment, stdin=source, stdout=subprocess.PIPE,
                                       stderr=(folder / ("stderr-" + str(index) + ".log")).open("x"), text=True)
            with lock:
                state["workers"][str(index)] = {"pid": process.pid, "container": name,
                                                "stations": selected, "status": "RUNNING"}
                persist()
            for line in process.stdout:
                target.write(line)
                target.flush()
                result = json.loads(line)
                with lock:
                    counters[result["state"]] += 1
                    state["workers"][str(index)]["last_scan"] = result["scan_id"]
                    persist()
            code = process.wait()
            with lock:
                state["workers"][str(index)].update(status="COMPLETE" if code == 0 else "PROCESS_FAILED",
                                                     exit_code=code)
                persist()
            return code

    with ThreadPoolExecutor(max_workers=4) as executor:
        codes = list(executor.map(lambda item: run(*item), enumerate(groups)))
    with lock:
        state["status"] = "SURVEY_COMPLETE" if not any(codes) else "SURVEY_PROCESS_FAILED"
        state["finished_at"] = datetime.now(timezone.utc).isoformat()
        persist()


if __name__ == "__main__":
    main()
