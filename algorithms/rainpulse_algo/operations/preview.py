# ruff: noqa: E501, I001
"""Standalone single-scan previews, without fabricating a RadarAnalysis product."""
from __future__ import annotations

import hashlib
import json

from .preview_mask import business_support


def render_preview(request, client):
    import numpy as np
    import yaml

    from rainpulse_algo.diagnostics.png import encode_rgba_png
    from rainpulse_algo.diagnostics.renderer import (
        QUALITY_STOPS,
        REFLECTIVITY_STOPS,
        _dbzh_sweeps,
        _open_group,
        _polar_to_ppi,
        _scalar_rgba,
    )
    from rainpulse_algo.radar.qc_zarr import validate_qc_zarr_store
    from rainpulse_algo.worker.object_store import ArtifactObjectReader, artifact_sha256
    from rainpulse_algo.worker.runtime import WorkerResult

    import os
    from pathlib import Path

    payload = request["payload"]
    source = ArtifactObjectReader(client).load(payload["input_uri"])
    digest = artifact_sha256(source)
    if payload.get("input_sha256") and digest != payload["input_sha256"]:
        raise ValueError("candidate source hash differs")
    validate_qc_zarr_store(source)
    root = _open_group(source)
    if (str(root.attrs.get("scan_id")) != payload["scan_id"]
            or str(root.attrs.get("radar_id")) != payload["radar_id"]):
        raise ValueError("QC identity differs from preview request")
    flags_config = yaml.safe_load(Path(os.environ["RAINPULSE_QC_FLAG_DEFINITIONS"]).read_text())
    version = flags_config["definition_version"]
    if version != root.attrs.get("flag_definition_version") or version != payload["flag_definition_version"]:
        raise ValueError("QC and mounted flag definitions differ; no implicit reinterpretation")
    definitions = {v["name"]: int(v["mask"]) for v in flags_config["flags"]}
    objects, layers = {}, []
    sweeps = list(_dbzh_sweeps(root))
    if not 1 <= len(sweeps) <= 32:
        raise ValueError("preview requires 1-32 real DBZH sweeps")
    for group, number in sweeps:
        flags = group["QC_FLAGS"][:]
        eligible = group["QPE_ELIGIBLE_MASK"][:] if "QPE_ELIGIBLE_MASK" in group else None
        for field, title, stops in (
            ("DBZH_RAW", "原始反射率", REFLECTIVITY_STOPS),
            ("DBZH_QC", "业务可用质控反射率", REFLECTIVITY_STOPS),
            ("QUALITY_INDEX", "质量指数", QUALITY_STOPS),
        ):
            values = group[field][:]
            valid = np.isfinite(values)
            if field == "DBZH_QC":
                valid = business_support(values, flags, definitions, flag_version=version, eligible=eligible)
            rgba = _scalar_rgba(values, valid, stops, smooth=field != "QUALITY_INDEX")
            png = encode_rgba_png(_polar_to_ppi(rgba, group["azimuth"][:], group["range"][:], 512))
            key = f"layers/sweep-{int(number):03d}-{field.lower()}.png"
            objects[key] = png
            layers.append({"object_path": key, "title": title, "field": field,
                "sweep_number": int(number), "elevation_deg": float(np.nanmedian(group["elevation"][:])),
                "maximum_range_km": float(np.max(group["range"][:]) / 1000),
                "sha256": hashlib.sha256(png).hexdigest(), "width": 512, "height": 512,
                "valid_gate_count": int(valid.sum()), "source_gate_count": int(valid.size)})
    manifest = {"schema_version": "ops-preview-1", "candidate_only": True,
        "default_publication_changed": False, "sampling_version": "native-footprint-v2",
        "scan_id": payload["scan_id"], "radar_id": payload["radar_id"],
        "input_uri": payload["input_uri"], "input_sha256": digest,
        "qc_pipeline_version": root.attrs.get("qc_pipeline_version"),
        "flag_definition_version": version, "layers": layers,
        "impact": "独立单站预览；格点、拼图、QPE、预报未更新"}
    objects["manifest.json"] = json.dumps(manifest, ensure_ascii=False, allow_nan=False).encode()
    return WorkerResult(objects=objects, diagnostics={"ops_preview": {"layer_count": len(layers),
        "scan_id": payload["scan_id"], "manifest_path": "manifest.json"}}, metrics={"layer_count": float(len(layers))})
