from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import zarr

from .qc import (
    BasicQCProfile,
    QCInputError,
    _detect_radial_interference,
    _volume_higher_elevation_radial_extents,
    _volume_vertical_consistency,
)
from .qc_input import open_qc_input


@dataclass(frozen=True)
class RadialCandidateSweep:
    name: str
    elevation_deg: float
    azimuth_deg: np.ndarray
    candidate_mask_by_ray: np.ndarray
    observed_mask_by_ray: np.ndarray
    hard_flag_by_ray: np.ndarray
    comparison_digest: str
    geometry_digest: str


def extract_radial_candidate_sweeps(
    normalized_objects: dict[str, bytes],
    profile: BasicQCProfile,
) -> dict[str, RadialCandidateSweep]:
    root = open_qc_input(normalized_objects).root
    if root.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise QCInputError("radial context input is not a normalized radar volume")

    vertical_probabilities = _volume_vertical_consistency(root, profile)
    higher_elevation_extents = _volume_higher_elevation_radial_extents(root, profile)
    sweeps: dict[str, RadialCandidateSweep] = {}
    threshold = profile.radial_interference.morphology.diagnostic_probability

    for sweep_number in root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        group = root[name]
        azimuth = group["azimuth"][:].astype("float32", copy=False)
        if "DBZH" in group:
            dbzh = group["DBZH"][:].astype("float32", copy=False)
        else:
            dbzh = np.full((len(azimuth), len(group["range"])), np.nan, dtype="float32")
        valid = np.isfinite(dbzh)
        lower, upper = profile.echo.dbzh_valid_range_dbz
        valid &= ~((dbzh < lower) | (dbzh > upper))
        detection = _detect_radial_interference(
            dbzh,
            valid,
            profile.radial_interference,
            ranges_m=group["range"][:],
            azimuth_deg=azimuth,
            vertical_consistency=(
                vertical_probabilities.get(name)
                if profile.vertical_consistency.mode == "radial_evidence"
                else None
            ),
            higher_elevation_extent_fraction=higher_elevation_extents.get(name),
        )
        candidate_mask_by_ray = detection.candidate_ray_mask
        if candidate_mask_by_ray is None:
            candidate_mask_by_ray = np.any(
                np.nan_to_num(detection.probability, nan=0.0) >= threshold,
                axis=1,
            )
        observed_mask_by_ray = detection.observed_ray_mask
        if observed_mask_by_ray is None:
            observed_mask_by_ray = np.any(valid, axis=1)
        sweeps[name] = RadialCandidateSweep(
            name=name,
            elevation_deg=float(np.nanmedian(group["elevation"][:]))
            if "elevation" in group
            else np.nan,
            azimuth_deg=azimuth,
            candidate_mask_by_ray=np.asarray(candidate_mask_by_ray, dtype=bool),
            observed_mask_by_ray=np.asarray(observed_mask_by_ray, dtype=bool),
            hard_flag_by_ray=np.any(
                np.nan_to_num(detection.probability, nan=0.0)
                >= profile.radial_interference.flag_probability,
                axis=1,
            ),
            comparison_digest=comparison_digest_for_sweep(group),
            geometry_digest=_geometry_digest(group),
        )
    return sweeps


def comparison_digest_for_sweep(group: zarr.Group) -> str:
    return _digest_for_arrays(group, ("elevation", "range"))


def geometry_digest_for_sweep(group: zarr.Group) -> str:
    return _geometry_digest(group)


def volume_geometry_digest(root: zarr.Group) -> str:
    digest = hashlib.sha256()
    for sweep_number in root["sweep_number"][:]:
        name = f"sweep_{int(sweep_number):03d}"
        if name not in root:
            continue
        digest.update(name.encode())
        digest.update(_geometry_digest(root[name]).encode())
    return digest.hexdigest()


def _geometry_digest(group: zarr.Group) -> str:
    return _digest_for_arrays(group, ("azimuth", "elevation", "range"))


def _digest_for_arrays(group: zarr.Group, names: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for name in names:
        if name not in group:
            continue
        values = group[name][:].astype("float32", copy=False)
        digest.update(name.encode())
        digest.update(values.tobytes())
    return digest.hexdigest()
