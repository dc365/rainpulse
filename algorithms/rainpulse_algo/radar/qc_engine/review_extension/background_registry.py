"""One shared profile hash, separately verified native backgrounds for each radar.

The registry stores URIs/digests, not all radar arrays in one giant in-memory NPZ.
Declared configuration bindings are explicitly distinguished from measured metadata.
"""
import json
import numpy as np
from .background import ASSET_VERSION, IDENTITY_FIELDS, digest, verify_background, text, _sha

REGISTRY_VERSION = "rainpulse-clutter-registry-v1"


def create_registry(entries):
    result, seen = [], set()
    for entry in entries:
        item = {k: text(entry, k) for k in IDENTITY_FIELDS}
        item["asset_uri"] = text(entry, "asset_uri")
        item["asset_version"] = entry.get("asset_version")
        if item["asset_version"] != ASSET_VERSION:
            raise ValueError("registry requires canonical v2 background assets")
        item["asset_content_sha256"] = _sha(entry.get("asset_content_sha256"))
        key = item["radar_id"], item["radar_config_version"]
        if key in seen:
            raise ValueError("one explicit hardware/scan binding per radar configuration version required")
        seen.add(key); result.append(item)
    if not result:
        raise ValueError("background registry must not be empty")
    result.sort(key=lambda x:(x["radar_id"],x["radar_config_version"]))
    meta = {"asset_version":REGISTRY_VERSION,"entries":result,
            "binding_semantics":"explicit_operator_binding_to_immutable_radar_config_version; not_hardware_inference"}
    encoded=json.dumps(meta,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    if len(encoded)>1024*1024:
        raise ValueError("registry metadata exceeds 1 MiB")
    arrays={"__meta_json":np.frombuffer(encoded,"uint8").copy()}
    return arrays,{**meta,"asset_content_sha256":digest(arrays)}


def load_verified_background(arrays, root, expected_sha, expected_version, *, load_asset):
    if digest(arrays)!=expected_sha:
        raise ValueError("background binding content hash differs")
    x=np.asarray(arrays.get("__meta_json"))
    if x.dtype!=np.dtype("uint8") or x.ndim!=1 or x.size>16*1024*1024:
        raise ValueError("invalid background binding metadata")
    meta=json.loads(x.tobytes().decode())
    if expected_version==ASSET_VERSION:
        return verify_background(arrays,root,expected_sha,expected_version)
    if expected_version!=REGISTRY_VERSION or meta.get("asset_version")!=REGISTRY_VERSION or set(arrays)!={"__meta_json"}:
        raise ValueError("unsupported background registry version/encoding")
    canonical,record=create_registry(meta["entries"])
    if digest(canonical)!=expected_sha:
        raise ValueError("background registry is not canonical")
    found=[e for e in record["entries"] if e["radar_id"]==str(root.attrs.get("radar_id")) and e["radar_config_version"]==str(root.attrs.get("radar_config_version"))]
    if len(found)!=1:
        raise ValueError("no unique background binding for current radar/configuration")
    entry=found[0]
    asset=load_asset(entry["asset_uri"])
    result=verify_background(asset,root,entry["asset_content_sha256"],entry["asset_version"],identity_binding=entry)
    for values in result.values():
        values["nonprecip_background_receipt"].update(
            binding_content_sha256=expected_sha,binding_version=REGISTRY_VERSION,
            selected_asset_uri=entry["asset_uri"],
        )
    return result
