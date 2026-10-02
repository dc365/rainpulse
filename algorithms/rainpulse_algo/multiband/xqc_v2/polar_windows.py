"""Original joint polar distributions in physical windows; candidate only."""

from dataclasses import dataclass
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from rainpulse_algo.radar.qc_engine.volume_review.data import (
    ResourceLimit,
    checked_mask,
    json_bytes,
)
from rainpulse_algo.radar.qc_engine.volume_review.geometry import wrap

from .polar_morphology import _angular_runs


class WindowPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["x-polar-window-objects-20261003-v1"] = "x-polar-window-objects-20261003-v1"
    scale_m: float = Field(default=5000.0, ge=2000.0, le=20000.0)
    minimum_range_m: float = Field(default=1500.0, ge=500.0)
    minimum_span_m: float = Field(default=20000.0, ge=20000.0)
    minimum_windows: int = Field(default=4, ge=4, strict=True)
    minimum_support_fraction: float = Field(default=0.4, ge=0.4, le=1.0)
    minimum_known_fraction: float = Field(default=0.8, ge=0.8, le=1.0)
    minimum_reference_fraction: float = Field(default=0.8, ge=0.8, le=1.0)
    maximum_rhohv: float = Field(default=0.8, gt=0.0, le=0.9)
    minimum_snr_db: float = Field(default=12.0, ge=8.0, le=30.0)
    maximum_abs_zdr_db: float = Field(default=8.0, ge=5.0, le=10.0)
    maximum_dbzh: float = Field(default=35.0, ge=25.0, le=45.0)
    maximum_width_deg: float = Field(default=45.0, gt=0.0, le=60.0)
    boundary_tolerance_rays: float = Field(default=1.05, gt=0.0, le=1.5)
    minimum_fan_range_ratio: float = Field(default=1.7, ge=1.5)
    maximum_sweep_gates: int = Field(default=2000000, ge=1, le=2000000, strict=True)
    maximum_work: int = Field(default=50000000, ge=1, le=50000000, strict=True)
    maximum_objects: int = Field(default=20000, ge=1, le=20000, strict=True)
    maximum_evidence_bytes: int = Field(default=4 * 1024**2, ge=4096, le=16 * 1024**2, strict=True)


@dataclass(frozen=True)
class WindowEvidence:
    mask: np.ndarray
    record: dict


def detect(sweep, policy, *, protected=None):
    p = WindowPolicy.model_validate(policy)
    shape = sweep.shape
    if np.prod(shape) > p.maximum_sweep_gates:
        raise ResourceLimit("polar window gate budget exceeded")
    hard = (
        np.zeros(shape, bool)
        if protected is None
        else checked_mask(protected, shape, "polar window protection")
    )
    work = 0

    def charge(amount):
        nonlocal work
        work += int(amount)
        if work > p.maximum_work:
            raise ResourceLimit("polar window work budget exceeded")

    charge(np.prod(shape) * 6)
    z, az = sweep.moment("DBZH")
    sn, ass = sweep.moment("SNR")
    rho, ar = sweep.moment("RHOHV")
    zdr, ad = sweep.moment("ZDR")
    known = az & ass & ar & ad
    joint = (
        known
        & (z >= 5)
        & (sn >= p.minimum_snr_db)
        & (rho <= p.maximum_rhohv)
        & (abs(zdr) < p.maximum_abs_zdr_db)
        & ((zdr < -1) | (zdr > 4))
        & ~hard
        & (sweep.ranges[None, :] >= p.minimum_range_m)
    )
    da = (np.roll(sweep.azimuth, -1) - sweep.azimuth) % 360
    edges = ~sweep.gap_after & sweep.good & np.roll(sweep.good, -1)
    mask = np.zeros(shape, bool)
    records = []
    reviews = []
    evidence_bytes = 0
    if not edges.any():
        return WindowEvidence(
            mask, {"status": "GEOMETRY_UNAVAILABLE", "diagnostic_only": True, "work": work}
        )
    spacing = float(np.median(da[edges]))
    buckets = np.floor(sweep.ranges / p.scale_m).astype(int)
    tracks = []
    nodes = 0

    def reference(row, cols):
        charge(len(cols) * 4)
        supported = known[row, cols] & ~hard[row, cols]
        fraction = float(supported.mean())
        if fraction < p.minimum_known_fraction:
            return {"known_fraction": fraction, "ordinary_polar": False}
        rr = float(np.median(rho[row, cols][supported]))
        dd = float(np.median(zdr[row, cols][supported]))
        ss = float(np.median(sn[row, cols][supported]))
        return {
            "known_fraction": fraction,
            "rhohv_median": rr,
            "zdr_median_db": dd,
            "snr_median_db": ss,
            "ordinary_polar": bool(
                rr > p.maximum_rhohv and -1 <= dd <= 4 and ss >= p.minimum_snr_db
            ),
        }

    def finish(track):
        nonlocal evidence_bytes
        history = track["history"]
        first = history[0]
        begin = float(sweep.ranges[first["cols"][0]])
        end = float(sweep.ranges[history[-1]["cols"][-1]])
        span = end - begin + sweep.dr
        excursion = max(
            abs(float(wrap(e[side] - first[side])))
            / max((e[side + "_spacing"] + first[side + "_spacing"]) / 2, 1e-6)
            for e in history
            for side in ("left", "right")
        )
        support = sum(e["support_m"] for e in history)
        bilateral = min(
            sum(e["sides"][side]["ordinary_polar"] for e in history) / len(history)
            for side in (0, 1)
        )
        fan = first["width"] > 3 * spacing
        qualifies = (
            not track["ambiguous"]
            and len(history) >= p.minimum_windows
            and span >= p.minimum_span_m
            and support >= p.minimum_support_fraction * span
            and min(e["known_fraction"] for e in history) >= p.minimum_known_fraction
            and max(e["width"] for e in history) <= p.maximum_width_deg
            and excursion <= p.boundary_tolerance_rays
            and bilateral >= p.minimum_reference_fraction
            and (not fan or end / max(begin, sweep.dr) >= p.minimum_fan_range_ratio)
        )
        record = {
            "track_id": track["track_id"],
            "parent_track_ids": track["parents"],
            "range_begin_m": begin,
            "range_end_m": end,
            "span_m": span,
            "support_m": support,
            "windows": len(history),
            "maximum_width_deg": max(e["width"] for e in history),
            "boundary_excursion_native_footprints": excursion,
            "bilateral_reference_fraction": bilateral,
            "ambiguous": track["ambiguous"],
            "qualified": bool(qualifies),
            "history": [{k: v for k, v in e.items() if k not in ("rows", "cols")} for e in history],
        }
        if len(records) + len(reviews) >= p.maximum_objects:
            raise ResourceLimit("polar window evidence object budget exceeded")
        charge(len(history))
        evidence_bytes += len(json_bytes(record))
        if evidence_bytes > p.maximum_evidence_bytes:
            raise ResourceLimit("polar window evidence byte budget exceeded")
        if qualifies:
            for e in history:
                charge(len(e["rows"]) * len(e["cols"]))
                ix = np.ix_(e["rows"], e["cols"])
                mask[ix] |= joint[ix] & (z[ix] < p.maximum_dbzh)
            records.append(record)
        else:
            reviews.append(record)

    for bucket in np.unique(buckets):
        cols = np.flatnonzero(buckets == bucket)
        charge(shape[0] * len(cols) * 3)
        counts = joint[:, cols].sum(axis=1)
        # A weak original predecessor remains in history. Qualification's
        # support fraction must never filter original measurement windows.
        occupied = counts * sweep.dr >= max(500.0, 2 * sweep.dr)
        occupied &= sweep.good
        entries = []
        envelopes = [np.arange(shape[0])] if occupied.all() else _angular_runs(occupied, sweep)
        for rows in envelopes:
            nodes += 1
            if nodes > p.maximum_objects:
                raise ResourceLimit("polar window node budget exceeded")
            leftrow = (int(rows[0]) - 1) % shape[0]
            rightrow = (int(rows[-1]) + 1) % shape[0]
            left = float(sweep.azimuth[rows[0]] - da[leftrow] / 2)
            width = float(
                (sweep.azimuth[rows[-1]] - sweep.azimuth[rows[0]]) % 360
                + (da[leftrow] + da[rows[-1]]) / 2
            )
            if occupied.all():
                width = 360.0
            ix = np.ix_(rows, cols)
            entry = {
                "node_id": nodes,
                "bucket": int(bucket),
                "rows": rows,
                "cols": cols,
                "left": left,
                "right": left + width,
                "width": width,
                "left_spacing": float(da[leftrow]),
                "right_spacing": float(da[rows[-1]]),
                "center": left + width / 2,
                "support_m": float(joint[ix].any(axis=0).sum() * sweep.dr),
                "known_fraction": float(known[ix].mean()),
                "protected_gates": int(hard[ix].sum()),
                "sides": [reference(leftrow, cols), reference(rightrow, cols)],
            }
            entries.append(entry)
        links = []
        for entry in entries:
            matches = []
            for i, track in enumerate(tracks):
                charge(1)
                previous = track["history"][-1]
                delta = abs(float(wrap(entry["center"] - previous["center"])))
                if delta < (entry["width"] + previous["width"]) / 2 + spacing * 0.1:
                    matches.append(i)
            links.append(matches)
        used = set()
        updated = []
        for entry, matches in zip(entries, links):
            if len(matches) == 1 and sum(matches[0] in m for m in links) == 1:
                track = tracks[matches[0]]
                track["history"].append(entry)
                track["ambiguous"] |= bool(entry["protected_gates"])
                used.add(matches[0])
                updated.append(track)
            else:
                # No new fragment may escape a conflicting original parent.
                for i in matches:
                    tracks[i]["ambiguous"] = True
                # Parents close below with their COMPLETE measurements. A
                # persistent rejection and explicit ancestry links retain the
                # conflict without exponentially copying branch histories.
                updated.append(
                    {
                        "history": [entry],
                        "track_id": entry["node_id"],
                        "parents": [tracks[i]["track_id"] for i in matches],
                        "ambiguous": bool(matches or entry["protected_gates"]),
                    }
                )
        for i, track in enumerate(tracks):
            if i not in used:
                finish(track)
        tracks = updated
    for track in tracks:
        finish(track)
    return WindowEvidence(
        mask,
        {
            "status": "EVALUATED",
            "version": p.version,
            "diagnostic_only": True,
            "source_verified": False,
            "weather_truth": False,
            "work": work,
            "qualified_gates": int(mask.sum()),
            "objects": records,
            "review_objects": reviews,
            "original_nodes": nodes,
            "evidence_bytes": evidence_bytes,
        },
    )
