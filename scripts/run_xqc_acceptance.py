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
    parser.add_argument("--mode", choices=("audit", "pilot", "raw"), default="audit")
    parser.add_argument("--failed-pilot", type=Path)
    parser.add_argument("--raw-inventory", type=Path)
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument("--configs", type=Path)
    parser.add_argument("--gate-script", type=Path)
    parser.add_argument("--raw-image")
    parser.add_argument("--limit-files", type=int)
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
    image_id = frozen["image_id"]
    raw_args = []
    raw_mounts = []
    if args.mode == "raw":
        if not all((args.raw_inventory, args.raw_root, args.configs, args.gate_script, args.raw_image)):
            raise ValueError("raw mode requires frozen inventory, read-only source/configs, gate script and decoder image")
        raw_state = json.loads((args.raw_inventory / "state.json").read_text())
        if raw_state["status"] != "FROZEN":
            raise ValueError("raw identity inventory has not completed")
        image_id = json.loads(subprocess.check_output(["docker", "image", "inspect", args.raw_image]))[0]["Id"]
        raw_mounts = ["-v", str(args.raw_root.resolve()) + ":/inputs:ro",
                      "-v", str(args.configs.resolve()) + ":/configs:ro",
                      "-v", str((args.raw_inventory / "raw-files.jsonl").resolve()) + ":/opt/raw-files.jsonl:ro",
                      "-v", str(args.gate_script.resolve()) + ":/opt/xqc_acceptance.py:ro"]
        identities = {p.stem: hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in args.configs.glob("*.yaml")}
        identities_path = folder.resolve() / "decoder-config-identities.json"
        with identities_path.open("x") as target:
            json.dump(identities, target, sort_keys=True)
        raw_mounts += ["-v", str(identities_path) + ":/opt/decoder-config-identities.json:ro"]
        raw_args = ["--config-identities", "/opt/decoder-config-identities.json", "--raw-root", "/inputs", "--raw-records", "/opt/raw-files.jsonl", "--configs", "/configs",
                    "--raw-sha256", raw_state["manifest_sha256"]]
        if args.limit_files is not None:
            raw_args += ["--limit-files", str(args.limit_files)]
    failed_scans = set()
    if args.failed_pilot:
        for evidence in args.failed_pilot.glob("audit-*.jsonl"):
            failed_scans.update(row["scan_id"] for row in
                                (json.loads(line) for line in evidence.read_text().splitlines())
                                if row.get("failures") == ["PILOT_ERROR"])
        if args.mode != "pilot" or not failed_scans:
            raise ValueError("failed-pilot requires explicit pilot errors to repair")
    lock = threading.Lock()
    state = {"status": "RUNNING", "pid": os.getpid(), "manifest_sha256": frozen["manifest_sha256"],
             "audit_script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(), "mode": args.mode,
             "compute_image_id": image_id,
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
        command = ["docker", "run", "--rm", "--name", name, "--cpus", "1", "--memory", "6g" if args.mode == "raw" else "3g",
                   "--network", next(iter(c["NetworkSettings"]["Networks"])),
                   "--read-only", "--tmpfs", "/tmp:rw,size=64m", "-i",
                   "-v", str(script) + ":/opt/compute.py:ro", "--entrypoint", "python"]
        # Audit/pilot import their own gate helpers; raw compute uses the same
        # separate helper module for all acceptance predicates.
        if args.mode != "raw":
            command += ["-v", str(script) + ":/opt/xqc_acceptance.py:ro"]
        command += raw_mounts
        for key in ("RAINPULSE_OBJECT_STORE_ENDPOINT", "RAINPULSE_OBJECT_STORE_ACCESS_KEY",
                    "RAINPULSE_OBJECT_STORE_SECRET_KEY"):
            command += ["-e", key]
        command += [image_id, "/opt/compute.py", args.mode, *raw_args]
        for station in selected:
            command += ["--station", station]
        for scan in sorted(failed_scans):
            command += ["--scan-id", scan]
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
