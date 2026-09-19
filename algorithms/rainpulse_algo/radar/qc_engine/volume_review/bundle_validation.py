"""Independent P0 replay of bound numerical snapshots and actual PNG bytes."""
import hashlib
import json
from pathlib import PurePosixPath
import numpy as np
from .data import array_digest
from .receipts import load_npz, verify_binding


def resource(objects,key):
    p=PurePosixPath(key)
    if p.is_absolute() or ".." in p.parts or key not in objects:raise ValueError("missing/unsafe artifact path "+key)
    return bytes(objects[key])


def verify_qc_evidence(objects):
    manifest=json.loads(resource(objects,"qc/volume_review/manifest.json"))
    if manifest.get("schema")!="rainpulse.bound-volume-evidence-v1" or not manifest.get("flag_definitions"):
        raise ValueError("bound volume identity or flag definitions missing")
    seen=set();snapshots={}
    for rec in manifest["sweeps"]:
        if rec["sweep"] in seen:raise ValueError("duplicate bound sweep")
        seen.add(rec["sweep"]);data=resource(objects,rec["path"])
        if hashlib.sha256(data).hexdigest()!=rec["sha256"]:raise ValueError("QC snapshot bytes changed")
        a=load_npz(data)
        if array_digest(a)!=rec["numeric_sha256"] or sorted(a)!=rec["fields"]:raise ValueError("QC snapshot numerical identity changed")
        required=("DBZH_RAW","DBZH_QC","QC_ACTION","VALID_MASK","REFLECTIVITY_TRUST_MASK","QPE_ELIGIBLE_MASK","QC_FLAGS")
        if any(k not in a for k in required):raise ValueError("bound QC is missing qualification fields")
        if not np.array_equal(a["QC_ACTION"]==3,a["VALID_MASK"]==0):raise ValueError("bound action altered missing support")
        snapshots[rec["sweep"]]=a
    if not snapshots:raise ValueError("empty bound QC volume")
    return manifest,snapshots


def verify_diagnostic_bindings(qc_objects,diagnostic_objects):
    qc,snapshots=verify_qc_evidence(qc_objects)
    manifest=json.loads(resource(diagnostic_objects,"manifest.json"));count=0
    for layer in manifest["layers"]:
        if "bound_receipt_path" not in layer:continue
        receipt=json.loads(resource(diagnostic_objects,layer["bound_receipt_path"]))
        identity=receipt["identity"]
        # Other participating volumes may have their own explicitly bound snapshots.
        if identity["volume_id"]!=str(qc["scan_id"]):continue
        if identity["config_sha256"]!=qc["config_sha256"]:raise ValueError("rendered QC configuration differs")
        payload=resource(diagnostic_objects,layer["bound_source_path"])
        png=resource(diagnostic_objects,layer["object_path"])
        verify_binding(payload,png,receipt)
        arrays=load_npz(payload);key=f"sweep_{int(identity['sweep_id']):03d}"
        if key not in snapshots:raise ValueError("image sweep not present in bound QC")
        for field,value in snapshots[key].items():
            if field not in arrays or not np.array_equal(value,arrays[field],equal_nan=True):raise ValueError("PNG bound to a different numeric sweep: "+field)
        count+=1
    if not count:raise ValueError("no PNG bound to this QC volume")
    return {"status":"VERIFIED_NUMERIC_PNG_BINDING","layers":count,"scientific_acceptance":False}
