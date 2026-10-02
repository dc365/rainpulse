#!/usr/bin/env python3
"""Replay original source envelopes against a pinned Git baseline; no publication."""

import argparse
import hashlib
import json
import subprocess
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import (
    source_envelope,
    source_ledger,
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def baseline(ref):
    commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", ref + "^{commit}"], text=True
    ).strip()
    path = "algorithms/rainpulse_algo/radar/qc_engine/review_extension/radial_revision/source_envelope.py"
    source = subprocess.check_output(["git", "show", commit + ":" + path], text=True)
    module = types.ModuleType("flank_baseline")
    module.__package__ = source_envelope.__package__
    exec(compile(source, commit + ":" + path, "exec"), module.__dict__)
    return commit, hashlib.sha256(source.encode()).hexdigest(), module


def replay(path, old, published=None):
    with np.load(path, allow_pickle=False) as data:
        a = {k: data[k] for k in data.files}
    meta = json.loads(str(a["METADATA"]))
    if meta["web_frame_identity"]["web_scan_id"] != meta["scan_id"]:
        raise ValueError("exact original Web scan required")
    n = SimpleNamespace(
        shape=a["RAW"].shape,
        ranges=a["RANGE"],
        azimuth=a["AZIMUTH"],
        fields={
            "DBZH": a["RAW"],
            **{k[7:]: v for k, v in a.items() if k.startswith("MOMENT_")},
        },
        field_available={k[10:]: v for k, v in a.items() if k.startswith("AVAILABLE_")},
        geometry_good=a["GEOMETRY_GOOD"],
        gap_after=a["GAP_AFTER"],
    )
    blocked = (a["WEATHER"] == 1) | (a["CONFLICTS"] == 1) | (a["RV2_BARRED_MASK"] == 1)
    seed = a["RV2_SOURCE_LEDGER_SEED_MASK"] == 1
    targets = a["RV2_SOURCE_LEDGER_CANDIDATE_MASK"] == 1
    beam = meta["config"]["fragment_line"]["antenna_beam_width_deg"]
    options = dict(beam_width=beam)
    old_envelope, _ = old.detect(n, blocked, seed, targets, **options)
    saved = source_ledger._corridor
    try:
        source_ledger._corridor = lambda *args, **kwargs: old._corridor(*args)
        old_ledger, old_report = source_ledger.freeze(
            n, blocked, seed, targets, **options
        )
    finally:
        source_ledger._corridor = saved
    new_envelope, _ = source_envelope.detect(n, blocked, seed, targets, **options)
    new_ledger, new_report = source_ledger.freeze(n, blocked, seed, targets, **options)
    source_ledger.validate(new_ledger, n.field_available["DBZH"], blocked, seed)
    for name in ("SEED_ID", "SEED_MASK", "KIND", "START_M", "END_M", "SUPPORT_M"):
        if not np.array_equal(
            old_ledger["RV2_SOURCE_LEDGER_" + name],
            new_ledger["RV2_SOURCE_LEDGER_" + name],
            equal_nan=True,
        ):
            raise ValueError("original source identity/support changed")
    if not np.array_equal(n.fields["DBZH"], a["RAW"], equal_nan=True):
        raise ValueError("RAW changed")
    proposal = (new_envelope["RV2_ENVELOPE_MASK"] == 1) & (
        old_envelope["RV2_ENVELOPE_MASK"] == 0
    )
    if (proposal & blocked).any() or (proposal & ~n.field_available["DBZH"]).any():
        raise ValueError("recovered proposal crossed protection/observation")
    row = dict(
        snapshot=str(path),
        snapshot_sha256=digest(path),
        scan_id=meta["scan_id"],
        local_time=meta["local_time"],
        radar_id=meta["radar_id"],
        old_narrow_sources=old_report["narrow_supported_objects"],
        new_narrow_sources=new_report["narrow_supported_objects"],
        old_linked_gates=old_report["linked_gates"],
        new_linked_gates=new_report["linked_gates"],
        recovered_envelope_gates=int(proposal.sum()),
        original_source_identity_unchanged=True,
        raw_unchanged=True,
        action_authority=False,
        published_delta_gates=0,
    )
    if published and published["snapshot_sha256"] == row["snapshot_sha256"]:
        visible = np.zeros(n.shape, bool)
        for t in published["target_records"]:
            i, j = t["row"], t["column"]
            if not np.isclose(a["RAW"][i, j], t["raw_dbzh"], atol=0.0001, rtol=0):
                raise ValueError("published target RAW mismatch")
            visible[i, j] = t["renderer_visible"]
        row["actual_published_visible_targets"] = int(visible.sum())
        row["recovered_overlap_with_published_targets"] = int(
            (proposal & visible).sum()
        )
    return row, proposal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", nargs="+", type=Path)
    parser.add_argument("--baseline-ref", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--published", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    published = json.loads(args.published.read_text()) if args.published else None
    if (
        published
        and published.get("scope") != "exact_Web_consumed_stored_QC_not_replay"
    ):
        raise ValueError("actual published receipt required")
    commit, old_sha, old = baseline(args.baseline_ref)
    args.output.mkdir(parents=True)
    records = []
    for path in args.snapshots:
        row, proposal = replay(path, old, published)
        output = args.output / (path.stem + ".npz")
        np.savez_compressed(output, RECOVERED_ENVELOPE_MASK=proposal.astype("uint8"))
        row.update(diagnostics=str(output), diagnostics_sha256=digest(output))
        records.append(row)
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in (
                        "radar_id",
                        "local_time",
                        "old_narrow_sources",
                        "new_narrow_sources",
                        "recovered_envelope_gates",
                    )
                }
            ),
            flush=True,
        )
    report = dict(
        scope="original_source_flank_sampling_replay_not_published_QC",
        baseline_commit=commit,
        baseline_source_sha256=old_sha,
        product_writes=False,
        action_authority=False,
        results=records,
        code_sha256={
            m.__name__: digest(Path(m.__file__))
            for m in (source_envelope, source_ledger)
        },
        script_sha256=digest(Path(__file__)),
    )
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
