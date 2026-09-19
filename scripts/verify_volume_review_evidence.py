#!/usr/bin/env python3
"""Verify a local QC object directory and its diagnostic PNG receipts. Read-only."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"algorithms/rainpulse_algo/radar/qc_engine"))
from volume_review.bundle_validation import verify_qc_evidence,verify_diagnostic_bindings


def objects(root):
    return {p.relative_to(root).as_posix():p.read_bytes() for p in root.rglob("*") if p.is_file() and not p.is_symlink()}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--qc-root",type=Path,required=True);p.add_argument("--diagnostics-root",type=Path)
    a=p.parse_args();qc=objects(a.qc_root)
    if a.diagnostics_root:report=verify_diagnostic_bindings(qc,objects(a.diagnostics_root))
    else:
        manifest,sweeps=verify_qc_evidence(qc);report={"status":"VERIFIED_NUMERIC_SNAPSHOTS","sweeps":len(sweeps),"png_verified":False}
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
