"""Strict RDD reference-result boundary, not a fabricated RDD reimplementation.

The accessible publisher abstract names CZ/CD/ME/SE but does not supply their
formulae or the decision tree. RDD remains unavailable unless a frozen external
result is provided. Its absence is never interpreted as a negative detection.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .afl import PaperEvidence

RDD_DOI = "10.16032/j.issn.1004-4965.2022.073"


def geometry_sha256(native) -> str:
    digest = hashlib.sha256()
    # Native source ordering is part of identity, including sorted-to-source map.
    for name, array, dtype in (
        ("azimuth", native.azimuth, "<f8"),
        ("elevation", native.elevation, "<f8"),
        ("range", native.ranges, "<f8"),
        ("original_indices", native.original_indices, "<i8"),
    ):
        value = np.ascontiguousarray(array, dtype=dtype)
        digest.update(name.encode() + str(value.shape).encode() + value.tobytes())
    return digest.hexdigest()


def unavailable_rdd(shape) -> PaperEvidence:
    return PaperEvidence(
        {
            "RDD_AVAILABLE_MASK": np.zeros(shape, "uint8"),
            "RDD_CANDIDATE_MASK": np.zeros(shape, "uint8"),
        },
        {
            "algorithm": "RDD-external-reference",
            "source_doi": RDD_DOI,
            "status": "not_executed_no_verified_reference",
            "author_reproduction": False,
            "reason": "Full formulae and decision-tree source unavailable; no invented substitute.",
        },
    )


def load_rdd_reference(
    path: Path, *, metadata_sha256: str, input_sha256: str, native, sweep_name: str
) -> PaperEvidence:
    path = Path(path)
    if path.is_symlink() or path.stat().st_size > 100000:
        raise ValueError("invalid RDD metadata file")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != metadata_sha256:
        raise ValueError("RDD metadata SHA differs from manifest")
    meta = json.loads(content)
    checks = {
        "schema_version": "rainpulse.rdd-reference.v1",
        "algorithm": "RDD",
        "source_doi": RDD_DOI,
        "input_sha256": input_sha256,
        "geometry_sha256": geometry_sha256(native),
        "sweep": sweep_name,
        "ordering": "source_ray_gate",
        "granularity": "gate",
        "interpolated": False,
    }
    for key, expected in checks.items():
        if meta.get(key) != expected:
            raise ValueError(f"RDD reference {key} differs from the frozen input")
    for key in ("implementation_revision", "parameters_sha256", "review_record"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            raise ValueError(f"RDD reference requires explicit {key}")
    for key in ("parameters_sha256", "mask_sha256"):
        if len(meta.get(key, "")) != 64 or any(c not in "0123456789abcdef" for c in meta[key]):
            raise ValueError(f"RDD reference requires a SHA256 for {key}")
    relative = Path(meta["mask_file"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("RDD mask path must be confined to its metadata directory")
    file = path.parent / relative
    if any(p.is_symlink() for p in (file, *file.parents)):
        raise ValueError("RDD reference links are not permitted")
    if file.stat().st_size > np.prod(native.shape) * 8 + 100000:
        raise ValueError("RDD reference exceeds gate budget")
    if hashlib.sha256(file.read_bytes()).hexdigest() != meta["mask_sha256"]:
        raise ValueError("RDD reference mask checksum differs")
    # npy, not pickled Python or a compressed archive with unbounded inflation.
    values = np.load(file, allow_pickle=False, mmap_mode="r")
    if values.shape != (*native.shape, 2) or values.dtype != np.dtype("uint8"):
        raise ValueError("RDD reference requires uint8 [ray,gate,(available,candidate)]")
    values = np.asarray(values)[native.original_indices]
    if not np.isin(values, [0, 1]).all():
        raise ValueError("RDD masks must be binary")
    available, candidate = values[..., 0] == 1, values[..., 1] == 1
    observed = native.field_available["DBZH"]
    if np.any(available & ~observed) or np.any(candidate & ~available):
        raise ValueError("RDD reference cannot turn missing or unavailable gates into detections")
    return PaperEvidence(
        {
            "RDD_AVAILABLE_MASK": available.astype("uint8"),
            "RDD_CANDIDATE_MASK": candidate.astype("uint8"),
        },
        {
            "algorithm": "RDD-external-reference",
            "source_doi": RDD_DOI,
            "status": "external_reference_loaded",
            "author_reproduction": False,
            "reference_metadata_sha256": metadata_sha256,
            "implementation_revision": meta["implementation_revision"],
            "parameters_sha256": meta["parameters_sha256"],
            "review_record": meta["review_record"],
            "mask_semantics": "external_detection_not_automatically_final_reject",
            "provenance_note": (
                "Hash validation checks identity, not authenticity of author's code."
            ),
        },
    )
