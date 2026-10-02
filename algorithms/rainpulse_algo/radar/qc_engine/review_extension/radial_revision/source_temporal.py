"""Causal recurrence on original seed cells; diagnostic only, never QC authority."""

from dataclasses import dataclass

import numpy as np

from ...volume_review.clutter_fusion.context import ground, height, sample_ground
from ...volume_review.data import ResourceLimit, array_digest, checked_mask, frozen


@dataclass(frozen=True)
class SourceFrame:
    sweep: object
    seed_id: np.ndarray
    source_hold: np.ndarray
    blocked: np.ndarray
    radar_id: str
    scan_id: str
    source_sha256: str
    lineage_id: str

    def __post_init__(self):
        if not self.radar_id or not self.scan_id or not self.lineage_id:
            raise ValueError("identified original source frame required")
        if len(self.source_sha256) != 64 or any(
            c not in "0123456789abcdef" for c in self.source_sha256
        ):
            raise ValueError("original content SHA required")
        shape = self.sweep.shape
        if (
            self.seed_id.shape != shape
            or self.seed_id.dtype != np.dtype("uint32")
            or self.source_hold.shape != shape
            or self.source_hold.dtype != np.dtype("uint16")
            or np.any(self.source_hold > 7)
            or np.any((self.seed_id == 0) & (self.source_hold != 0))
        ):
            raise ValueError("invalid original seed identity/hold")
        blocked = checked_mask(self.blocked, shape, "original source protection")
        _, observed = self.sweep.moment("DBZH")
        if np.any((self.seed_id > 0) & (~observed | blocked | ~self.sweep.good[:, None])):
            raise ValueError("original seed crossed observation/protection")
        for key, value in (
            ("seed_id", self.seed_id),
            ("source_hold", self.source_hold),
            ("blocked", blocked),
        ):
            object.__setattr__(self, key, frozen(value))

    @property
    def digest(self):
        return array_digest(
            {
                **self.sweep.arrays(),
                "seed": self.seed_id,
                "source_hold": self.source_hold,
                "blocked": self.blocked,
            }
        )


def weather(frame):
    rho, ra = frame.sweep.moment("RHOHV")
    snr, sa = frame.sweep.moment("SNR")
    return frame.blocked | (ra & sa & (rho >= 0.95) & (snr >= 10))


def diagnose(current, previous, *, maximum_pairs=8000000):
    """Use latest actual DBZH observation, including its conflicts and unknown SNR.

    The nearest original donor cell must contain an original seed itself. Neither
    previously linked tails nor this function's matches can supply more seeds.
    All RAW targets are measured before any caller selects residual/isolated ROIs.
    """
    s = current.sweep
    if (
        len(previous) > 4
        or np.prod(s.shape) > 2000000
        or sum(np.prod(p.sweep.shape) for p in previous) > 8000000
    ):
        raise ResourceLimit("source temporal whole-input budget")
    z, observed = s.moment("DBZH")
    snr, sa = s.moment("SNR")
    edge = s.gap_after | np.roll(s.gap_after, 1)
    candidate = observed & sa & s.good[:, None] & ~edge[:, None] & ~weather(current)
    candidate &= (current.seed_id == 0) & (z >= -5) & (snr >= 3) & (s.ranges[None, :] >= 10000)
    rows, gates = np.where(candidate)
    if len(rows) * len(previous) > maximum_pairs:
        raise ResourceLimit("source temporal pairing budget")
    out = {
        "ST_" + k + "_MASK": np.zeros(s.shape, "uint8")
        for k in ("MEASURED", "SNR_MEASURED", "MATCH", "BOUNDARY_SUPPORTED")
    }
    out["ST_CANDIDATE_MASK"] = candidate.astype("uint8")
    out.update({"ST_" + k: np.full(s.shape, -1, "int32") for k in ("SOURCE_INDEX", "RAY", "GATE")})
    out["ST_ORIGINAL_SOURCE_ID"] = np.zeros(s.shape, "uint32")
    out.update(
        {
            "ST_" + k: np.full(s.shape, np.nan, "float32")
            for k in (
                "AGE_S",
                "DBZH_DELTA_DB",
                "SNR_DELTA_DB",
                "HORIZONTAL_ERROR_M",
                "VERTICAL_ERROR_M",
            )
        }
    )
    report = dict(
        version="original-seed-recurrence-v1",
        actions=0,
        action_authority=False,
        source_agreement_is_pollution_truth=False,
        recursive_growth=False,
        current_raw_sha256=current.digest,
        current_source_sha256=current.source_sha256,
        current_scan_id=current.scan_id,
        current_lineage_id=current.lineage_id,
        rules=dict(
            maximum_age_s=1200,
            maximum_horizontal_error_m=1500,
            maximum_vertical_error_m=250,
            maximum_elevation_error_deg=0.1,
            minimum_target_range_m=10000,
            minimum_snr_db=3,
            maximum_dbzh_change_db=3,
            maximum_snr_change_db=3,
            donor_scope="original_seed_cell_only",
            newest_observation_conflict_retained=True,
        ),
        sources=[],
        rejected={},
    )
    eligible, seen = [], {}
    for p in sorted(previous, key=lambda p: (p.scan_id, p.sweep.name, p.source_sha256)):
        d, reason = p.sweep, None
        if p.radar_id.lower() != current.radar_id.lower() or p.lineage_id != current.lineage_id:
            reason = "IDENTITY_MISMATCH"
        elif p.scan_id == current.scan_id:
            reason = "SELF_REFERENCE"
        elif s.ray_time_s is None or d.ray_time_s is None:
            reason = "TIME_UNAVAILABLE"
        elif d.ray_time_s.max() >= s.ray_time_s.min():
            reason = "NOT_STRICTLY_PAST"
        elif s.ray_time_s.min() - d.ray_time_s.max() > 1200:
            reason = "TOO_OLD"
        elif abs(np.median(d.elevation) - np.median(s.elevation)) > 0.1:
            reason = "ELEVATION_MISMATCH"
        if reason:
            report["rejected"][reason] = report["rejected"].get(reason, 0) + 1
            continue
        key, digest = (p.scan_id, d.name), p.digest
        if key in seen:
            if seen[key] != digest:
                raise ValueError("duplicate source identity has changed RAW evidence")
            continue
        seen[key] = digest
        eligible.append(p)
    eligible.sort(key=lambda p: (-float(p.sweep.ray_time_s.max()), p.source_sha256, p.scan_id))
    distance = ground(s.ranges[gates], s.elevation[rows])
    altitude = height(s.ranges[gates], s.elevation[rows])
    taken = np.zeros(len(rows), bool)
    for index, p in enumerate(eligible):
        d = p.sweep
        jj, kk, support, donor_height = sample_ground(d, s.azimuth[rows], distance)
        donor_distance = ground(d.ranges[kk], d.elevation[jj])
        da = np.deg2rad((d.azimuth[jj] - s.azimuth[rows] + 180) % 360 - 180)
        horizontal = np.sqrt(
            np.maximum(
                0,
                (distance - donor_distance) ** 2 + 2 * distance * donor_distance * (1 - np.cos(da)),
            )
        )
        vertical = donor_height - altitude
        age = s.ray_time_s[rows] - d.ray_time_s[jj]
        support &= (horizontal <= 1500) & (abs(vertical) <= 250) & (age > 0) & (age <= 1200)
        support &= abs(d.elevation[jj] - s.elevation[rows]) <= 0.1
        support &= ~(d.gap_after | np.roll(d.gap_after, 1))[jj]
        dz, za = d.moment("DBZH")
        ds, dsa = d.moment("SNR")
        # Newer measured weather/no-seed/unknown-SNR is retained as a conflict.
        take = support & za[jj, kk] & ~taken
        taken |= take
        delta = z[rows, gates] - dz[jj, kk]
        snr_delta = np.where(dsa[jj, kk], snr[rows, gates] - ds[jj, kk], np.nan)
        match = take & dsa[jj, kk] & (p.seed_id[jj, kk] > 0) & ~weather(p)[jj, kk]
        match &= (abs(delta) <= 3) & (abs(snr_delta) <= 3) & (ds[jj, kk] >= 3)
        ix, mx = (rows[take], gates[take]), (rows[match], gates[match])
        out["ST_MEASURED_MASK"][ix] = 1
        out["ST_SNR_MEASURED_MASK"][ix] = dsa[jj[take], kk[take]]
        out["ST_MATCH_MASK"][mx] = 1
        out["ST_BOUNDARY_SUPPORTED_MASK"][mx] = p.source_hold[jj[match], kk[match]] == 0
        out["ST_ORIGINAL_SOURCE_ID"][mx] = p.seed_id[jj[match], kk[match]]
        for key, value in (
            ("SOURCE_INDEX", np.full(len(rows), index)),
            ("RAY", jj),
            ("GATE", kk),
            ("AGE_S", age),
            ("DBZH_DELTA_DB", delta),
            ("SNR_DELTA_DB", snr_delta),
            ("HORIZONTAL_ERROR_M", horizontal),
            ("VERTICAL_ERROR_M", vertical),
        ):
            out["ST_" + key][ix] = value[take]
        report["sources"].append(
            dict(
                scan_id=p.scan_id,
                source_sha256=p.source_sha256,
                raw_evidence_sha256=p.digest,
                sweep=d.name,
            )
        )
    report.update(
        candidate_gates=len(rows),
        measured_gates=int(out["ST_MEASURED_MASK"].sum()),
        matched_gates=int(out["ST_MATCH_MASK"].sum()),
        boundary_supported_gates=int(out["ST_BOUNDARY_SUPPORTED_MASK"].sum()),
    )
    return out, report
