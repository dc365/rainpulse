#!/usr/bin/env python3
"""Raw-only replay of the legacy multisweep NPZ export; NEVER business QC replay.

The old export lacks gate qualification, moments, times and binding receipts.
Do not silently fill them from another case, from filenames, or from PNGs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"algorithms/rainpulse_algo/radar/qc_engine"))
from volume_review.config import VolumeReviewConfig
from volume_review.data import Sweep, json_bytes
from volume_review.engine import evaluate


def read_legacy(directory):
    directory=Path(directory);manifest=json.loads((directory/"manifest.json").read_text())
    sweeps=[];sources=[];seen=set()
    for rec in manifest["sweeps"]:
        relative=Path(rec["file"])
        if relative.is_absolute() or len(relative.parts)!=1 or relative.suffix!=".npz":raise ValueError("unsafe case path")
        number=int(rec["sweep_number"])
        if number in seen:raise ValueError("duplicate sweep identity")
        seen.add(number);path=directory/relative
        with np.load(path,allow_pickle=False) as n:
            raw={k:n[k] for k in n.files}
        az=np.asarray(raw["azimuth_deg"],float)%360;order=np.argsort(az,kind="stable");az=az[order]
        if raw["DBZH_RAW"].shape!=(len(az),len(raw["range_m"])):raise ValueError("legacy raw geometry differs")
        gap=(np.roll(az,-1)-az)%360;dups=gap<=.01
        positive=gap[~dups];nominal=float(np.median(positive)) if positive.size else 360.
        gaps=gap>nominal*1.8;good=~(dups|np.roll(dups,1))
        fields={k[:-4]:v[order] for k,v in raw.items() if k in ("DBZH_RAW","SNR_RAW","RHOHV_RAW","ZDR_RAW","PHIDP_RAW","VR_RAW","SW_RAW")}
        available={k:np.isfinite(v)&good[:,None] for k,v in fields.items()}
        # Only an explicitly provided relative-seconds field is accepted; no invented timing.
        times=raw.get("ray_time_s");times=None if times is None else times[order]
        s=Sweep(f"sweep_{number:03d}",az,raw["elevation_deg"][order],raw["range_m"],fields,available,good,gaps,times)
        sweeps.append(s)
        sources.append({"file":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"raw_digest":s.digest,
                        "excluded_duplicate_rays":int((~good).sum()),"loaded_fields":sorted(fields)})
    return sweeps,{"case":manifest.get("case"),"nominal_time":manifest.get("nominal_time"),"manifest_sha256":hashlib.sha256((directory/"manifest.json").read_bytes()).hexdigest(),"inputs":sources}


def run(directory,phases=(1,2,3)):
    sweeps,source=read_legacy(directory);runs=[]
    for phase in phases:
        e=evaluate(sweeps,VolumeReviewConfig(phase=phase,mode="audit"))
        runs.append({"summary":e.summary,"objects":e.objects,"links":e.links,"reference_models":e.models})
    return {"schema":"rainpulse.legacy-multisweep-raw-audit-v1","source":source,
            "binding_status":"UNBOUND_INCOMPLETE_LEGACY_EXPORT", "business_masks_reconstructed":False,
            "png_used_for_labels":False,"real_cr_reconstructed":False,"deployment":False,
            "limitation":"No action/trust/QPE/flag definitions or source receipts. Counts are candidates, not confirmed interference or accuracy.","runs":runs}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--case",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    if a.out.exists():raise ValueError("output file exists")
    report=run(a.case);a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_bytes(json_bytes(report))
    print(json.dumps({"status":report["binding_status"],"sweeps":len(report["source"]["inputs"]),"phases":[r["summary"]["phase"] for r in report["runs"]]},indent=2))

if __name__=="__main__":main()
