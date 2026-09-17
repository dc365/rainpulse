#!/usr/bin/env python3
"""Read-only P0/P1/combined replay against saved stage evidence.

This is a lowest-cut, target-stage intervention, NOT a full historical Worker
reconstruction. Source inputs/QC assets remain unchanged. No truth labels, no
publication, no filling, no source-image pixel heuristics in algorithm inputs.
"""

import argparse
import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "algorithms"))
import numpy as np
import zarr
from PIL import Image
from rainpulse_algo.diagnostics.polar_sampling import polar_pixels, project_rgba
from rainpulse_algo.diagnostics.renderer import REFLECTIVITY_STOPS, _scalar_rgba
from rainpulse_algo.radar.qc import load_qc_profile
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.decision import Decision
from rainpulse_algo.radar.qc_engine.finalize import finalize_decision
from rainpulse_algo.radar.qc_engine.forensic_io import directory_digest
from rainpulse_algo.radar.qc_engine.generalization import broad_source_review
from rainpulse_algo.radar.qc_engine.object_consensus.adapter import raw_from_native
from rainpulse_algo.radar.qc_engine.object_consensus.engine import infer
from rainpulse_algo.radar.qc_engine.quality_policy import health_facets
from rainpulse_algo.radar.qc_engine.range_signature import range_signatures


def safe(v):
    if isinstance(v, dict):
        return {str(k): safe(x) for k, x in v.items()}
    if isinstance(v, (tuple, list)):
        return [safe(x) for x in v]
    if isinstance(v, np.ndarray):
        return safe(v.tolist())
    if isinstance(v, np.generic):
        return safe(v.item())
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


def write(path, value):
    path.write_text(
        json.dumps(safe(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )


def png(path, values, valid, native):
    rgba = _scalar_rgba(
        native.restore(values), native.restore(valid), REFLECTIVITY_STOPS
    )
    # Source-order azimuth accompanies restored arrays. Do not use sorted index as source ray.
    az = np.empty_like(native.azimuth)
    az[native.original_indices] = native.azimuth
    image = project_rgba(rgba, az, native.ranges, 640)
    Image.fromarray(image).save(path)


def reconstruct_prehealth(g, native, health, old):
    action = g["QC_ACTION"][:][native.original_indices]
    arrays = {
        k: g[k][:][native.original_indices]
        for k in g.array_keys()
        if g[k].shape == native.shape
        and (
            k
            in {
                "QC_ACTION",
                "QPE_ELIGIBLE_MASK",
                "REFLECTIVITY_TRUST_MASK",
                "RFI_QUARANTINE_MASK",
                "RFI_RISK_STATE",
                "RFI_MIXED_MASK",
                "DBZH_USABLE",
            }
            or k.endswith("_TRUST_MASK")
        )
    }
    q = g["QUALITY_INDEX"][:][native.original_indices]
    # Inversion is solely for reproducing the stored final policy projection. It
    # cannot reconstruct earlier internal classifiers or change their decisions.
    factor = (
        old.health_gate.degraded_quality_multiplier
        if health["health"] == "DEGRADED"
        else 1.0
    )
    preq = q / factor
    final_only = g["V7_FIRST_INELIGIBLE_DECIDER"][:][native.original_indices] == 7
    preeligible = (arrays["QPE_ELIGIBLE_MASK"] == 1) | final_only
    if np.any(
        preeligible
        & ((action == 2) | (action == 3) | (arrays["RFI_QUARANTINE_MASK"] == 1))
    ):
        raise ValueError("recorded first-eligibility stage contradicts decisions")
    arrays["QPE_ELIGIBLE_MASK"] = preeligible.astype("uint8")
    arrays["DBZH_USABLE"] = np.where(preeligible, native.fields["DBZH"], np.nan).astype(
        "float32"
    )
    # Existing reason flags are retained: this is not reconstruction of each
    # prehealth flag's first setter. Report flags separately from QI eligibility.
    flags = g["QC_FLAGS"][:][native.original_indices]
    d = Decision(arrays, flags.copy(), preq.copy())
    reference = deepcopy(d)
    rq, _, _, _ = finalize_decision(native, reference, old, health)
    if not np.array_equal(
        reference.arrays["QPE_ELIGIBLE_MASK"],
        g["QPE_ELIGIBLE_MASK"][:][native.original_indices],
    ):
        raise ValueError("cannot exactly reproduce saved health eligibility")
    if not np.allclose(rq, q, rtol=0, atol=1e-7, equal_nan=True):
        raise ValueError("cannot reproduce saved quality within float32 tolerance")
    return d


def region(native, g, site):
    z = native.fields["DBZH"]
    valid = native.field_available["DBZH"]
    if site == "SITE_A":
        return (
            (g["QPE_ELIGIBLE_MASK"][:][native.original_indices] == 1)
            & (z >= 50)
            & (native.ranges[None, :] >= 100000)
            & (native.ranges[None, :] < 400000)
        )
    if site == "SITE_C":
        az = np.empty_like(native.azimuth)
        az[native.original_indices] = native.azimuth
        m = polar_pixels(az, native.ranges, 640)
        pix = np.zeros((640, 640), bool)
        pix[433:473, 192:245] = True
        pix &= m.available
        rr, gg = m.ray[pix], m.gate[pix]
        real = native.restore(valid)
        zz = native.restore(z)
        usable = real[rr, gg] & (zz[rr, gg] >= -10)
        rr, gg = rr[usable], gg[usable]
        if not len(rr):
            return np.zeros(native.shape, bool)
        center = np.median(az[rr])
        ang = abs((native.azimuth - center + 180) % 360 - 180) < 12
        return (
            ang[:, None]
            & (native.ranges[None, :] >= native.ranges[gg].min() - 5000)
            & (native.ranges[None, :] <= native.ranges[gg].max() + 5000)
            & valid
            & (z >= 10)
        )
    return valid & (z >= 10)


def run(root, output):
    root, output = root.resolve(), output.resolve()
    if output.exists() or output == root or root in output.parents:
        raise ValueError("new output outside input required")
    old = load_qc_profile(
        ROOT / "configs/qc/fujian-qc-object-consensus-oc1.yaml",
        ROOT / "configs/qc/flag-definitions-v2.yaml",
    )
    p = load_qc_profile(
        ROOT / "configs/qc/fujian-qc-generalization-p0p2.yaml",
        ROOT / "configs/qc/flag-definitions-v2.yaml",
    )
    metadata = [
        json.loads(x) for x in (root / "metadata/cases.jsonl").read_text().splitlines()
    ]
    if len({r["case_id"] for r in metadata}) != len(metadata):
        raise ValueError("duplicate case id")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".p0p2-", dir=output.parent))
    records = []
    try:
        for row in metadata:
            t = perf_counter()
            case = row["case_id"]
            npath = root / row["normalized_path"]
            if not npath.resolve().is_relative_to(root):
                raise ValueError("input path escapes root")
            if directory_digest(npath) != row["normalized_sha256_scrubbed"]:
                raise ValueError("raw input hash differs")
            qpaths = list((root / row["qc_path"]).glob("_objects/*"))
            if len(qpaths) != 1:
                raise ValueError("ambiguous QC asset")
            qpath = qpaths[0]
            qsha = directory_digest(qpath)
            nroot = zarr.open_group(str(npath), mode="r")
            qroot = zarr.open_group(str(qpath), mode="r")
            g = qroot["sweep_000"]
            n = adapt_sweep(nroot, "sweep_000", old)
            if not np.array_equal(
                n.restore(n.fields["DBZH"]), g["DBZH_RAW"][:], equal_nan=True
            ):
                raise ValueError("raw/QC array mismatch")
            h = json.loads((npath / "health/summary.json").read_text())
            qs = json.loads((qpath / "qc/summary.json").read_text())
            d = reconstruct_prehealth(g, n, h, old)
            re = range_signatures(n, p.cross_radar, route_all_shapes=True)
            if not np.array_equal(
                re.arrays["V5_RANGE_CANDIDATE_MASK"],
                g["V5_RANGE_CANDIDATE_MASK"][:][n.original_indices],
            ):
                raise ValueError("frozen range replay differs")
            e = infer(raw_from_native(n, p.geometry.phase_period_deg))
            for key, saved in [
                ("family_code", "OC1_FAMILY_CODE"),
                ("state", "OC1_STATE"),
                ("reason", "OC1_REASON"),
            ]:
                if not np.array_equal(e.arrays[key], g[saved][:][n.original_indices]):
                    raise ValueError(f"OC1 replay differs: {case}/{key}")
            ocstatus = qs["sweeps"]["sweep_000"]["v7_graph"]["object_consensus"][
                "status"
            ]
            ocproposal = (e.arrays["state"] == 5) & (
                e.arrays["bracketed_reference_mask"] == 1
            )
            ocout = SimpleNamespace(
                summary={"status": ocstatus}, proposed_quarantine=ocproposal
            )
            cross = g["V7_CROSS_RADAR_SUPPORT_SCORE"][:][n.original_indices]
            vertical = g["V7_VERTICAL_SUPPORT_SCORE"][:][n.original_indices]
            weather = np.fmax(cross, vertical)
            domain = region(n, g, row["site"])
            out = tmp / case
            out.mkdir()
            before = g["QPE_ELIGIBLE_MASK"][:][n.original_indices] == 1
            png(out / "raw.png", n.fields["DBZH"], n.field_available["DBZH"], n)
            png(out / "baseline.png", n.fields["DBZH"], before, n)
            modes = {}
            results = {}
            for name, split, apply in [
                ("p0_only", True, False),
                ("p1_p2_only", False, True),
                ("combined", True, True),
            ]:
                current = deepcopy(d)
                pp = p.model_copy(
                    update={
                        "generalization": p.generalization.model_copy(
                            update={"split_admission_health": split}
                        )
                    }
                )
                detail = {"status": "not_executed"}
                if apply:
                    current, detail = broad_source_review(
                        n,
                        current,
                        pp,
                        e,
                        ocout,
                        weather_support=weather,
                        eligible_before_oc1=current.arrays["QPE_ELIGIBLE_MASK"] == 1,
                    )
                quality, _, _, _flags = finalize_decision(n, current, pp, h)
                eligible = current.arrays["QPE_ELIGIBLE_MASK"] == 1
                added = (
                    current.arrays.get("P2_ADDED_QUARANTINE_MASK", np.zeros(n.shape))
                    == 1
                )
                png(out / (name + ".png"), n.fields["DBZH"], eligible, n)
                m = {
                    "eligible_gates": int(eligible.sum()),
                    "additional_isolation_gates": int(added.sum()),
                    "eligible_restored_by_administrative_projection": int(
                        (eligible & ~before).sum()
                    ),
                    "eligible_withheld": int((before & ~eligible).sum()),
                    "region_gates": int(domain.sum()),
                    "region_admin_restored": int((domain & eligible & ~before).sum()),
                    "region_additional_isolation": int((domain & added).sum()),
                    "region_eligible_after": int((domain & eligible).sum()),
                    "new_confirmed_pollution": 0,
                    "review_required": detail.get("review_required", False),
                    "health_facets": health_facets(h, pp),
                }
                modes[name] = m
                results[name] = {
                    "eligible": n.restore(eligible),
                    "quality": n.restore(quality),
                    "action": n.restore(current.arrays["QC_ACTION"]),
                    "quarantine": n.restore(current.arrays["RFI_QUARANTINE_MASK"]),
                    "added": n.restore(added),
                    "reason": n.restore(
                        current.arrays.get(
                            "P2_REVIEW_REASON", np.zeros(n.shape, "uint32")
                        )
                    ),
                }
                write(out / (name + "-details.json"), detail)
                if np.any(
                    eligible
                    & (
                        (current.arrays["QC_ACTION"] == 2)
                        | (current.arrays["RFI_QUARANTINE_MASK"] == 1)
                        | ~n.field_available["DBZH"]
                    )
                ):
                    raise AssertionError("invalid quantitative eligibility")
            arrays = {
                f"{mode}_{k}": v
                for mode, values in results.items()
                for k, v in values.items()
            }
            arrays.update(
                region_mask=n.restore(domain),
                range_supported=n.restore(re.arrays["P2_RANGE_MEASUREMENT_MASK"]),
                range_route=n.restore(re.arrays["P2_RANGE_ROUTE_CODE"]),
            )
            np.savez_compressed(out / "replay_fields.npz", **arrays)
            rec = {
                **row,
                "scope": "actual_lowest_cut_target_stage_replay_not_full_worker_or_independent_weather_truth",
                "frozen_range_mask_exact": True,
                "frozen_OC1_fields_exact": True,
                "baseline_health_eligibility_exact": True,
                "baseline_quality_tolerance": 1e-7,
                "prehealth_flags_provenance": "retained_saved_flags_not_all_first_setters_reconstructed",
                "new_range_measurement_supported": int(
                    re.arrays["P2_RANGE_MEASUREMENT_MASK"].sum()
                ),
                "new_range_wide_supported": int(
                    (re.arrays["P2_RANGE_ROUTE_CODE"] > 1).sum()
                ),
                "range_routes": re.summary["routed_objects"],
                "review_region_is_ground_truth": False,
                "modes": modes,
                "qc_export_sha256": qsha,
                "recorded_weather_context": "saved_vertical_and_cross_arrays_same_for_all_modes",
                "budget_denominator": "recorded_OC1_output_prehealth_eligible; fullWorker uses pre_OC1 eligible",
                "elapsed_seconds": perf_counter() - t,
                "upload_seconds": None,
                "precision": None,
                "recall": None,
            }
            if (
                directory_digest(npath) != row["normalized_sha256_scrubbed"]
                or directory_digest(qpath) != qsha
            ):
                raise ValueError("input mutated")
            write(out / "summary.json", rec)
            records.append(rec)
            print(case, json.dumps(modes["combined"]), flush=True)
        write(
            tmp / "summary.json",
            {
                "baseline": "c3e7680478c3df3fe7a609b267e10a7bcccce455",
                "cases": records,
                "same_renderer": "native-footprint-v2, original palette",
                "operational_eligible": False,
                "truth_labels": None,
            },
        )
        sections = []
        for r in records:
            case = r["case_id"]
            pics = "".join(
                f'<figure><img src="{case}/{x}.png"><figcaption>{label}</figcaption></figure>'
                for x, label in [
                    ("raw", "原始"),
                    ("baseline", "已发布基线"),
                    ("p0_only", "仅P0行政/物理分离"),
                    ("p1_p2_only", "仅P1/P2隔离复核"),
                    ("combined", "P0–P2组合"),
                ]
            )
            sections.append(
                f'<h2>{case} / {r["site"]}</h2><p><a href="{case}/summary.json">逐项统计与证据</a></p><div class="row">{pics}</div>'
            )
        (tmp / "index.html").write_text(
            '<meta charset="utf-8"><title>P0–P2 九例阶段重放</title><style>body{font:16px/1.6 system-ui;margin:30px} .row{display:flex;flex-wrap:wrap}figure{width:20%;margin:0}img{width:100%;background:#111}h2{margin-top:32px}</style><h1>P0–P2 原始证据阶段重放</h1><p>真实输入，旧距离模型和OC1逐门结果已核验；仅重放目标阶段，不是完整历史Worker重建，也没有人工真值。图像空白是不可定量/缺测/色标以下，不代表无雨。恢复资格不等于已证天气，隔离不等于确认污染。</p>'
            + "".join(sections)
        )
        tmp.rename(output)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    run(a.input, a.output)
