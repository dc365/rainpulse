"""A recovered proof overlay must bind RAW, published QC and diagnostic bytes."""

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


@pytest.mark.parametrize(
    "kind",
    ["good", "authority", "snapshot", "published", "diagnostic", "unknown", "count"],
)
def test_footprint_overlay_binding(tmp_path, kind):
    raw = np.full((2, 2), 15, "float32")
    snapshot, published = tmp_path / "raw.npz", tmp_path / "published.json"
    np.savez(snapshot, RAW=raw)
    published.write_text("{}")
    diagnostic = tmp_path / "evidence.npz"
    np.savez(diagnostic, RECOVERED_MASK=np.ones((2, 2), "uint8"))
    record = dict(
        snapshot_sha256=m.digest(snapshot),
        published_receipt_sha256=m.digest(published),
        action_authority=False,
        recovered_gates=4,
        diagnostics=str(diagnostic),
        diagnostics_sha256=m.digest(diagnostic),
    )
    report = dict(
        scope="complete_original_source_replay_not_published_QC",
        action_authority=False,
        product_writes=False,
        results=[record],
    )
    if kind == "authority":
        report["action_authority"] = True
    if kind == "snapshot":
        record["snapshot_sha256"] = "a" * 64
    if kind == "published":
        record["published_receipt_sha256"] = "a" * 64
    if kind == "diagnostic":
        record["diagnostics_sha256"] = "a" * 64
    if kind == "unknown":
        raw[0, 0] = np.nan
    if kind == "count":
        record["recovered_gates"] = 3
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    if kind == "good":
        assert m.footprint_overlay(snapshot, published, path, raw).sum() == 4
    else:
        with pytest.raises(ValueError):
            m.footprint_overlay(snapshot, published, path, raw)
