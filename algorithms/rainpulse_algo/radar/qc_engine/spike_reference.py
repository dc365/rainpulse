"""Read-only import boundary for a separately executed native RADVOL-QC baseline.

No copied SPIKE algorithm, guessed native API, interpolated reflectivity, or
ray-QI-to-gate-truth conversion is permitted here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np


def load_native_spike_reference(metadata_path, *, expected_input_sha256, shape):
    path = Path(metadata_path)
    data = json.loads(path.read_text())
    if data.get("algorithm") != "RADVOL-QC-SPIKE":
        raise ValueError("reference is not the native SPIKE algorithm")
    if data.get("input_sha256") != expected_input_sha256:
        raise ValueError("native SPIKE reference has different source input")
    if not data.get("source_revision") or not data.get("parameters_sha256"):
        raise ValueError("native SPIKE reference lacks reproducible version/parameters")
    if data.get("interpolated_values_used") is not False:
        raise ValueError("interpolated SPIKE reflectivity is not a native observation")
    artifact = (path.parent / data["mask_file"]).resolve()
    if not artifact.is_relative_to(path.parent.resolve()):
        raise ValueError("reference artifact escapes its directory")
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != data.get("mask_sha256"):
        raise ValueError("native SPIKE reference mask hash differs")
    mask = np.load(artifact, allow_pickle=False)
    if np.any(~np.isin(mask, [0, 1])):
        raise ValueError("reference mask must be binary")
    if data.get("granularity") == "ray":
        if mask.shape != (shape[0],):
            raise ValueError("ray-level reference geometry differs")
        return {"granularity": "ray", "mask": mask, "gate_metrics_permitted": False}
    if data.get("granularity") == "gate" and data.get("native_gate_mask_verified") is True:
        if mask.shape != shape:
            raise ValueError("gate-level reference geometry differs")
        return {"granularity": "gate", "mask": mask, "gate_metrics_permitted": True}
    raise ValueError("unverified reference granularity; ray QI cannot establish gate labels")
