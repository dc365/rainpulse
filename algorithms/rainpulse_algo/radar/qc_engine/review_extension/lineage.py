"""Record actual mapping/weights; audit immutable exports; never infer gate IDs."""
import hashlib
import json
from pathlib import Path
import numpy as np
from . import VERSION
from .background import timestamp


def record_grid_selection(output, mapping, choose):
    if "SOURCE_RAY" not in output:
        return
    choose = np.asarray(choose, bool)
    if np.any(choose & ~np.asarray(mapping.supported, bool)):
        raise ValueError("a selected grid cell has no native mapping")
    for name, value in (("SOURCE_RAY", mapping.ray_index), ("SOURCE_GATE", mapping.gate_index)):
        a = np.asarray(value)
        if a.shape != choose.shape or np.any(a[choose] < 0):
            raise ValueError("invalid actual Hybrid source mapping")
        output[name][choose] = a[choose]


def validate_grid_sources(root):
    if not root.attrs.get("qc_review_extension_version"):
        return
    valid = np.asarray(root["VALID_MASK"][:]) == 1
    indices = {}
    for key in ("SOURCE_RAY", "SOURCE_GATE"):
        if key not in root or root[key].shape != valid.shape or root[key].dtype != np.dtype("int32"):
            raise ValueError(f"missing review source index {key}")
        x = np.asarray(root[key][:])
        if np.any(x[~valid] != -1) or np.any(x[valid] < 0):
            raise ValueError("review source index filled a missing cell")
        indices[key] = x
    sweep = np.asarray(root["SOURCE_SWEEP"][:])
    for index in np.unique(sweep[valid]):
        group = root[f"polar/sweep_{int(index):03d}"]
        selected = valid & (sweep == index)
        if np.any(indices["SOURCE_RAY"][selected] >= len(group["azimuth"])) or np.any(indices["SOURCE_GATE"][selected] >= len(group["range"])):
            raise ValueError("review source index is outside the recorded native cut")


def attach_mosaic_sources(fields, details, inputs, roots, weights, codes):
    versions = [r.attrs.get("qc_review_extension_version") for r in roots]
    if not any(versions):
        return details
    if any(x != VERSION for x in versions):
        raise ValueError("mosaic mixes review and stale/non-review grid assets")
    extra = {}
    for index, (item, root) in enumerate(zip(inputs, roots, strict=True)):
        name = f"CONTRIBUTOR_WEIGHT_{codes[item.radar_id]:03d}"
        # Exactly the weights used above in the real linear-Z fusion, not recomputed.
        fields[name] = np.asarray(weights[index], dtype="float32").copy()
        extra[item.radar_id] = {
            "qc_review_extension_version": VERSION,
            "review_weight_field": name,
            "review_grid_asset_id": str(root.attrs["asset_id"]),
            "review_qc_asset_id": str(root.attrs["qc_asset_id"]),
            "review_qc_parameters_sha256": str(root.attrs["qc_parameters_sha256"]),
        }
    return tuple({**d, **extra[d["radar_id"]]} for d in details)


def validate_mosaic_weights(root):
    contributors = root.attrs.get("contributors", [])
    review = [d for d in contributors if d.get("qc_review_extension_version")]
    if not review:
        return
    if len(review) != len(contributors) or any(d["qc_review_extension_version"] != VERSION for d in review):
        raise ValueError("partial/stale mosaic review provenance")
    valid = np.asarray(root["VALID_MASK"][:]) == 1
    total = np.zeros(valid.shape, "float64")
    count = np.zeros(valid.shape, "uint8")
    names = set()
    for d in review:
        key = d["review_weight_field"]
        if key in names or key not in root or root[key].shape != valid.shape or root[key].dtype != np.dtype("float32"):
            raise ValueError("invalid contributor weight identity/shape")
        names.add(key)
        w = np.asarray(root[key][:])
        if not np.isfinite(w).all() or np.any((w < 0) | (w > 1)) or np.any(w[~valid] != 0):
            raise ValueError("invalid contributor weights")
        total += w
        count += w > 0
    if not np.allclose(total[valid], 1, atol=1e-6) or not np.array_equal(count, root["CONTRIBUTOR_COUNT"][:]):
        raise ValueError("recorded contributors do not reconstruct fusion membership")


def tree_digest(path):
    """Same length-prefixed content construction used by hybrid._qc_artifact_digest.

    Hash local exported Zarr keys, not a ZIP file or an S3 ETag.
    """
    base = Path(path).resolve()
    if not base.is_dir():
        raise ValueError("an unpacked immutable artifact directory is required")
    h = hashlib.sha256()
    for file in sorted(base.rglob("*")):
        if file.is_symlink():
            raise ValueError("artifact exports may not contain symlinks")
        if not file.is_file():
            continue
        key = file.relative_to(base).as_posix().encode()
        data = file.read_bytes()
        h.update(len(key).to_bytes(4, "big")); h.update(key)
        h.update(len(data).to_bytes(8, "big")); h.update(hashlib.sha256(data).digest())
    return h.hexdigest()


def artifact_attrs(path):
    path = Path(path)
    if (path/".zattrs").exists():
        return json.loads((path/".zattrs").read_text())
    if (path/"zarr.json").exists():
        return json.loads((path/"zarr.json").read_text()).get("attributes", {})
    raise ValueError("export lacks Zarr root attributes")


def _references(attrs):
    found = set()
    def walk(value, root=False):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_asset_ids") and isinstance(item, list):
                    found.update(str(x) for x in item)
                elif (key.endswith("_asset_id") or (key == "asset_id" and not root)) and item:
                    found.add(str(item))
                elif isinstance(item, (dict, list)):
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(attrs, root=True)
    found.discard(str(attrs.get("asset_id")))
    return found


def compare_definitions(left, right):
    keys = ("field", "aggregation", "sampled_level", "units", "display_threshold_dbz",
            "color_scale_id", "coordinate_sha256", "analysis_time")
    missing = [k for k in keys if k not in left or k not in right or left[k] is None or right[k] is None]
    unequal = [k for k in keys if k not in missing and left[k] != right[k]]
    return {"comparable": not missing and not unequal, "unproven_fields": missing, "different_fields": unequal}


def audit_inventory(manifest, *, base_dir):
    if manifest.get("schema_version") != "rainpulse.review-lineage-v1":
        raise ValueError("unsupported review inventory schema")
    base = Path(base_dir).resolve()
    records, issues = {}, []
    for entry in manifest.get("assets", []):
        path = (base/entry["path"]).resolve()
        attrs = artifact_attrs(path)
        ident = str(attrs.get("asset_id", ""))
        if not ident or ident in records:
            raise ValueError("missing/duplicate actual asset identity")
        digest = tree_digest(path)
        expected = entry.get("content_sha256")
        if not expected:
            issues.append({"asset_id": ident, "reason": "expected_content_hash_missing"})
        elif expected != digest:
            issues.append({"asset_id": ident, "reason": "content_hash_mismatch"})
        records[ident] = {"attrs": attrs, "path": str(path), "content_sha256": digest, "parents": sorted(_references(attrs))}
    target = manifest.get("target_asset_id")
    if target not in records:
        raise ValueError("target asset must be an actual exported artifact")
    seen, active = set(), set()
    def visit(ident):
        if ident in active:
            raise ValueError("artifact provenance contains a cycle")
        if ident in seen:
            return
        active.add(ident)
        record = records[ident]
        # Normalized volumes are this QC audit's explicit upstream boundary.
        if record["attrs"].get("contract_name") != "rainpulse.normalized-radar-volume":
            for parent in record["parents"]:
                if parent not in records:
                    issues.append({"asset_id": ident, "parent_id": parent, "reason": "upstream_export_missing"})
                else:
                    visit(parent)
        active.remove(ident); seen.add(ident)
    visit(target)
    expected_qc = manifest.get("expected_qc", {})
    found_radars = set()
    t = timestamp(manifest["analysis_time"])
    max_age = float(manifest.get("maximum_source_age_seconds", 900))
    if not np.isfinite(max_age) or max_age < 0:
        raise ValueError("invalid source age budget")
    for ident in sorted(seen):
        attrs = records[ident]["attrs"]
        contract = attrs.get("contract_name")
        if "analysis_time" in attrs and timestamp(attrs["analysis_time"]) != t:
            issues.append({"asset_id": ident, "reason": "analysis_time_mismatch"})
        if contract in {"rainpulse.qc-radar-volume", "rainpulse.radar-grid"}:
            end = attrs.get("volume_end_time_utc")
            if end is None:
                issues.append({"asset_id": ident, "reason": "observation_time_unavailable"})
            elif not 0 <= (t-timestamp(end)).total_seconds() <= max_age:
                issues.append({"asset_id": ident, "reason": "future_or_stale_observation"})
        if contract == "rainpulse.qc-radar-volume":
            radar = str(attrs.get("radar_id"))
            found_radars.add(radar)
            desired = expected_qc.get(radar)
            if not desired:
                issues.append({"asset_id": ident, "reason": "expected_qc_identity_missing"})
            elif ident != desired.get("asset_id") or attrs.get("qc_parameters_sha256") != desired.get("parameters_sha256"):
                issues.append({"asset_id": ident, "reason": "stale_qc_generation", "expected": desired})
    for radar in set(expected_qc)-found_radars:
        issues.append({"radar_id": radar, "reason": "expected_radar_not_in_target_ancestry"})
    if not found_radars:
        issues.append({"reason": "no_qc_ancestry_proven"})
    comparisons = [compare_definitions(x["left"], x["right"]) for x in manifest.get("comparisons", [])]
    if any(not x["comparable"] for x in comparisons):
        issues.append({"reason": "product_or_rendering_definitions_differ"})
    return {
        "schema_version": manifest["schema_version"], "target_asset_id": target,
        "lineage_consistent": not issues, "same_product_comparison_proven": bool(comparisons) and all(x["comparable"] for x in comparisons),
        "issues": issues, "comparisons": comparisons, "ancestry": sorted(seen), "assets": records,
        "scope": "immutable_exports_from_normalized_boundary; no_08:24_data_included",
    }


def trace_mosaic_cell(mosaic, grids, qc_volumes, *, row, column):
    shape = mosaic["VALID_MASK"].shape
    if not 0 <= row < shape[0] or not 0 <= column < shape[1]:
        raise ValueError("cell is outside the actual mosaic grid")
    validate_mosaic_weights(mosaic)
    if mosaic["VALID_MASK"][row, column] != 1:
        return {"status": "missing_not_valid_no_rain", "contributors": []}
    rows, weighted_z, weights = [], 0., 0.
    for detail in mosaic.attrs.get("contributors", []):
        name = detail.get("review_weight_field")
        if not name or name not in mosaic:
            raise ValueError("actual contributor weights were not recorded; regenerate Grid/Mosaic")
        weight = float(mosaic[name][row, column])
        if weight == 0:
            continue
        gid, qid = detail["review_grid_asset_id"], detail["review_qc_asset_id"]
        grid, qc = grids[gid], qc_volumes[qid]
        if str(grid.attrs["asset_id"]) != gid or str(grid.attrs["qc_asset_id"]) != qid or str(qc.attrs["asset_id"]) != qid:
            raise ValueError("trace asset identity differs")
        if grid.attrs.get("coordinate_sha256") != mosaic.attrs.get("coordinate_sha256"):
            raise ValueError("trace grid coordinates differ")
        if detail["review_qc_parameters_sha256"] != qc.attrs.get("qc_parameters_sha256"):
            raise ValueError("trace selected a stale QC generation")
        validate_grid_sources(grid)
        sweep = int(grid["SOURCE_SWEEP"][row, column])
        ray, gate = int(grid["SOURCE_RAY"][row, column]), int(grid["SOURCE_GATE"][row, column])
        g = qc[f"sweep_{sweep:03d}"]
        if ray < 0 or gate < 0 or ray >= g["VALID_MASK"].shape[0] or gate >= g["VALID_MASK"].shape[1]:
            raise ValueError("trace points outside actual QC geometry")
        if g["VALID_MASK"][ray, gate] != 1 or g["QPE_ELIGIBLE_MASK"][ray, gate] != 1:
            raise ValueError("withheld or missing QC gate leaked into mosaic")
        value = float(grid["DBZH_QC"][row, column])
        raw = float(g["DBZH_RAW"][ray, gate])
        if not np.isfinite(value) or not np.isclose(value, raw, atol=1e-5):
            raise ValueError("recorded Hybrid source does not reconstruct selected raw reflectivity")
        weighted_z += weight * 10**(value/10); weights += weight
        diagnostic = {key: np.asarray(g[key][ray, gate]).item() for key in (
            "QC_ACTION", "QC_FLAGS", "QUALITY_INDEX", "RFI_QUARANTINE_MASK", "NP_CLASS", "NP_QUARANTINE_MASK", "NP_EVIDENCE_BITS", "SRC_REVIEW_QUALIFIED_MASK", "V7_FIRST_DECIDER",
        ) if key in g}
        rows.append({"radar_id": detail["radar_id"], "weight": weight, "grid_asset_id": gid, "qc_asset_id": qid,
                     "sweep": sweep, "ray": ray, "gate": gate, "azimuth_deg": float(g["azimuth"][ray]),
                     "range_m": float(g["range"][gate]), "beam_height_m": float(grid["BEAM_HEIGHT"][row, column]),
                     "DBZH_RAW": raw, "diagnostics": diagnostic})
    expected = 10*np.log10(weighted_z) if weighted_z > 0 else np.nan
    actual = float(mosaic["DBZH_QC"][row, column])
    if not np.isclose(weights, 1., atol=1e-6) or not np.isclose(expected, actual, atol=1e-4):
        raise ValueError("actual native contributions do not reconstruct this mosaic cell")
    return {"status": "verified_recorded_sources", "row": row, "column": column, "DBZH_QC": actual,
            "reconstructed_DBZH_QC": float(expected), "contributors": rows}
