#!/usr/bin/env python3
"""Fixed native comparison of fixed, variable and unified object evidence.

Read-only input. No workers, database, products or original arrays are changed.
Historical Web residual receipts are optional and must bind exact input bytes.
"""

import argparse
from collections import Counter
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import types

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "algorithms/rainpulse_algo/radar/qc_engine"
for name, paths in (("unified_audit", []), ("unified_audit.engine", [str(PACKAGE)])):
    module = types.ModuleType(name)
    module.__path__ = paths
    sys.modules[name] = module
BASE = "unified_audit.engine.review_extension.radial_revision."


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def published_targets(paths, snapshot, a, meta):
    selected = []
    for path in paths:
        receipt = json.loads(path.read_text())
        if receipt.get("snapshot_sha256") != digest(snapshot):
            continue
        if receipt.get("scope") != "exact_Web_consumed_stored_QC_not_replay":
            raise ValueError("exact stored Web receipt required")
        if receipt["web_frame_identity"]["web_scan_id"] != meta["scan_id"]:
            raise ValueError("published identity differs")
        visible = np.zeros(a["RAW"].shape, bool)
        seen = visible.copy()
        for item in receipt["target_records"]:
            row, col = item["row"], item["column"]
            if seen[row, col] or not all(
                (
                    np.isclose(
                        a["RAW"][row, col], item["raw_dbzh"], rtol=0, atol=0.0001
                    ),
                    np.isclose(
                        a["AZIMUTH"][row], item["azimuth_deg"], rtol=0, atol=0.0001
                    ),
                    np.isclose(a["RANGE"][col], item["range_m"], rtol=0, atol=0.001),
                )
            ):
                raise ValueError("unbound published target")
            seen[row, col] = True
            visible[row, col] = item["renderer_visible"]
        if int(visible.sum()) != receipt["renderer_eligible_visible_gates"]:
            raise ValueError("published visible count differs")
        selected.append((visible, digest(path)))
    if len(selected) > 1:
        raise ValueError("ambiguous published receipt")
    return selected[0] if selected else (None, None)


def replay(path, output, receipts, plot, subbands=False, separated_edges=False):
    before = digest(path)
    with np.load(path, allow_pickle=False) as source:
        a = {key: source[key] for key in source.files}
    meta = json.loads(str(a["METADATA"]))
    if meta["scan_id"] != meta["web_frame_identity"]["web_scan_id"]:
        raise ValueError("exact selected scan required")
    native = types.SimpleNamespace(
        shape=a["RAW"].shape,
        ranges=a["RANGE"],
        azimuth=a["AZIMUTH"],
        geometry_good=a["GEOMETRY_GOOD"],
        gap_after=a["GAP_AFTER"],
        fields={
            "DBZH": a["RAW"],
            **{k[7:]: v for k, v in a.items() if k.startswith("MOMENT_")},
        },
        field_available={k[10:]: v for k, v in a.items() if k.startswith("AVAILABLE_")},
    )
    raw = a["RAW"].copy()
    blocked = (a["WEATHER"] == 1) | (a["CONFLICTS"] == 1) | (a["RV2_BARRED_MASK"] == 1)
    beam = meta["config"]["fragment_line"].get("antenna_beam_width_deg")
    methods = (
        ("fixed", "morphology_objects", {"physical_windows": True}),
        ("variable", "variable_morphology", {}),
        ("unified", "unified_objects", {}),
    )
    if subbands:
        methods += (
            ("unified_subbands", "unified_objects", {"subbands_enabled": True}),
        )
    if separated_edges:
        methods += (
            (
                "unified_separate_edges",
                "unified_objects",
                {"subbands_enabled": True, "separated_edges_enabled": True},
            ),
        )
    all_arrays, rows = {}, {}
    visible, receipt_sha = published_targets(receipts, path, a, meta)
    remaining = a["BEFORE"].astype(bool) & ~a["ADDED"].astype(bool)
    for label, name, options in methods:
        module = importlib.import_module(BASE + name)
        start = time.monotonic()
        unified = name == "unified_objects"
        func = module.evaluate if unified else module.detect
        arrays, report = func(native, blocked, beam_width=beam, **options)
        elapsed = time.monotonic() - start
        module.validate(arrays, native, blocked, beam_width=beam, **options)
        chosen = (
            arrays[module.PREFIX + ("PROPOSAL_MASK" if unified else "STRONG_MASK")] == 1
        )
        if (chosen & blocked).any() or (chosen & ~native.field_available["DBZH"]).any():
            raise ValueError("proposal crossed external protection or missing")
        holds = Counter(reason for obj in report["objects"] for reason in obj["holds"])
        rows[label] = dict(
            version=report["version"],
            elapsed_seconds=elapsed,
            proposal_gates=int(chosen.sum()),
            source_stage_remaining_overlap=int((chosen & remaining).sum()),
            stored_web_visible_overlap=int((chosen & visible).sum())
            if visible is not None
            else None,
            object_count=len(report["objects"]),
            object_holds=dict(holds),
            action_gates=report["action_gates"],
            raw_unchanged=True,
            protection_overlap=0,
        )
        all_arrays[label + "_PROPOSAL"] = chosen.astype("uint8")
        if unified:
            rows[label]["objects"] = report["objects"]
            all_arrays.update(arrays)
    if (
        not np.array_equal(raw, native.fields["DBZH"], equal_nan=True)
        or digest(path) != before
    ):
        raise ValueError("immutable RAW input changed")
    record = dict(
        snapshot=str(path),
        snapshot_sha256=before,
        scan_id=meta["scan_id"],
        sweep=meta["sweep"],
        radar_id=meta["radar_id"],
        local_time=meta["local_time"],
        published_receipt_sha256=receipt_sha,
        stored_web_visible=int(visible.sum()) if visible is not None else None,
        methods=rows,
    )
    evidence = output / (path.stem + ".npz")
    np.savez_compressed(evidence, **all_arrays)
    record["evidence_sha256"] = digest(evidence)
    if plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        az = np.deg2rad(a["AZIMUTH"][:, None])
        r = a["RANGE"][None, :] / 1000.0
        x, y = r * np.sin(az), r * np.cos(az)
        observed = native.field_available["DBZH"] & np.isfinite(raw) & (raw >= 0)
        target = visible if visible is not None else remaining
        fig, axes = plt.subplots(
            1, len(rows), figsize=(5 * len(rows), 5), constrained_layout=True
        )
        for ax, label in zip(axes, rows, strict=True):
            ax.scatter(
                x[observed], y[observed], color="#cccccc", s=0.2, rasterized=True
            )
            ax.scatter(x[target], y[target], color="#1787bc", s=2, rasterized=True)
            hit = target & (all_arrays[label + "_PROPOSAL"] == 1)
            ax.scatter(x[hit], y[hit], color="#d00000", s=3, rasterized=True)
            ax.set(
                title=f"{label}: {int(hit.sum())}/{int(target.sum())} nominated",
                xlim=(-470, 470),
                ylim=(-470, 470),
                xlabel="East (km)",
                ylabel="North (km)",
            )
            ax.set_aspect("equal")
            ax.grid(alpha=0.2)
        kind = "historical stored Web" if visible is not None else "source-stage"
        fig.suptitle(
            path.stem
            + f" | {kind} residuals; red offline proposals, no new publication"
        )
        image = output / (path.stem + ".png")
        fig.savefig(image, dpi=140)
        plt.close(fig)
        record["image_sha256"] = digest(image)
    print(
        path.stem,
        [
            (k, v["proposal_gates"], v["stored_web_visible_overlap"])
            for k, v in rows.items()
        ],
        flush=True,
    )
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--published", type=Path, action="append", default=[])
    parser.add_argument("--plot", action="store_true")
    parser.add_argument(
        "--subbands", action="store_true", help="Compare original subbands with v1"
    )
    parser.add_argument(
        "--separated-edges",
        action="store_true",
        help="Compare independent side references",
    )
    args = parser.parse_args()
    modules = (
        "unified_objects.py",
        "object_model.py",
        "projected_objects.py",
        "native_subbands.py",
        "variable_morphology.py",
        "morphology_objects.py",
    )
    paths = [PACKAGE / "review_extension/radial_revision" / p for p in modules] + [
        Path(__file__)
    ]
    hashes = {str(p.relative_to(ROOT)): digest(p) for p in paths}
    if len({p.stem for p in args.snapshots}) != len(args.snapshots):
        raise ValueError("duplicate output identity")
    args.output.mkdir(parents=True, exist_ok=False)
    records = [
        replay(
            p,
            args.output,
            args.published,
            args.plot,
            args.subbands,
            args.separated_edges,
        )
        for p in args.snapshots
    ]
    if any(digest(ROOT / p) != sha for p, sha in hashes.items()):
        raise ValueError("algorithm bytes changed during replay")
    report = dict(
        scope="fixed_native_object_comparison_not_published_QC",
        code_sha256=hashes,
        results=records,
        action_gates=0,
        independent_weather_truth=False,
        product_writes=False,
        radvol_baseline_implemented=False,
    )
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
