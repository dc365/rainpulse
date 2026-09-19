#!/usr/bin/env python3
"""Generate new child profiles only; no live selection or edits to the parent."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import yaml


def children(parent, *, retire_direct_shapes=False):
    if parent.get("engine")!="open_source" or parent.get("pipeline_version")!="qc-opensource-7.3.6" or parent.get("operational_eligible") is not False:
        raise ValueError("an explicit non-operational 7.3.6 parent is required")
    if parent.get("volume_review") is not None:raise ValueError("use the frozen parent, not a volume-review child")
    if not isinstance(parent.get("profile_version"),str) or not parent["profile_version"]:raise ValueError("parent profile_version required")
    for phase,modes in ((0,("audit",)),(1,("audit",)),(2,("audit","experiment_quarantine")),(3,("audit","experiment_quarantine"))):
        for mode in modes:
            child=deepcopy(parent)
            child["profile_version"]+=f"-volume-20260919-p{phase}-{mode}"+("-no-direct-shapes" if retire_direct_shapes else "")
            child["volume_review"]={"version":"volume-object-review-20260919-v1","phase":phase,"mode":mode,"operational_eligible":False}
            if retire_direct_shapes:
                line=child
                for key in ("generalization", "broad_source", "source_review", "radial_revision"):
                    line=line.get(key) or {}
                line=line.get("fragment_line")
                if line:
                    for key in ("morphology_quarantine_enabled","isolated_quarantine_enabled","sparse_isolated_enabled","group_morphology_enabled","window_tracks_enabled","power_fan_enabled"):
                        if key in line:line[key]=False
            yield phase,mode,child


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--parent",type=Path,required=True);p.add_argument("--out",type=Path,required=True)
    p.add_argument("--retire-direct-shapes",action="store_true",help="Separate ablation family: disable explicit legacy direct-shape switches; never revive old products")
    p.add_argument("--diagnostics-parent",type=Path,help="Optional frozen diagnostic parent: generate explicit all-DBZH-sweeps/full-range-CR child")
    a=p.parse_args();raw=a.parent.read_bytes();parent=yaml.safe_load(raw);items=list(children(parent,retire_direct_shapes=a.retire_direct_shapes))
    diagnostic=None
    if a.diagnostics_parent:
        diagnostic=yaml.safe_load(a.diagnostics_parent.read_bytes())
        if not isinstance(diagnostic.get("polar_render"),dict) or not isinstance(diagnostic.get("grid_render"),dict):
            raise ValueError("diagnostic parent must declare polar_render and grid_render")
        diagnostic["profile_version"]+="-volume-20260919-all-sweeps"
        diagnostic["polar_render"]["sweep_selection"]="all_dbzh_sweeps"
        diagnostic["grid_render"]["full_range_reflectivity"]=True
    if a.out.exists():raise ValueError("output directory must be new")
    a.out.mkdir(parents=True)
    receipt={"parent_sha256":hashlib.sha256(raw).hexdigest(),"parent_profile_version":parent["profile_version"],"retire_direct_shapes":a.retire_direct_shapes,"children":[]}
    for phase,mode,child in items:
        path=a.out/f"volume-p{phase}-{mode}.yaml";data=yaml.safe_dump(child,sort_keys=False,allow_unicode=True).encode();path.write_bytes(data)
        receipt["children"].append({"path":path.name,"sha256":hashlib.sha256(data).hexdigest()})
    if diagnostic is not None:
        path=a.out/"diagnostic-volume-all-sweeps.yaml"
        data=yaml.safe_dump(diagnostic,sort_keys=False,allow_unicode=True).encode();path.write_bytes(data)
        receipt["diagnostics"]={"parent_sha256":hashlib.sha256(a.diagnostics_parent.read_bytes()).hexdigest(),"path":path.name,"sha256":hashlib.sha256(data).hexdigest()}
    (a.out/"lineage.json").write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    print(json.dumps(receipt,ensure_ascii=False,indent=2))

if __name__=="__main__":main()
