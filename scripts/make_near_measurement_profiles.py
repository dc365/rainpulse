"""Generate opt-in children from a real P3 parent; never overwrite live configs."""
import argparse
from pathlib import Path
import json
import hashlib
import os
import sys
import tempfile
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "algorithms/rainpulse_algo/radar/qc_engine"))
from volume_review.config import VolumeReviewConfig
from volume_review.near_measurement.config import NearMeasurementConfig


def generate(parent_path, output, *, backend="wradlib", pyart_check=False):
    parent_path, output = Path(parent_path), Path(output)
    parent_bytes = parent_path.read_bytes(); parent = yaml.safe_load(parent_bytes)
    if not isinstance(parent, dict) or parent.get("operational_eligible") is not False:
        raise ValueError("explicit non-operational parent required")
    if not isinstance(parent.get("profile_version"), str) or not parent["profile_version"]:
        raise ValueError("parent profile identity missing")
    v = parent.get("volume_review")
    if not isinstance(v, dict) or v.get("near_measurement") is not None:
        raise ValueError("use a real P3 parent without near_measurement")
    vc = VolumeReviewConfig.model_validate(v)
    if vc.phase != 3: raise ValueError("P3 parent required")
    echo = float(parent.get("echo", {}).get("no_rain_below_dbz", -10.))
    variants = [("audit", "audit", "cr_withhold", "cr_withhold", 8.),
                ("nonmet-cr", "experiment", "cr_withhold", "diagnostic_only", 8.),
                *[(f"strict-cr-snr{int(t)}", "experiment", "cr_withhold", "cr_withhold", t) for t in (3., 6., 8., 10.)],
                ("nonmet-quarantine", "experiment", "quarantine", "diagnostic_only", 8.),
                ("strict-cr-snr8-nonmet-quarantine", "experiment", "quarantine", "cr_withhold", 8.)]
    if output.exists(): raise ValueError("output directory exists; refusing to mix profile generations")
    output.parent.mkdir(parents=True, exist_ok=True)
    records=[]
    with tempfile.TemporaryDirectory(prefix=".near-profile-", dir=output.parent) as td:
        tmp = Path(td)
        for name, mode, npolicy, upolicy, t in variants:
            child = json.loads(json.dumps(parent))
            cfg = NearMeasurementConfig(mode=mode, nonmet_policy=npolicy, uncertainty_policy=upolicy,
                    uncertainty_snr_db=t, depolarization_backend=backend,
                    gatefilter_check="pyart" if pyart_check else "disabled", no_rain_below_dbz=echo)
            child["profile_version"] += "-nmr-v1-" + name
            if mode == "experiment": child["volume_review"]["mode"] = "experiment_quarantine"
            child["volume_review"]["near_measurement"] = cfg.model_dump(mode="json")
            VolumeReviewConfig.model_validate(child["volume_review"])
            payload = yaml.safe_dump(child, sort_keys=False, allow_unicode=True).encode()
            filename = "near-measurement-" + name + ".yaml"; (tmp/filename).write_bytes(payload)
            records.append({"file": filename, "sha256": hashlib.sha256(payload).hexdigest(),
                            "near_config_sha256": cfg.digest})
        (tmp/"generation.json").write_text(json.dumps({"source_parent_sha256": hashlib.sha256(parent_bytes).hexdigest(),
            "profiles": records, "live_configuration_modified": False}, indent=2))
        # Complete set appears together. Refuse a concurrent target, do not replace.
        if output.exists(): raise ValueError("output appeared concurrently")
        # TemporaryDirectory starts at 0700; workers and the control service
        # must be able to traverse the published configuration directory.
        tmp.chmod(0o755)
        os.rename(tmp, output)
    return records


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--parent", required=True, type=Path);p.add_argument("--output", required=True, type=Path)
    p.add_argument("--backend", choices=("wradlib", "numpy_reference"), default="wradlib")
    p.add_argument("--pyart-check", action="store_true")
    a=p.parse_args();print(json.dumps(generate(a.parent,a.output,backend=a.backend,pyart_check=a.pyart_check),indent=2))
