"""P0: content-bound numeric snapshots and deterministic image evidence."""
from io import BytesIO
import hashlib
import json
import zipfile
import numpy as np
from PIL import Image
from .data import array_digest, json_bytes
from .sampling import polar_pixels


def npz_bytes(arrays):
    out=BytesIO()
    with zipfile.ZipFile(out,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for key in sorted(arrays):
            if not key or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_" for c in key):
                raise ValueError("unsafe NPZ field")
            a=np.asarray(arrays[key]); b=BytesIO()
            if a.dtype.hasobject:
                raise ValueError("object array forbidden")
            np.save(b,a,allow_pickle=False)
            item=zipfile.ZipInfo(key+".npy",date_time=(1980,1,1,0,0,0)); item.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(item,b.getvalue())
    return out.getvalue()


def load_npz(data):
    with np.load(BytesIO(data),allow_pickle=False) as n:
        return {k:n[k] for k in n.files}


def png_bytes(rgba):
    rgba=np.asarray(rgba)
    if rgba.ndim!=3 or rgba.shape[-1]!=4 or rgba.dtype!=np.uint8:
        raise ValueError("uint8 RGBA required")
    out=BytesIO(); Image.fromarray(rgba).save(out,format="PNG",optimize=False)
    return out.getvalue()


def image_binding(arrays, field, valid, polar_rgba, png, identity):
    """Bind a supplied PNG to the exact scalar values, mask AND palette output.

    This proves a single rendering's content lineage, not meteorological truth.
    Numerical palette correctness is additionally verified in the actual renderer.
    """
    required=("volume_id","sweep_id","qc_content_sha256","config_sha256","sampling_version","palette_version")
    if any(not identity.get(k) for k in required):
        raise ValueError("complete image source identity required")
    for k in ("qc_content_sha256","config_sha256"):
        if len(identity[k])!=64 or any(c not in '0123456789abcdef' for c in identity[k]):
            raise ValueError("invalid source hash")
    a={k:np.array(v,copy=True) for k,v in arrays.items()}
    if field not in a or a[field].shape!=np.shape(valid) or np.shape(polar_rgba)!=(*np.shape(valid),4):
        raise ValueError("image source geometry mismatch")
    if not np.isin(valid,(0,1)).all() or np.any(np.asarray(valid,bool)&~np.isfinite(a[field])):
        raise ValueError("image validity has unsupported observations")
    if np.any(np.asarray(polar_rgba)[~np.asarray(valid,bool),3]!=0):
        raise ValueError("invalid scalar pixel is opaque")
    image=np.asarray(Image.open(BytesIO(png)).convert("RGBA"))
    if image.shape[0]!=image.shape[1]:
        raise ValueError("bound PPI must be square")
    rows,gates,available=polar_pixels(a["azimuth"],a["range"],image.shape[0])
    expected=np.asarray(polar_rgba)[rows,gates].copy(); expected[~available]=0
    if not np.array_equal(expected,image):
        raise ValueError("PNG does not reproduce this numeric source and rendering")
    a["RENDER_VALID_MASK"]=np.asarray(valid,"uint8"); a["RENDER_POLAR_RGBA"]=np.asarray(polar_rgba,"uint8")
    snapshot=npz_bytes(a)
    receipt={"schema":"rainpulse.bound-polar-evidence-v1","identity":dict(identity),"field":field,
             "numeric_sha256":array_digest(a),"snapshot_sha256":hashlib.sha256(snapshot).hexdigest(),
             "png_sha256":hashlib.sha256(png).hexdigest(),"image_size":int(image.shape[0]),
             "missing_is_clear_air":False,"observed_support_pixels":int((available&np.asarray(valid,bool)[rows,gates]).sum())}
    return snapshot, receipt


def verify_binding(snapshot,png,receipt):
    if hashlib.sha256(snapshot).hexdigest()!=receipt["snapshot_sha256"] or hashlib.sha256(png).hexdigest()!=receipt["png_sha256"]:
        raise ValueError("bound payload checksum differs")
    a=load_npz(snapshot)
    if array_digest(a)!=receipt["numeric_sha256"]:
        raise ValueError("bound numeric digest differs")
    _,reproduced=image_binding(a,receipt["field"],a["RENDER_VALID_MASK"],a["RENDER_POLAR_RGBA"],png,receipt["identity"])
    if reproduced["numeric_sha256"]!=receipt["numeric_sha256"]:
        raise ValueError("bound numeric replay differs")
    return True


def snapshot_group(group):
    keys=["azimuth","elevation","range","ray_time","DBZH_RAW","DBZH_QC","VALID_MASK","QC_FLAGS", "QC_ACTION",
          "QUALITY_INDEX","REFLECTIVITY_TRUST_MASK","QPE_ELIGIBLE_MASK","RFI_QUARANTINE_MASK","NP_QUARANTINE_MASK",
          "VOR_QUARANTINE_MASK","VOR_STATE","VOR_REASON","VOR_OBJECT_ID","REFLECTIVITY_ELIGIBLE_FOR_CR","CR_UNCERTAIN_MASK","CR_QUALIFICATION_REASON"]
    keys += [k+"_RAW" for k in ("SNR","RHOHV","ZDR","PHIDP","VR","SW")]
    return {k:np.array(group[k][:],copy=True) for k in keys if k in group}


def write_snapshots(root,output_store,result):
    cfg=getattr(result.profile,"volume_review",None)
    if cfg is None:
        return
    root.attrs["qc_volume_review_status"]=result.summary["volume_review"]["status"]
    for key,value in (getattr(result,"volume_review_artifacts",None) or {}).items():
        if not key.startswith("qc/volume_review/") or ".." in key:
            raise ValueError("invalid volume artifact path")
        output_store[key]=value
    if not cfg.export_evidence:
        return
    records=[]
    for qc in result.sweeps:
        arrays=snapshot_group(root[qc.name]); content=npz_bytes(arrays)
        path=f"qc/volume_review/{qc.name}.npz"; output_store[path]=content
        records.append({"sweep":qc.name,"path":path,"numeric_sha256":array_digest(arrays),
                        "sha256":hashlib.sha256(content).hexdigest(),"fields":sorted(arrays)})
    manifest={"schema":"rainpulse.bound-volume-evidence-v1","asset_id":root.attrs.get("asset_id"),
              "scan_id":root.attrs.get("scan_id"),"config_sha256":result.profile.parameters_hash,
              "algorithm_version":result.profile.pipeline_version,"extension_sha256":cfg.digest,
              "flag_definitions":{k:int(v) for k,v in result.profile.flag_masks.items()},
              "unknown_raw_codes_are_not_no_echo":True,"sweeps":records}
    output_store["qc/volume_review/manifest.json"]=json_bytes(manifest)


def bind_renderer_layer(objects,layer,group,qc_binding,field,valid,polar_rgba):
    arrays=snapshot_group(group)
    if field not in arrays:
        arrays[field]=np.array(group[field][:],copy=True)
    identity={"volume_id":str(layer["scan_id"]),"sweep_id":str(layer["sweep_number"]),
              "qc_content_sha256":qc_binding["qc_content_sha256"],"config_sha256":qc_binding["qc_parameters_sha256"],
              "sampling_version":layer["sampling_version"],"palette_version":layer["palette_version"]}
    snapshot,receipt=image_binding(arrays,field,valid,polar_rgba,objects[layer["object_path"]],identity)
    prefix="volume_review/polar/"+hashlib.sha256(json_bytes([identity,field])).hexdigest()
    objects[prefix+".npz"]=snapshot; objects[prefix+".json"]=json_bytes(receipt)
    layer.update(bound_source_path=prefix+".npz",bound_receipt_path=prefix+".json",numeric_source_sha256=receipt["numeric_sha256"])
