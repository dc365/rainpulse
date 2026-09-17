"""Prior identities and geometry are explicit and independently checkable."""
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np


def utc(text):
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("UTC-aware timestamps required")
    return value.astimezone(timezone.utc)


def sha_text(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("SHA256 receipt required")
    return value


@dataclass(frozen=True)
class ClearAirSample:
    native: object
    radar_id: str
    partition_id: str
    scan_id: str
    observed_at_utc: str
    input_sha256: str
    review_receipt_sha256: str
    reviewed_clear_air: bool
    valid_no_echo: np.ndarray

    def validate(self):
        sha_text(self.input_sha256)
        sha_text(self.review_receipt_sha256)
        utc(self.observed_at_utc)
        if self.reviewed_clear_air is not True or not all((self.scan_id, self.radar_id, self.partition_id)):
            raise ValueError("identified, reviewed clear-air samples required")
        a = np.asarray(self.valid_no_echo)
        if a.shape != self.native.shape or not np.isin(a, [0, 1]).all():
            raise ValueError("explicit valid-no-echo mask required")


def align_rows(reference_azimuth, reference_elevation, current, tolerance_fraction=0.4):
    """One-to-one matches only. No interpolated prior in unobserved sectors."""
    az = np.asarray(reference_azimuth)
    if len(az) < 2:
        tolerance = 0.1
    else:
        steps = np.diff(np.sort(az % 360))
        steps = steps[steps > 1e-4]
        tolerance = float(np.median(steps)) * tolerance_fraction if len(steps) else 0.1
    diff = abs((np.asarray(current.azimuth)[:, None] - az[None, :] + 180) % 360 - 180)
    nearest = np.argmin(diff, axis=1)
    valid = diff[np.arange(len(nearest)), nearest] <= tolerance
    valid &= abs(current.elevation - np.asarray(reference_elevation)[nearest]) <= 0.1
    valid &= current.geometry_good
    indices, count = np.unique(nearest[valid], return_counts=True)
    ambiguous = indices[count > 1]
    valid &= ~np.isin(nearest, ambiguous)
    return nearest, valid
