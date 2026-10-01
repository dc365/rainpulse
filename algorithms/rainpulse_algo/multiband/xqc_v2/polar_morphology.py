"""Native station-centred morphology; no RF-source or Doppler claims.

Track the complete connected angular envelope, including its failures. Starting
again at a narrower far-range tail would misclassify constant-km-width rain.
Only observed object members are returned; never interpolate deletion holes.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit, checked_mask
from rainpulse_algo.radar.qc_engine.volume_review.geometry import runs, wrap

MAXIMUM_FLANK_SEARCH_DEG = 3.0


class MorphologyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal["x-polar-morphology-20261001-v1", "x-polar-morphology-20261002-v2"] = (
        "x-polar-morphology-20261001-v1"
    )
    expanding_fans_enabled: bool = False
    local_weather_policy: Literal["protect", "joint_review"] = "protect"
    scales_m: tuple[Annotated[float, Field(ge=2000.0, le=20000.0)], ...] = Field(
        default=(5000.0, 10000.0),
        min_length=1,
        max_length=4,
        json_schema_extra={"uniqueItems": True},
    )
    levels_dbz: tuple[Annotated[float, Field(ge=5.0, le=55.0)], ...] = Field(
        default=(5.0, 15.0, 25.0),
        min_length=1,
        max_length=6,
        json_schema_extra={"uniqueItems": True},
    )
    minimum_echo_dbz: Literal[5.0] = 5.0
    minimum_range_m: float = Field(default=1500.0, ge=500.0)
    minimum_span_m: float = Field(default=20000.0, ge=15000.0)
    minimum_support_fraction: float = Field(default=0.4, ge=0.4, le=1.0)
    minimum_windows: int = Field(default=4, ge=4)
    maximum_width_deg: float = Field(default=45.0, gt=0.0, le=60.0)
    # A thresholded edge can move one native footprint as an adjacent sample
    # appears/disappears. Freeze the template, but account for that sampling
    # uncertainty instead of demanding identical occupied row indices.
    boundary_tolerance_rays: float = Field(default=1.05, gt=0.0, le=1.5)
    flank_contrast_db: float = Field(default=6.0, ge=6.0, le=15.0)
    minimum_flank_fraction: float = Field(default=0.8, ge=0.8, le=1.0)
    quiet_snr_db: float = Field(default=3.0, ge=0.0, le=5.0)
    minimum_fan_range_ratio: float = Field(default=1.7, ge=1.5)
    maximum_unknown_gap_m: float = Field(default=1000.0, ge=0.0, le=1500.0)
    maximum_objects: int = Field(default=20000, ge=1, le=20000, strict=True)
    maximum_sweep_gates: int = Field(default=2000000, ge=1, le=2000000, strict=True)
    maximum_work: int = Field(default=50000000, ge=1, le=50000000, strict=True)

    @model_validator(mode="after")
    def physical_scales(self):
        if self.expanding_fans_enabled and self.version != "x-polar-morphology-20261002-v2":
            raise ValueError("expanding fan geometry requires v2 identity")
        if (
            not self.scales_m
            or not self.levels_dbz
            or len(self.scales_m) > 4
            or len(self.levels_dbz) > 6
            or len(set(self.scales_m)) != len(self.scales_m)
            or len(set(self.levels_dbz)) != len(self.levels_dbz)
            or any(x < 2000 or x > 20000 for x in self.scales_m)
            or any(x < self.minimum_echo_dbz or x > 55 for x in self.levels_dbz)
        ):
            raise ValueError("bounded unique physical scales and reflectivity levels required")
        return self


@dataclass(frozen=True)
class MorphologyEvidence:
    mask: np.ndarray
    object_id: np.ndarray
    objects: list[dict]
    record: dict


def _angular_runs(occupied, sweep):
    """Closed north seam is ordinary adjacency; declared gaps are barriers."""
    if occupied.all():
        return []  # no measured flanks
    start = int(np.flatnonzero(~occupied)[0])
    order = (np.arange(len(occupied)) + start) % len(occupied)
    selected = occupied[order]
    result = []
    for lo, hi in runs(selected):
        rows = order[lo:hi]
        if len(rows) and not sweep.gap_after[rows[:-1]].any():
            left = (int(rows[0]) - 1) % len(occupied)
            right = (int(rows[-1]) + 1) % len(occupied)
            if (
                sweep.good[left]
                and sweep.good[right]
                and not sweep.gap_after[left]
                and not sweep.gap_after[rows[-1]]
            ):
                result.append(rows)
    return result


def detect(sweep, policy, *, protected=None):
    p = MorphologyPolicy.model_validate(policy)
    shape = sweep.shape
    if shape[0] * shape[1] > p.maximum_sweep_gates:
        raise ResourceLimit("morphology native gate budget exceeded")
    z, za = sweep.moment("DBZH")
    sn, sa = sweep.moment("SNR")
    hard = (
        np.zeros(shape, bool)
        if protected is None
        else checked_mask(protected, shape, "morphology protection")
    )
    raw = za & (z >= p.minimum_echo_dbz) & (sweep.ranges[None, :] >= p.minimum_range_m)
    raw &= sweep.good[:, None] & ~hard
    known_native = za | sweep.no_echo | (sa & (sn <= p.quiet_snr_db))
    da = (np.roll(sweep.azimuth, -1) - sweep.azimuth) % 360
    valid_edges = ~sweep.gap_after & sweep.good & np.roll(sweep.good, -1)
    if not valid_edges.any():
        return MorphologyEvidence(
            np.zeros(shape, bool),
            np.zeros(shape, "uint32"),
            [],
            {"status": "GEOMETRY_UNAVAILABLE", "qualified_gates": 0},
        )
    spacing = float(np.median(da[valid_edges]))
    records = []
    reviews = []
    mask = np.zeros(shape, bool)
    ids = np.zeros(shape, "uint32")
    work = 0
    trials = 0

    def charge(amount):
        nonlocal work
        work += int(amount)
        if work > p.maximum_work:
            raise ResourceLimit("morphology work budget exceeded")

    # Measure blind runs once on the native range axis. Repeating this inside
    # buckets would reset a single long gap at every physical window boundary.
    charge(shape[0] * shape[1])
    long_unknown = np.zeros(shape, bool)
    for row in range(shape[0]):
        for lo, hi in runs(~known_native[row]):
            if (hi - lo) * sweep.dr > p.maximum_unknown_gap_m:
                long_unknown[row, lo:hi] = True

    def exterior_flank(start, direction, boundary, cols, strength, active):
        known = np.zeros(len(cols), bool)
        clear = np.zeros(len(cols), bool)
        decided = np.zeros(len(cols), bool)
        row = start
        offset = 0.0
        for _ in range(shape[0]):
            charge(len(cols))
            if abs(float(wrap(sweep.azimuth[row] - boundary))) > MAXIMUM_FLANK_SEARCH_DEG:
                break
            quiet = sa[row, cols] & (sn[row, cols] <= p.quiet_snr_db)
            valid = za[row, cols] | sweep.no_echo[row, cols] | quiet
            blocked = hard[row, cols]
            choose = valid & ~decided & ~blocked
            known[choose] = True
            measured_clear = (za[row, cols] & (z[row, cols] <= strength - p.flank_contrast_db)) | (
                ~za[row, cols] & (quiet | sweep.no_echo[row, cols])
            )
            clear[choose] = measured_clear[choose]
            if (choose & active).any():
                offset = max(offset, abs(float(wrap(sweep.azimuth[row] - boundary))))
            # The first measured shoulder is final, even when it is not quiet.
            # Only an unavailable REF/quiet measurement may look farther out.
            decided |= valid | blocked
            if decided[active].all():
                break
            other = (row + direction) % shape[0]
            edge = row if direction == 1 else other
            if sweep.gap_after[edge] or not sweep.good[other]:
                break
            row = other
        return known, clear, offset

    def finish(track, scale, level):
        entries = track["entries"]
        rows0 = entries[0]["rows"]
        width = entries[0]["width"]
        # Keep a whole changing envelope as a counterexample, not a new far tail.
        excursion = max(
            max(
                abs(float(wrap(e["left"] - entries[0]["left"]))),
                abs(float(wrap(e["right"] - entries[0]["right"]))),
            )
            for e in entries
        )
        normalized_excursion = max(
            abs(float(wrap(e[edge] - entries[0][edge])))
            / max((e[edge + "_spacing"] + entries[0][edge + "_spacing"]) / 2.0, 1e-6)
            for e in entries
            for edge in ("left", "right")
        )
        # Complete original history: a fixed centre with outward widening is
        # distinct from fixed-km weather narrowing with distance. Never restart
        # on a far tail or relax the existing fixed-edge test.
        footprints = np.array([(e["left_spacing"] + e["right_spacing"]) / 2 for e in entries])
        widths = np.array([e["width"] for e in entries])
        centers = np.array([abs(float(wrap(e["center"] - entries[0]["center"]))) for e in entries])
        center_excursion = float(
            np.max(centers / np.maximum((footprints + footprints[0]) / 2, 1e-6))
        )
        narrowing = np.maximum.accumulate(widths) - widths
        normalized_narrowing = float(np.max(narrowing / np.maximum(footprints, 1e-6)))
        growth = float((widths[-1] - widths[0]) / max((footprints[-1] + footprints[0]) / 2, 1e-6))
        expanding = (
            p.expanding_fans_enabled
            and center_excursion <= p.boundary_tolerance_rays + 1e-6
            and normalized_narrowing <= 2 * p.boundary_tolerance_rays + 1e-6
            and growth > 2 * p.boundary_tolerance_rays
            and float(widths.max()) <= p.maximum_width_deg
        )
        cols0 = entries[0]["cols"]
        cols1 = entries[-1]["cols"]
        begin = float(sweep.ranges[cols0[0]])
        end = float(sweep.ranges[cols1[-1]])
        span = end - begin + sweep.dr
        support = sum(e["radial_support_m"] for e in entries)
        known = sum(e["known"] for e in entries)
        clear = sum(e["clear"] for e in entries)
        denom = sum(e["flank_total"] for e in entries)
        per_side_known = np.sum([e["known_sides"] for e in entries], axis=0)
        per_side_clear = np.sum([e["clear_sides"] for e in entries], axis=0)
        side_total = denom / 2.0
        fan = width > 3 * spacing
        qualified = (
            not track["ambiguous"]
            and len(entries) >= p.minimum_windows
            and span >= p.minimum_span_m
            and support >= p.minimum_support_fraction * span
            and (normalized_excursion <= p.boundary_tolerance_rays + 1e-6 or expanding)
            and width <= p.maximum_width_deg
            and np.all(per_side_known >= p.minimum_flank_fraction * side_total)
            and np.all(per_side_clear >= p.minimum_flank_fraction * side_total)
            and (not (fan or expanding) or end / max(begin, sweep.dr) >= p.minimum_fan_range_ratio)
        )
        if not qualified:
            if span >= p.minimum_span_m / 2:
                reviews.append(
                    dict(
                        first_native_ray=int(rows0[0]),
                        last_native_ray=int(rows0[-1]),
                        range_begin_m=begin,
                        range_end_m=end,
                        span_m=span,
                        support_m=support,
                        windows=len(entries),
                        angular_width_deg=width,
                        maximum_boundary_excursion_deg=excursion,
                        maximum_boundary_excursion_native_footprints=normalized_excursion,
                        known_flank_fraction=known / max(denom, 1),
                        minimum_bilateral_known_fraction=float(per_side_known.min())
                        / max(side_total, 1),
                        clear_flank_fraction=clear / max(denom, 1),
                        minimum_bilateral_clear_fraction=float(per_side_clear.min())
                        / max(side_total, 1),
                        maximum_center_excursion_native_footprints=center_excursion,
                        maximum_narrowing_native_footprints=normalized_narrowing,
                        width_growth_native_footprints=growth,
                        expanding_shape_qualified=bool(expanding),
                        ambiguous=track["ambiguous"],
                        scale_m=scale,
                        level_dbz=level,
                    )
                )
            return
        if len(records) >= p.maximum_objects:
            raise ResourceLimit("morphology object budget exceeded")
        oid = len(records) + 1
        members = 0
        for entry in entries:
            rows = entry["rows"]
            cols = entry["window_cols"]
            block = raw[np.ix_(rows, cols)]
            old = mask[np.ix_(rows, cols)]
            mask[np.ix_(rows, cols)] = old | block
            ii = ids[np.ix_(rows, cols)]
            ii[(ii == 0) & block] = oid
            ids[np.ix_(rows, cols)] = ii
            members += int(block.sum())
        kind = (
            "expanding_fan"
            if expanding
            else (
                "fan"
                if fan
                else ("discontinuous_radial" if support < span * 0.85 else "radial_line")
            )
        )
        records.append(
            dict(
                object_id=oid,
                kind=kind,
                scale_m=scale,
                level_dbz=level,
                first_native_ray=int(rows0[0]),
                last_native_ray=int(rows0[-1]),
                range_begin_m=begin,
                range_end_m=end,
                span_m=span,
                support_m=support,
                angular_width_deg=width,
                maximum_boundary_excursion_deg=excursion,
                maximum_boundary_excursion_native_footprints=normalized_excursion,
                windows=len(entries),
                known_flank_fraction=known / max(denom, 1),
                minimum_bilateral_known_fraction=float(per_side_known.min()) / max(side_total, 1),
                clear_flank_fraction=clear / max(denom, 1),
                minimum_bilateral_clear_fraction=float(per_side_clear.min()) / max(side_total, 1),
                maximum_center_excursion_native_footprints=center_excursion,
                maximum_narrowing_native_footprints=normalized_narrowing,
                width_growth_native_footprints=growth,
                member_gates=members,
                maximum_flank_offset_deg=max(e["maximum_flank_offset_deg"] for e in entries),
                source_verified=False,
            )
        )

    for scale in p.scales_m:
        buckets = np.floor(sweep.ranges / scale).astype(int)
        for level in p.levels_dbz:
            tracks = []
            for bucket in np.unique(buckets):
                cols = np.flatnonzero(buckets == bucket)
                charge(len(cols) * shape[0])
                signal = raw[:, cols] & (z[:, cols] >= level)
                blocked = hard[:, cols].any(axis=1)
                # Unknown REF with a measured strong SNR is not measured
                # quiet. Stop long blind intervals even inside a coarse
                # physical window; smaller holes contribute no support and
                # are never painted into the output.
                blocked |= long_unknown[:, cols].any(axis=1)
                occupied = (signal.sum(axis=1) * sweep.dr >= max(500.0, 2 * sweep.dr)) & ~blocked
                entries = []
                for rows in _angular_runs(occupied, sweep):
                    leftrow = (int(rows[0]) - 1) % shape[0]
                    rightrow = (int(rows[-1]) + 1) % shape[0]

                    left = float(sweep.azimuth[rows[0]] - da[leftrow] / 2.0)
                    width = float(
                        ((sweep.azimuth[rows[-1]] - sweep.azimuth[rows[0]]) % 360)
                        + da[leftrow] / 2.0
                        + da[rows[-1]] / 2.0
                    )
                    if width > p.maximum_width_deg:
                        continue
                    block = signal[rows]
                    active = block.any(axis=0)
                    if not active.any():
                        continue
                    native_cols = cols[active]
                    body = np.where(raw[np.ix_(rows, cols)], z[np.ix_(rows, cols)], np.nan)
                    # Maximum supports intensity-varying rays; shoulders still
                    # need independently measured samples at the same ranges.
                    strength = np.max(np.where(np.isfinite(body), body, -np.inf), axis=0)
                    flank = [
                        exterior_flank(leftrow, -1, left, cols, strength, active),
                        exterior_flank(rightrow, 1, left + width, cols, strength, active),
                    ]
                    shoulder_known = np.stack([f[0] for f in flank])
                    shoulder_clear = np.stack([f[1] for f in flank])
                    sample = np.broadcast_to(active, (2, len(cols)))
                    entries.append(
                        dict(
                            rows=rows,
                            cols=native_cols,
                            window_cols=cols,
                            left=left,
                            right=left + width,
                            left_spacing=float(da[leftrow]),
                            right_spacing=float(da[rows[-1]]),
                            width=width,
                            center=left + width / 2.0,
                            bucket=int(bucket),
                            radial_support_m=float(active.sum() * sweep.dr),
                            known_sides=(shoulder_known & sample).sum(axis=1),
                            clear_sides=(shoulder_clear & sample).sum(axis=1),
                            known=int((shoulder_known & sample).sum()),
                            clear=int((shoulder_clear & sample).sum()),
                            flank_total=int(sample.sum()),
                            maximum_flank_offset_deg=max(f[2] for f in flank),
                        )
                    )
                # Associate by previous envelope, retain the initial template.
                links = []
                for entry in entries:
                    matches = []
                    for i, track in enumerate(tracks):
                        charge(1)
                        previous = track["entries"][-1]
                        delta = abs(float(wrap(entry["center"] - previous["center"])))
                        if (
                            entry["bucket"] - previous["bucket"] <= 2
                            and delta < (entry["width"] + previous["width"]) / 2.0 + spacing * 0.1
                        ):
                            matches.append(i)
                    links.append(matches)
                used = set()
                updated = []
                for entry, matches in zip(entries, links):
                    if len(matches) == 1 and sum(matches[0] in m for m in links) == 1:
                        idx = matches[0]
                        track = tracks[idx]
                        track["entries"].append(entry)
                        used.add(idx)
                        updated.append(track)
                    else:
                        for idx in matches:
                            tracks[idx]["ambiguous"] = True
                        trials += 1
                        if trials > p.maximum_objects:
                            raise ResourceLimit("morphology track budget exceeded")
                        # A join has no inherited object identity. Close the
                        # conflicting parents and test this observed envelope
                        # anew. Poisoning the new track permanently would veto
                        # a subsequent long, independently measured fan.
                        # No old members or qualification are inherited.
                        updated.append({"entries": [entry], "ambiguous": False})
                for idx, track in enumerate(tracks):
                    if idx in used:
                        continue
                    last = track["entries"][-1]
                    # A missing range window is a barrier; only measured quiet
                    # interruptions may be crossed as a discontinuous band.
                    previous_rows = last["rows"]
                    known = known_native[np.ix_(previous_rows, cols)]
                    barrier = blocked[previous_rows].any() or not known.all()
                    if int(bucket) - last["bucket"] < 2 and not barrier and not track["ambiguous"]:
                        updated.append(track)
                    else:
                        finish(track, scale, level)
                tracks = updated
            for track in tracks:
                finish(track, scale, level)
    return MorphologyEvidence(
        mask,
        ids,
        records,
        dict(
            status="EVALUATED",
            qualified_gates=int(mask.sum()),
            object_count=len(records),
            work=work,
            track_trials=trials,
            source_verified=False,
            maximum_flank_search_deg=MAXIMUM_FLANK_SEARCH_DEG,
            doppler_required=False,
            objects=records,
            review_objects=reviews,
        ),
    )
