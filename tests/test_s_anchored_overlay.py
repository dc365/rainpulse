"""Bound proposal images cannot masquerade as published QC removals."""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location(
    "s_render", Path(__file__).resolve().parents[1] / "scripts/render_s_qc_review.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


@pytest.mark.parametrize("method", ["anchored-shape", "original-fan"])
@pytest.mark.parametrize("kind", ["good", "authority", "snapshot", "published", "diagnostic",
                                  "unknown", "count", "unowned", "ambiguous", "method"])
def test_anchored_overlay_binding(tmp_path, kind, method):
    raw = np.full((2, 2), 15, "float32")
    snapshot, published = tmp_path / "raw.npz", tmp_path / "published.json"
    np.savez(snapshot, RAW=raw)
    published.write_text("{}")
    diagnostic = tmp_path / "evidence.npz"
    owner = np.full((2, 2), 7, "uint32")
    ambiguous = np.zeros((2, 2), "uint8")
    if kind == "unowned":
        owner[0, 0] = 0
    if kind == "ambiguous":
        ambiguous[0, 0] = 1
    prefix = "RV2_ORIGINAL_FAN_SHAPE_" if method == "original-fan" else "RV2_ANCHORED_RADIAL_SHAPE_"
    np.savez(diagnostic, **{prefix + "QUALIFIED_MASK": np.ones((2, 2), "uint8"),
                           prefix + "SOURCE_ID": owner, prefix + "AMBIGUOUS_MASK": ambiguous})
    record = dict(snapshot_sha256=m.digest(snapshot),
                  published_receipt_sha256=m.digest(published), action_authority=False,
                  qualified_gates=4, diagnostics=str(diagnostic),
                  diagnostics_sha256=m.digest(diagnostic))
    report = dict(scope="original_RAW_boundary_research_not_published_QC", method=method,
                  action_authority=False, product_writes=False, results=[record])
    if kind == "authority":
        report["action_authority"] = True
    if kind == "method":
        report["method"] = "original-boundary"
    if kind in ("snapshot", "published", "diagnostic"):
        record[{"snapshot": "snapshot_sha256", "published": "published_receipt_sha256",
                "diagnostic": "diagnostics_sha256"}[kind]] = "a"*64
    if kind == "unknown":
        raw[0, 0] = np.nan
    if kind == "count":
        record["qualified_gates"] = 3
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    if kind == "good":
        assert m.anchored_overlay(snapshot, published, path, raw, method=method).sum() == 4
    else:
        with pytest.raises(ValueError):
            m.anchored_overlay(snapshot, published, path, raw, method=method)
