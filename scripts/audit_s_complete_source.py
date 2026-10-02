#!/usr/bin/env python3
"""Compare complete frozen-source references with a pinned baseline, read-only."""

import argparse
import hashlib
import json
import subprocess
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import (
    source_footprint as module,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def extent_decisions(native, group, blocked, result, targets):
    """Separate angular loss from range loss without changing qualification.

    Replay the exact frozen-ID and angular-island ownership used by qualify;
    diagnostic code zero outside the candidate mask is never acceptance.
    """
    parent = group["RV2_RAW_FAN_ID"]
    seed = group["RV2_SOURCE_LEDGER_SEED_ID"]
    observed = native.field_available["DBZH"]
    blocked = blocked | ~native.geometry_good[:, None]
    candidate = result[module.PREFIX + "CANDIDATE_MASK"] == 1
    code = result[module.PREFIX + "REJECTION_CODE"]
    labels = {}
    cache = {}
    for i, j in zip(*np.where(targets), strict=True):
        if not candidate[i, j]:
            reason = "outside_candidate:" + (
                "blocked" if blocked[i, j] else
                "already_seed" if seed[i, j] else "no_raw_parent"
            )
        elif code[i, j] != 4:
            reason = module.REJECTIONS[int(code[i, j])]
        else:
            identity = int(parent[i, j])
            if identity not in cache:
                original = (seed > 0) & observed & ~blocked
                clues = original & (parent == identity)
                rows = np.unique(np.where(clues)[0])
                source = original & np.isin(seed, np.unique(seed[clues]))
                source[~np.isin(np.arange(native.shape[0]), rows)] = False
                islands = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
                cache[identity] = (source, [v for v in islands if len(v) >= 2])
            source, islands = cache[identity]
            island = next((v for v in islands if i in v), None)
            if island is None:
                reason = "outside_original_extent:angular"
            else:
                columns = np.where(source[island])[1]
                in_range = (
                    native.ranges[columns].min() <= native.ranges[j]
                    <= native.ranges[columns].max()
                )
                reason = "outside_original_extent:" + (
                    "unresolved" if in_range else "range"
                )
        labels[reason] = labels.get(reason, 0) + 1
    return labels


def replay(path, old, published):
    snapshot_sha = digest(path)
    with np.load(path, allow_pickle=False) as data:
        a = {k: data[k] for k in data.files}
    meta = json.loads(str(a["METADATA"]))
    if meta["scan_id"] != meta["web_frame_identity"]["web_scan_id"]:
        raise ValueError("exact Web-selected scan required")
    n = SimpleNamespace(
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
    raw_before = a["RAW"].copy()
    blocked = (a["WEATHER"] == 1) | (a["CONFLICTS"] == 1) | (a["RV2_BARRED_MASK"] == 1)
    group = {key: a[key] for key in ("RV2_RAW_FAN_ID", "RV2_SOURCE_LEDGER_SEED_ID")}
    before, old_report = old.qualify(n, blocked, group)
    after, new_report = module.qualify(n, blocked, group)
    module.validate(
        {**after, **group, **module.evidence(n)}, n.field_available["DBZH"], blocked
    )
    prior = before[module.PREFIX + "QUALIFIED_MASK"] == 1
    current = after[module.PREFIX + "QUALIFIED_MASK"] == 1
    recovered, withdrawn = current & ~prior, prior & ~current
    if (current & blocked).any() or (current & ~n.field_available["DBZH"]).any():
        raise ValueError("qualification crossed original protection/observation")
    if not np.array_equal(a["RAW"], raw_before, equal_nan=True):
        raise ValueError("RAW changed")
    row = dict(
        snapshot=str(path),
        snapshot_sha256=digest(path),
        scan_id=meta["scan_id"],
        radar_id=meta["radar_id"],
        local_time=meta["local_time"],
        baseline_qualified=int(prior.sum()),
        current_qualified=int(current.sum()),
        recovered_gates=int(recovered.sum()),
        withdrawn_gates=int(withdrawn.sum()),
        current_polar_retained=new_report["current_polar_retained_gates"],
        withdrawn_decisions={
            name: int(
                (withdrawn & (after[module.PREFIX + "REJECTION_CODE"] == code)).sum()
            )
            for code, name in module.REJECTIONS.items()
        },
        baseline_decisions=old_report["candidate_decisions"],
        current_decisions=new_report["candidate_decisions"],
        raw_unchanged=True,
        protection_overlap=0,
        action_authority=False,
        published_delta_gates=0,
    )
    for receipt_path in published:
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("scope") != "exact_Web_consumed_stored_QC_not_replay":
            raise ValueError("actual published receipt required")
        if receipt["snapshot_sha256"] != row["snapshot_sha256"]:
            continue
        if "published_receipt_sha256" in row:
            raise ValueError("duplicate published receipt for snapshot")
        if receipt["web_frame_identity"]["web_scan_id"] != meta["scan_id"]:
            raise ValueError("published scan mismatch")
        visible = np.zeros(n.shape, bool)
        seen = visible.copy()
        for t in receipt["target_records"]:
            i, j = t["row"], t["column"]
            if seen[i, j] or not np.isclose(
                a["RAW"][i, j], t["raw_dbzh"], atol=0.0001, rtol=0
            ):
                raise ValueError("duplicate/unbound target")
            if not (
                np.isclose(a["AZIMUTH"][i], t["azimuth_deg"], atol=0.0001, rtol=0)
                and np.isclose(a["RANGE"][j], t["range_m"], atol=0.001, rtol=0)
            ):
                raise ValueError("published native coordinate mismatch")
            seen[i, j] = True
            visible[i, j] = t["renderer_visible"]
        if (
            seen.sum() != receipt["target_gates"]
            or visible.sum() != receipt["renderer_eligible_visible_gates"]
        ):
            raise ValueError("published count mismatch")
        row.update(
            published_receipt_sha256=digest(receipt_path),
            published_visible=int(visible.sum()),
            recovered_published_overlap=int((recovered & visible).sum()),
            withdrawn_published_overlap=int((withdrawn & visible).sum()),
            published_extent_decisions=extent_decisions(
                n, group, blocked, after, visible
            ),
        )
    if digest(path) != snapshot_sha:
        raise ValueError("snapshot changed during replay")
    return row, recovered, withdrawn


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots", nargs="+", type=Path)
    p.add_argument("--baseline-ref", required=True)
    p.add_argument("--published", action="append", default=[], type=Path)
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"], text=True
    ).strip()
    relative = "algorithms/rainpulse_algo/radar/qc_engine/review_extension/radial_revision/source_footprint.py"
    source = subprocess.check_output(
        ["git", "show", commit + ":" + relative], text=True
    )
    old = types.ModuleType("complete_source_baseline")
    old.__package__ = module.__package__
    exec(compile(source, commit + ":" + relative, "exec"), old.__dict__)
    bound_code = [Path(module.__file__), Path(__file__)]
    for name in ("anchored_radial_shape.py", "original_fan_shape.py"):
        child = Path(module.__file__).with_name(name)
        if child.exists():
            bound_code.append(child)
    code_sha = {str(path): digest(path) for path in bound_code}
    args.output.mkdir(parents=True)
    results = []
    for path in args.snapshots:
        row, recovered, withdrawn = replay(path, old, args.published)
        output = args.output / (path.stem + ".npz")
        if output.exists():
            raise ValueError("duplicate snapshot name")
        np.savez_compressed(
            output,
            RECOVERED_MASK=recovered.astype("uint8"),
            WITHDRAWN_MASK=withdrawn.astype("uint8"),
        )
        row.update(diagnostics=str(output), diagnostics_sha256=digest(output))
        results.append(row)
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in (
                        "radar_id",
                        "local_time",
                        "recovered_gates",
                        "withdrawn_gates",
                    )
                }
            ),
            flush=True,
        )
    if any(digest(Path(path)) != sha for path, sha in code_sha.items()):
        raise ValueError("replay code changed during run")
    report = dict(
        code_sha256=code_sha,
        scope="complete_original_source_replay_not_published_QC",
        baseline_commit=commit,
        baseline_source_sha256=hashlib.sha256(source.encode()).hexdigest(),
        source_sha256=digest(Path(module.__file__)),
        script_sha256=digest(Path(__file__)),
        product_writes=False,
        action_authority=False,
        results=results,
    )
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
