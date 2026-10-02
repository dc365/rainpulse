"""Render only input-bound non-actionable temporal evidence."""

import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "s_render", Path(__file__).resolve().parents[1] / "scripts/render_s_qc_review.py"
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixture(tmp_path):
    raw = np.full((2, 2), 15, "float32")
    snapshot = tmp_path / "raw.npz"
    np.savez(snapshot, RAW=raw)
    diagnostic = tmp_path / "evidence.npz"
    masks = {
        "ST_" + k + "_MASK": np.ones((2, 2), "uint8")
        for k in ("MATCH", "CANDIDATE", "MEASURED", "BOUNDARY_SUPPORTED")
    }
    np.savez(diagnostic, **masks)
    record = dict(
        snapshot_sha256=m.digest(snapshot),
        action_authority=False,
        matched_gates=4,
        diagnostics=str(diagnostic),
        diagnostics_sha256=m.digest(diagnostic),
    )
    report = dict(
        scope="past_original_seed_recurrence_not_published_QC_delta",
        action_authority=False,
        product_writes=False,
        results=[record],
    )
    return raw, snapshot, diagnostic, masks, report


@pytest.mark.parametrize(
    "kind", ["good", "authority", "snapshot", "diagnostic", "unknown", "no_candidate"]
)
def test_temporal_overlay_binding(tmp_path, kind):
    raw, snapshot, diagnostic, masks, report = fixture(tmp_path)
    if kind == "authority":
        report["action_authority"] = True
    if kind == "snapshot":
        report["results"][0]["snapshot_sha256"] = "a" * 64
    if kind == "diagnostic":
        report["results"][0]["diagnostics_sha256"] = "a" * 64
    if kind == "unknown":
        raw[0, 0] = np.nan
    if kind == "no_candidate":
        masks["ST_CANDIDATE_MASK"][0, 0] = 0
        np.savez(diagnostic, **masks)
        report["results"][0]["diagnostics_sha256"] = m.digest(diagnostic)
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    if kind == "good":
        assert m.temporal_overlay(snapshot, path, raw).sum() == 4
    else:
        with pytest.raises(ValueError):
            m.temporal_overlay(snapshot, path, raw)
