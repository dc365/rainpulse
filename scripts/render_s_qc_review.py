#!/usr/bin/env python3
"""Render bound native QC audit targets; research overlays never mean removal."""

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def temporal_overlay(snapshot, report_path, raw):
    report = json.loads(report_path.read_text())
    if (
        report.get("scope") != "past_original_seed_recurrence_not_published_QC_delta"
        or report.get("action_authority") is not False
        or report.get("product_writes") is not False
    ):
        raise ValueError("temporal evidence must remain non-actionable")
    matches = [r for r in report["results"] if r["snapshot_sha256"] == digest(snapshot)]
    if len(matches) != 1 or matches[0].get("action_authority") is not False:
        raise ValueError("unique bound temporal snapshot required")
    record = matches[0]
    path = Path(record["diagnostics"])
    if digest(path) != record["diagnostics_sha256"]:
        raise ValueError("temporal diagnostic digest mismatch")
    with np.load(path, allow_pickle=False) as data:
        masks = {
            key: data["ST_" + key + "_MASK"]
            for key in ("MATCH", "CANDIDATE", "MEASURED", "BOUNDARY_SUPPORTED")
        }
    for value in masks.values():
        if (
            value.shape != raw.shape
            or value.dtype != np.dtype("uint8")
            or not np.isin(value, (0, 1)).all()
        ):
            raise ValueError("invalid temporal overlay mask")
    hit = masks["MATCH"] == 1
    if (
        np.any(
            hit
            & (~np.isfinite(raw) | (masks["CANDIDATE"] != 1) | (masks["MEASURED"] != 1))
        )
        or np.any((masks["BOUNDARY_SUPPORTED"] == 1) & ~hit)
        or int(hit.sum()) != record["matched_gates"]
    ):
        raise ValueError("temporal overlay lacks original measured candidate")
    return hit


def footprint_overlay(snapshot, published, report_path, raw):
    report = json.loads(report_path.read_text())
    if (
        report.get("scope") != "complete_original_source_replay_not_published_QC"
        or report.get("action_authority") is not False
        or report.get("product_writes") is not False
    ):
        raise ValueError("footprint replay must remain non-actionable")
    matches = [r for r in report["results"] if r["snapshot_sha256"] == digest(snapshot)]
    if len(matches) != 1:
        raise ValueError("unique bound footprint snapshot required")
    record = matches[0]
    if (
        record.get("published_receipt_sha256") != digest(published)
        or record.get("action_authority") is not False
    ):
        raise ValueError("footprint published binding/authority mismatch")
    path = Path(record["diagnostics"])
    if digest(path) != record["diagnostics_sha256"]:
        raise ValueError("footprint diagnostic digest mismatch")
    with np.load(path, allow_pickle=False) as data:
        recovered = data["RECOVERED_MASK"]
    if (
        recovered.shape != raw.shape
        or recovered.dtype != np.dtype("uint8")
        or not np.isin(recovered, (0, 1)).all()
        or np.any((recovered == 1) & ~np.isfinite(raw))
        or int(recovered.sum()) != record["recovered_gates"]
    ):
        raise ValueError("invalid recovered footprint mask")
    return recovered == 1


def anchored_overlay(snapshot, published, report_path, raw, *, method="anchored-shape"):
    """Show bound original-object proposals; never imply production removal."""
    report = json.loads(report_path.read_text())
    if (report.get("scope") != "original_RAW_boundary_research_not_published_QC"
            or report.get("method") != method
            or report.get("action_authority") is not False
            or report.get("product_writes") is not False):
        raise ValueError("anchored evidence must remain non-actionable")
    matches = [r for r in report["results"] if r["snapshot_sha256"] == digest(snapshot)]
    if len(matches) != 1:
        raise ValueError("unique bound anchored snapshot required")
    record = matches[0]
    if (record.get("published_receipt_sha256") != digest(published)
            or record.get("action_authority") is not False):
        raise ValueError("anchored published binding/authority mismatch")
    path = Path(record["diagnostics"])
    if digest(path) != record["diagnostics_sha256"]:
        raise ValueError("anchored diagnostic digest mismatch")
    with np.load(path, allow_pickle=False) as data:
        if method not in ("anchored-shape", "original-fan"):
            raise ValueError("unsupported original-object overlay")
        prefix = "RV2_ORIGINAL_FAN_SHAPE_" if method == "original-fan" else "RV2_ANCHORED_RADIAL_SHAPE_"
        qualified = data[prefix + "QUALIFIED_MASK"]
        owner = data[prefix + "SOURCE_ID"]
        ambiguous = data[prefix + "AMBIGUOUS_MASK"]
    if (qualified.shape != raw.shape or qualified.dtype != np.dtype("uint8")
            or ambiguous.shape != raw.shape or ambiguous.dtype != np.dtype("uint8")
            or owner.shape != raw.shape or owner.dtype != np.dtype("uint32")
            or not np.isin(qualified, (0, 1)).all()
            or not np.isin(ambiguous, (0, 1)).all()
            or not np.array_equal(qualified == 1, owner > 0)
            or np.any((qualified == 1) & ((ambiguous == 1) | ~np.isfinite(raw) | (raw < 0)))
            or int(qualified.sum()) != record["qualified_gates"]):
        raise ValueError("invalid anchored original-object mask")
    return qualified == 1


def render(
    snapshot,
    published,
    output,
    shape_report=None,
    source_report=None,
    temporal_report=None,
    footprint_report=None,
    anchored_report=None,
    original_fan_report=None,
):
    if output.exists() or output.with_suffix(".json").exists():
        raise ValueError("new image and receipt required")
    if anchored_report and original_fan_report:
        raise ValueError("select one original-object overlay")
    receipt = json.loads(published.read_text())
    if receipt["scope"] != "exact_Web_consumed_stored_QC_not_replay":
        raise ValueError("actual published QC audit required")
    if digest(snapshot) != receipt["snapshot_sha256"]:
        raise ValueError("snapshot does not match published audit")
    with np.load(snapshot, allow_pickle=False) as data:
        raw = data["RAW"]
        az = data["AZIMUTH"]
        ranges = data["RANGE"]
        meta = json.loads(str(data["METADATA"]))
    if meta["scan_id"] != receipt["web_frame_identity"]["web_scan_id"]:
        raise ValueError("native scan mismatch")
    selected = np.zeros(raw.shape, bool)
    visible = selected.copy()
    qc = np.full(raw.shape, np.nan)
    for record in receipt["target_records"]:
        row, col = record["row"], record["column"]
        if selected[row, col] or not np.isclose(
            raw[row, col], record["raw_dbzh"], atol=0.0001, rtol=0
        ):
            raise ValueError("duplicate or unbound native measurement")
        if not (
            np.isclose(az[row], record["azimuth_deg"], atol=0.0001, rtol=0)
            and np.isclose(ranges[col], record["range_m"], atol=0.001, rtol=0)
        ):
            raise ValueError("native coordinate mismatch")
        selected[row, col] = True
        visible[row, col] = record["renderer_visible"]
        if record["qc_dbzh"] is not None:
            qc[row, col] = record["qc_dbzh"]
    if (
        selected.sum() != receipt["target_gates"]
        or visible.sum() != receipt["renderer_eligible_visible_gates"]
    ):
        raise ValueError("published count mismatch")
    candidates = np.zeros(raw.shape, bool)
    source_matches = candidates.copy()
    temporal_matches = candidates.copy()
    footprint_matches = candidates.copy()
    anchored_matches = candidates.copy()
    object_report = original_fan_report or anchored_report
    if object_report:
        method = "original-fan" if original_fan_report else "anchored-shape"
        anchored_matches = anchored_overlay(snapshot, published, object_report, raw, method=method) & visible
        record = next(r for r in json.loads(object_report.read_text())["results"]
                      if r["snapshot_sha256"] == digest(snapshot))
        if anchored_matches.sum() != record["qualified_published_overlap"]:
            raise ValueError("anchored published overlap mismatch")
    if footprint_report:
        footprint_matches = (
            footprint_overlay(snapshot, published, footprint_report, raw) & visible
        )
        record = next(
            r
            for r in json.loads(footprint_report.read_text())["results"]
            if r["snapshot_sha256"] == digest(snapshot)
        )
        if footprint_matches.sum() != record["recovered_published_overlap"]:
            raise ValueError("footprint published overlap mismatch")
    if temporal_report:
        temporal_matches = temporal_overlay(snapshot, temporal_report, raw) & visible
    for path in [shape_report, source_report]:
        if path is None:
            continue
        report = json.loads(path.read_text())
        if (
            report["snapshot_sha256"] != digest(snapshot)
            or report["published_receipt_sha256"] != digest(published)
            or report["action_authority"]
            or report["product_writes"]
        ):
            raise ValueError("research report binding/authority mismatch")
        if path == shape_report:
            evidence = report["detectors"]["variable_object"]
            for original in evidence["boundary_target_hypotheses"]:
                for run in original["matched_original_runs"]:
                    rr = slice(run["native_row_start"], run["native_row_end"])
                    columns = (ranges // original["scale_m"]).astype(int) == run[
                        "block"
                    ]
                    candidates[rr] |= columns[None, :] & (
                        raw[rr] >= original["level_dbz"]
                    )
            candidates &= visible
            if candidates.sum() != evidence["boundary_candidate_overlap"]:
                raise ValueError("research candidate count mismatch")
        if path == source_report:
            evidence = report["detectors"]["source_segments"]
            for record in evidence["records"]:
                if record["combined_source_agreement"]:
                    source_matches[record["row"], record["column"]] = True
            if (source_matches & ~visible).any() or source_matches.sum() != evidence[
                "target_same_source_agreement"
            ]:
                raise ValueError("source agreement count mismatch")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    theta = np.deg2rad(az[:, None])
    radius = ranges[None, :] / 1000.0
    x = radius * np.sin(theta)
    y = radius * np.cos(theta)
    rr, cc = np.where(selected)
    margin = 25.0
    bounds = (
        float(x[rr, cc].min() - margin),
        float(x[rr, cc].max() + margin),
        float(y[rr, cc].min() - margin),
        float(y[rr, cc].max() + margin),
    )
    context = (
        np.isfinite(raw)
        & (raw >= 5)
        & (x >= bounds[0])
        & (x <= bounds[1])
        & (y >= bounds[2])
        & (y <= bounds[3])
    )
    panels = (
        3 if (shape_report or source_report or temporal_report or footprint_report
              or object_report) else 2
    )
    fig, axes = plt.subplots(1, panels, figsize=(5 * panels, 5.5), layout="constrained")
    titles = [
        f"RAW audited targets: {selected.sum()}",
        f"Published QC visible targets: {visible.sum()}",
        f"Research: shape {candidates.sum()}, source {source_matches.sum()}, past {temporal_matches.sum()}",
    ]
    if footprint_report:
        titles[2] = f"Offline recovered: {footprint_matches.sum()}\nNot published QC"
    if object_report:
        titles[2] = f"Offline original-object proposals: {anchored_matches.sum()}\nNot published QC"
    for index, ax in enumerate(axes):
        ax.scatter(x[context], y[context], color="#d9e1e8", s=0.6, rasterized=True)
        use = selected if index == 0 else visible
        values = raw if index == 0 else qc
        points = ax.scatter(
            x[use],
            y[use],
            c=values[use],
            cmap="turbo",
            vmin=0,
            vmax=70,
            s=5,
            rasterized=True,
        )
        if index == 2:
            if object_report:
                ax.scatter(x[anchored_matches], y[anchored_matches], color="#ed2939",
                           s=40, edgecolors="#263238", linewidths=0.5,
                           label="Original-object shape (not removal)")
            if footprint_report:
                ax.scatter(
                    x[footprint_matches],
                    y[footprint_matches],
                    color="#ed2939",
                    s=40,
                    edgecolors="#263238",
                    linewidths=0.5,
                    label="Complete original-source proof (not removal)",
                )
            ax.scatter(
                x[candidates],
                y[candidates],
                color="#f28e00",
                s=8,
                label="Shape candidate",
            )
            ax.scatter(
                x[source_matches],
                y[source_matches],
                color="#a400b5",
                s=13,
                label="Original source agreement",
            )
            ax.scatter(
                x[temporal_matches],
                y[temporal_matches],
                color="#00c9c9",
                s=35,
                edgecolors="#263238",
                linewidths=0.4,
                label="Past original seed match (not removal)",
            )
            ax.legend(loc="lower left", fontsize=8)
        ax.scatter([0], [0], marker="+", color="#263238", s=40)
        ax.set(
            xlim=bounds[:2],
            ylim=bounds[2:],
            title=titles[index],
            xlabel="East (km)",
            ylabel="North (km)",
        )
        ax.set_aspect("equal")
        ax.grid(alpha=0.2)
    fig.colorbar(points, ax=axes[:2], shrink=0.6, label="dBZ")
    fig.suptitle(
        f"{meta['radar_id'].upper()} {meta['local_time']} CST | sweep {meta['sweep']}\n"
        "Gray = RAW context, not QC; colored = audited selection only; research overlays are NOT removals",
        fontsize=11,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=150)
    plt.close(fig)
    result = dict(
        snapshot_sha256=digest(snapshot),
        published_sha256=digest(published),
        shape_report_sha256=digest(shape_report) if shape_report else None,
        source_report_sha256=digest(source_report) if source_report else None,
        temporal_report_sha256=digest(temporal_report) if temporal_report else None,
        footprint_report_sha256=digest(footprint_report) if footprint_report else None,
        anchored_report_sha256=digest(anchored_report) if anchored_report else None,
        script_sha256=digest(Path(__file__)),
        image_sha256=digest(output),
        selected=int(selected.sum()),
        published_visible=int(visible.sum()),
        shape_candidates=int(candidates.sum()),
        source_agreements=int(source_matches.sum()),
        past_source_matches=int(temporal_matches.sum()),
        recovered_footprint_matches=int(footprint_matches.sum()),
        anchored_shape_matches=0 if original_fan_report else int(anchored_matches.sum()),
        original_fan_matches=int(anchored_matches.sum()) if original_fan_report else 0,
        original_fan_report_sha256=digest(original_fan_report) if original_fan_report else None,
        scope="audited_selection_only_with_RAW_context",
        research_is_removal=False,
        product_writes=False,
    )
    with output.with_suffix(".json").open("x") as stream:
        json.dump(result, stream, indent=2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("published", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--shape-report", type=Path)
    parser.add_argument("--source-report", type=Path)
    parser.add_argument("--temporal-report", type=Path)
    parser.add_argument("--footprint-report", type=Path)
    parser.add_argument("--anchored-report", type=Path)
    parser.add_argument("--original-fan-report", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            render(
                args.snapshot,
                args.published,
                args.output,
                args.shape_report,
                args.source_report,
                args.temporal_report,
                args.footprint_report,
                args.anchored_report,
                args.original_fan_report,
            )
        )
    )
