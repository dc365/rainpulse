#!/usr/bin/env python3
"""Generate separate audit/CR/quarantine children; never select or deploy them."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
import yaml
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"algorithms/rainpulse_algo/radar/qc_engine"))
from volume_review.config import VolumeReviewConfig
from volume_review.clutter_fusion.config import ClutterFusionConfig
from volume_review.episode_background.config import EpisodeConfig


def generate(parent_path,output,*,backend="wradlib",background_config=None,pyart_check=False):
    parent_path,output=Path(parent_path),Path(output)
    payload=parent_path.read_bytes();parent=yaml.safe_load(payload)
    if not isinstance(parent,dict) or parent.get("operational_eligible") is not False or not parent.get("profile_version"):
        raise ValueError("explicit versioned non-operational parent required")
    if parent.get("engine") != "open_source" or not isinstance(parent.get("volume_review"), dict):
        raise ValueError("existing open-source P3 parent required")
    pc=VolumeReviewConfig.model_validate(parent["volume_review"])
    if pc.phase!=3 or pc.mode!="experiment_quarantine" or pc.clutter_fusion is not None:
        raise ValueError("frozen experimental P3 parent without fusion required; do not silently change parent actions")
    bg_data=json.loads(Path(background_config).read_text()) if background_config else {}
    echo=float(parent.get("echo",{}).get("no_rain_below_dbz",-10.))
    bg_data.setdefault("no_rain_below_dbz",echo)
    bg=EpisodeConfig.model_validate(bg_data)
    if output.exists():raise FileExistsError(output)
    output.parent.mkdir(parents=True,exist_ok=True);records=[]
    variants=(("audit","audit","retain"),("cr","cr_withhold","retain"),
              ("quarantine","quarantine","retain"),("local-conflict-cr","cr_withhold","cr_withhold"))
    with tempfile.TemporaryDirectory(prefix=".fusion-config-",dir=output.parent) as td:
        tmp=Path(td)
        for name,mode,local in variants:
            child=copy.deepcopy(parent)
            c=ClutterFusionConfig(mode=mode,background=bg,no_rain_below_dbz=echo,
                depolarization_backend=backend,gatefilter_check="pyart" if pyart_check else "disabled",local_conflict_policy=local)
            child["profile_version"]+="-clutter-fusion-v1-"+name
            child["volume_review"]["clutter_fusion"]=c.model_dump(mode="json")
            VolumeReviewConfig.model_validate(child["volume_review"])
            data=yaml.safe_dump(child,sort_keys=False,allow_unicode=True).encode()
            filename="clutter-fusion-"+name+".yaml";(tmp/filename).write_bytes(data)
            records.append({"file":filename,"sha256":hashlib.sha256(data).hexdigest(),"fusion_sha256":c.digest})
        (tmp/"generation.json").write_text(json.dumps({"source_parent_sha256":hashlib.sha256(payload).hexdigest(),
            "profiles":records,"radial_config_unchanged":True,"no_live_selection":True},indent=2))
        if output.exists():raise FileExistsError(output)
        tmp.chmod(0o755);os.rename(tmp,output)
    return records


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--parent",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path);p.add_argument("--backend",choices=("wradlib","numpy_reference"),default="wradlib")
    p.add_argument("--background-config",type=Path);p.add_argument("--pyart-check",action="store_true")
    a=p.parse_args();print(json.dumps(generate(a.parent,a.output,backend=a.backend,background_config=a.background_config,pyart_check=a.pyart_check),indent=2))
