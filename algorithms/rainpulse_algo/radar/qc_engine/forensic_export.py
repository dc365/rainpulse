"""Read-only frozen PNG -> native gate evidence, not a real-weather classifier.

No network client, object upload, database or environment credential read.
Use original 640x640 RGBA panels and the matching polar DBZH_QC layer object.
"""

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Literal

import numpy as np
import zarr
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from ...diagnostics.polar_sampling import SAMPLING_VERSION
from .fingerprints import array_digest
from .forensic_io import directory_digest, verified_path
from .narrow_local import NarrowStage
from .network_manifest import FrozenFile
from .residual import ResidualReason
from .review import _safe_json
from .trace_pixel import trace


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=200)
    row: int = Field(ge=0, lt=640)
    column: int = Field(ge=0, lt=640)
    radius_gates: int = Field(default=2, ge=0, le=20)
    role: str = Field(default="unlabeled_review", max_length=200)


class Panel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    panel_id: str = Field(min_length=1, max_length=200)
    analysis_id: str = Field(min_length=1, max_length=200)
    expected_qc_asset_id: str = Field(min_length=1, max_length=200)
    qc_zarr: FrozenFile
    png: FrozenFile
    layer: FrozenFile
    sweep: str = Field(pattern=r"^sweep_[0-9]{3}$")
    samples: list[Sample] = Field(min_length=1, max_length=20)


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["rainpulse.qc-forensic.v1"]
    panels: list[Panel] = Field(min_length=1, max_length=24)


def _semantic_group(group):
    observation, decision = {}, {}
    for name in sorted(group.array_keys()):
        if name.endswith("_RAW") or name in {
            "azimuth",
            "range",
            "elevation",
            "ray_time",
            "VALID_MASK",
        }:
            observation[name] = array_digest(group[name][:])
        elif name in {
            "QC_ACTION",
            "QC_FLAGS",
            "QUALITY_INDEX",
            "RFI_QUARANTINE_MASK",
            "QPE_ELIGIBLE_MASK",
            "DBZH_USABLE",
        }:
            decision[name] = array_digest(group[name][:])
    return {"observation": observation, "decision": decision}


def _context(root):
    value = root.attrs.get("radial_context")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = None
    if not isinstance(value, dict):
        return {"status": "not_available; full context equivalence unproven"}
    return {
        k: value.get(k)
        for k in (
            "context_fingerprint",
            "decision_cutoff_utc",
            "artifacts",
            "geometry_resources",
            "prepared_context",
            "support_statistics",
        )
    }


def panel_evidence(base, panel):
    q = verified_path(base, panel.qc_zarr.model_dump(), directory=True)
    png = verified_path(base, panel.png.model_dump())
    layer_path = verified_path(base, panel.layer.model_dump())
    root = zarr.open_group(str(q), mode="r")
    layer = json.loads(layer_path.read_text())
    if root.attrs.get("contract_name") != "rainpulse.qc-radar-volume":
        raise ValueError("expected a QC radar artifact")
    if str(root.attrs.get("asset_id")) != panel.expected_qc_asset_id:
        raise ValueError("declared QC asset identity differs")
    if layer.get("sampling_version") != SAMPLING_VERSION:
        raise ValueError("wrong sampling version; rerender V5 using the same renderer first")
    if layer.get("field") != "DBZH_QC" or layer.get("scope") != "polar":
        raise ValueError("requires the original polar QC reflectivity layer")
    for key in ("scan_id", "radar_id"):
        if str(layer.get(key, "")).lower() != str(root.attrs.get(key, "")).lower():
            raise ValueError("layer and QC observation identity differ")
    if int(layer["sweep_number"]) != int(panel.sweep.split("_")[-1]):
        raise ValueError("layer sweep mismatch")
    if panel.sweep not in root:
        raise ValueError("unknown cut")
    with Image.open(png) as image:
        if image.mode != "RGBA" or image.size != (640, 640):
            raise ValueError("only original 640x640 RGBA source PNGs are supported")
        rgba = np.asarray(image).copy()
    group = root[panel.sweep]
    fields = [k for k in sorted(group.array_keys()) if group[k].shape == group["DBZH_RAW"].shape]
    samples = []
    for item in panel.samples:
        value = trace(root, sweep=panel.sweep, row=item.row, column=item.column, size=640)
        value.update(
            label=item.label,
            review_role=item.role,
            actual_png_rgba=rgba[item.row, item.column].tolist(),
            sampling_verified_against_png=True,
            truth_label=None,
        )
        alpha = int(rgba[item.row, item.column, 3])
        value["display_contradiction"] = bool(
            alpha > 0
            and (
                not value["measured_footprint"]
                or not value.get("eligible", False)
                or value["fields"].get("QC_ACTION") == 2
            )
        )
        if value["measured_footprint"]:
            ray, gate = value["ray_index"], value["gate_index"]
            lo = max(0, gate - item.radius_gates)
            hi = min(group["DBZH_RAW"].shape[1], gate + item.radius_gates + 1)
            value["adjacent_measured_gates"] = dict(
                start_gate=lo,
                end_gate_exclusive=hi,
                fields={k: group[k][ray, lo:hi].tolist() for k in fields},
            )
            value["decoded_residual_reasons"] = [
                bit.name
                for bit in ResidualReason
                if int(value["fields"].get("V6_DECISION_REASON", 0)) & int(bit)
            ]
            value["decoded_local_route_reasons"] = [
                bit.name
                for bit in NarrowStage
                if int(value["fields"].get("V61_NARROW_STAGE_REASON", 0)) & int(bit)
            ]
        samples.append(value)
    if directory_digest(q) != panel.qc_zarr.sha256:
        raise ValueError("QC artifact changed during forensic read")
    return _safe_json(
        dict(
            panel_id=panel.panel_id,
            analysis_id=panel.analysis_id,
            scan_id=str(root.attrs.get("scan_id")),
            radar_id=root.attrs.get("radar_id"),
            sweep=panel.sweep,
            asset_id=panel.expected_qc_asset_id,
            profile=root.attrs.get("qc_profile"),
            pipeline=root.attrs.get("qc_pipeline_version"),
            input_hashes={
                "qc": panel.qc_zarr.sha256,
                "png": panel.png.sha256,
                "layer": panel.layer.sha256,
            },
            context=_context(root),
            semantic_hashes=_semantic_group(group),
            samples=samples,
            association_boundary=(
                "analysis-to-QC association declared in frozen manifest; no server API query"
            ),
        )
    )


def export(manifest, output):
    path, output = Path(manifest).resolve(), Path(output).absolute()
    spec = Manifest.model_validate_json(path.read_text())
    if spec.schema_version != "rainpulse.qc-forensic.v1":
        raise ValueError("unsupported forensic manifest")
    if len({p.panel_id for p in spec.panels}) != len(spec.panels):
        raise ValueError("duplicate panel identity")
    if output.exists():
        raise ValueError("forensic output already exists")
    records = [panel_evidence(path.parent, p) for p in spec.panels]
    pairs = []
    for i, left in enumerate(records):
        for right in records[i + 1 :]:
            if (left["radar_id"], left["scan_id"], left["sweep"]) != (
                right["radar_id"],
                right["scan_id"],
                right["sweep"],
            ):
                continue
            lc, rc = left["context"], right["context"]
            evidence_present = all(
                c.get("prepared_context") and c.get("geometry_resources") for c in (lc, rc)
            )
            pairs.append(
                dict(
                    panels=[left["panel_id"], right["panel_id"]],
                    independent_observations=1,
                    same_observation_arrays=left["semantic_hashes"]["observation"]
                    == right["semantic_hashes"]["observation"],
                    same_decision_arrays=left["semantic_hashes"]["decision"]
                    == right["semantic_hashes"]["decision"],
                    same_prepared_evidence=(
                        lc["prepared_context"] == rc["prepared_context"]
                        and lc["geometry_resources"] == rc["geometry_resources"]
                    )
                    if evidence_present
                    else None,
                    same_cutoff=lc.get("decision_cutoff_utc") == rc.get("decision_cutoff_utc")
                    if lc.get("decision_cutoff_utc")
                    else None,
                    note="same scan is one observation; array equality alone is not a skill score",
                )
            )
    result = dict(
        schema_version="rainpulse.qc-forensic-report.v1",
        panels=records,
        same_scan_pairs=pairs,
        unique_physical_scan_count=len({(x["radar_id"], x["scan_id"]) for x in records}),
        manifest_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        accuracy=None,
        recall=None,
        published=False,
        real_weather_validation=False,
    )
    content = json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2)
    if len(content.encode()) > 40 * 1024 * 1024:
        raise ValueError("forensic output exceeds 40 MiB budget")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".lock")
    with lock.open("x"):
        pass
    temporary = Path(tempfile.mkdtemp(prefix="qc-forensic-", dir=output.parent))
    try:
        (temporary / "report.json").write_text(content)
        if output.exists():
            raise ValueError("output appeared during export")
        os.rename(temporary, output)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
        lock.unlink()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export(args.manifest, args.output)
    print(
        json.dumps(
            {"output": str(args.output), "panels": len(result["panels"]), "published": False}
        )
    )


if __name__ == "__main__":
    main()
