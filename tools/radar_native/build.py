#!/usr/bin/env python3
"""Build the included original C cores offline; verify every vendored source."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--cc", default="gcc")
    a = p.parse_args()
    root = Path(__file__).resolve().parents[2]
    sources = root / "third_party/radar_native"
    lock = json.loads((sources / "sources.lock.json").read_text())
    for name, sha in lock["files"].items():
        file = sources / name
        if file.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest() != sha:
            raise ValueError("source checksum mismatch: " + name)
    out = a.output.absolute()
    if out.exists() or out.is_symlink() or out.with_name(out.name + ".json").exists():
        raise ValueError("binary exists; use a new path")
    out.parent.mkdir(parents=True, exist_ok=True)
    b = sources / ("bropo-" + lock["bropo_revision"]) / "ropo"
    r = sources / ("rave-" + lock["rave_revision"]) / "librave/toolbox"
    driver = Path(__file__).with_name("emitter_driver.c")
    inputs = [
        driver,
        *sorted(b.glob("fmi_image*.c")),
        b / "fmi_meteosat.c",
        b / "fmi_radar_image.c",
        b / "fmi_util.c",
        r / "rave_debug.c",
        r / "rave_types.c",
    ]
    for source in inputs[1:]:
        if source.relative_to(sources).as_posix() not in lock["files"]:
            raise ValueError("unlocked source would be compiled")
    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        target = Path(tmp) / "emitter-core"
        command = [
            a.cc,
            "-O2",
            "-std=gnu99",
            "-ffunction-sections",
            "-fdata-sections",
            "-I" + str(b),
            "-I" + str(r),
            *map(str, inputs),
            "-Wl,--gc-sections",
            "-lm",
            "-o",
            str(target),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr)
        receipt = {
            "schema_version": "rainpulse.native-build.v1",
            "source_lock_sha256": hashlib.sha256(
                (sources / "sources.lock.json").read_bytes()
            ).hexdigest(),
            "driver_sha256": hashlib.sha256(driver.read_bytes()).hexdigest(),
            "binary_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "compiler": subprocess.check_output(
                [a.cc, "--version"], text=True
            ).splitlines()[0],
            "detectors": ["detect_emitters", "detect_emitters2"],
            "adaptation": "process_isolated_core_not_full_RAVE_ODIM",
            "source_revisions": {k: v for k, v in lock.items() if k != "files"},
            "warnings": result.stderr,
        }
        os.link(target, out)  # atomic no-clobber, same filesystem
    receipt_path = out.with_name(out.name + ".json")
    with receipt_path.open("x") as f:
        json.dump(receipt, f, indent=2)
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
