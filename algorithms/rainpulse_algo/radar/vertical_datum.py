"""Shared vertical-datum compatibility and evidence-status policy."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


EGM2008_DATUMS = frozenset({"EPSG:3855", "EGM2008", "EGM2008HEIGHT"})
DATUM_STATUSES = frozenset(
    {"unverified_engineering", "converted_literature_offset", "verified_egm2008"}
)


def normalized_datum(value: object) -> str:
    return str(value).strip().upper().replace(" ", "")


def vertical_datum_status(
    site: Mapping[str, object],
    *,
    vertical_crs: str = "EPSG:3855",
) -> str:
    """Return a fail-closed status for a radar site's vertical datum.

    A compatible CRS alone is not verification. Explicit evidence status is
    required before geometry consumers may treat the height as verified.
    """
    datum = site.get("altitude_datum")
    if datum is None:
        return "unverified_engineering"
    if normalized_datum(datum) not in EGM2008_DATUMS:
        return f"incompatible_with_{vertical_crs.lower().replace(':', '_')}"

    status = site.get("altitude_datum_status")
    if status is None:
        return "unverified_engineering"
    status = str(status)
    if status not in DATUM_STATUSES:
        return "invalid_altitude_datum_status"
    if status != "verified_egm2008":
        return status

    evidence = site.get("altitude_evidence")
    sigma = site.get("altitude_sigma_m")
    try:
        sigma_value = float(sigma)
    except (TypeError, ValueError):
        return "verification_evidence_missing"
    if not isinstance(evidence, str) or not evidence.strip() or not isfinite(sigma_value) or sigma_value < 0:
        return "verification_evidence_missing"
    return "verified_egm2008"
