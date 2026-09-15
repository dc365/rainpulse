"""Raw-domain interrupted spike experiment, informed by MIT ATC-454 §3.2/3.3.

Not the MIT implementation: our moments lack SQI/unprocessed PHIDP/KDP semantics.
Missing shoulders remain unknown; SNR is not substituted for SQI. Shape and
measured polarimetric corroboration must both hold at a gate before rejection.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.ndimage import convolve1d


class FragmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    method: Literal["raw-fragment-circular-phase-1"] = "raw-fragment-circular-phase-1"
    range_window_m: float = Field(default=20000, ge=5000, le=60000)
    minimum_observed_fraction: float = Field(default=0.65, gt=0.5, le=1)
    minimum_echo_dbz: float = Field(default=-5, ge=-20, le=30)
    minimum_range_m: float = Field(default=10000, ge=0)
    shoulder_offsets_deg: tuple[float, ...] = (1, 2, 4, 8)
    minimum_contrast_db: float = Field(default=8, ge=4, le=20)
    phase_window_gates: int = Field(default=5, ge=3, le=21)
    phase_minimum_fraction: float = Field(default=0.8, gt=0.5, le=1)
    phase_period_deg: float = Field(default=360, gt=0)
    phase_variance_minimum: float = Field(default=0.085, gt=0, lt=1)
    maximum_rhohv: float = Field(default=0.8, gt=0, lt=1)
    minimum_snr_db: float = Field(default=8, ge=0)
    association_maximum_distance_m: float = Field(default=0, ge=0, le=20000)
    association_minimum_anchor_m: float = Field(default=2000, ge=1000)
    association_difference_db: float = Field(default=8, ge=0, le=12)
    range_term_enabled: bool = False
    interrupted_objects_enabled: bool = False

    @model_validator(mode="after")
    def validate_windows(self):
        if self.phase_window_gates % 2 != 1:
            raise ValueError("phase window must be odd")
        if not self.shoulder_offsets_deg or any(
            not np.isfinite(x) or not 0 < x <= 12 for x in self.shoulder_offsets_deg
        ):
            raise ValueError("positive finite shoulder offsets <=12 degrees required")
        if tuple(sorted(set(self.shoulder_offsets_deg))) != self.shoulder_offsets_deg:
            raise ValueError("shoulder offsets must be ordered and unique")
        return self


def _sum(values, width):
    return convolve1d(
        np.asarray(values, dtype=np.float64), np.ones(width), axis=1, mode="constant", cval=0
    )


def fragment_evidence(native, cfg):
    from ..qc_geometry import nearest_azimuth_matches

    z = native.fields["DBZH"]
    observed = native.field_available["DBZH"] & np.isfinite(z) & native.geometry_good[:, None]
    width = max(3, int(round(cfg.range_window_m / native.gate_spacing_m)) | 1)
    count = _sum(observed, width)
    enough = count >= np.ceil(width * cfg.minimum_observed_fraction)
    echo = observed & (z >= cfg.minimum_echo_dbz)
    solid = _sum(echo, width) >= np.ceil(width * cfg.minimum_observed_fraction)
    mean = _sum(np.where(observed, z, 0), width) / np.maximum(count, 1)
    contrast = np.full(native.shape, np.nan, "float32")
    rows = np.arange(native.shape[0])
    spacing = float(native.audit["azimuth_spacing_deg"])
    # Geometry, including internal gaps, is validated along both azimuth paths.
    for offset in cfg.shoulder_offsets_deg:
        left, dl, _ = nearest_azimuth_matches((native.azimuth - offset) % 360, native.azimuth)
        right, dr, _ = nearest_azimuth_matches((native.azimuth + offset) % 360, native.azimuth)
        good = (
            (dl <= spacing * 0.55)
            & (dr <= spacing * 0.55)
            & (left != rows)
            & (right != rows)
            & (left != right)
        )
        good &= native.geometry_good & native.geometry_good[left] & native.geometry_good[right]
        for ray in np.flatnonzero(good):
            for target, direction in ((left[ray], -1), (right[ray], 1)):
                j = ray
                for _ in range(len(rows)):
                    if j == target:
                        break
                    edge = (j - 1) % len(rows) if direction == -1 else j
                    nxt = (j + direction) % len(rows)
                    if (
                        native.gap_after[edge]
                        or not native.geometry_good[nxt]
                        or (not native.full_ppi and abs(nxt - j) > 1)
                    ):
                        good[ray] = False
                        break
                    j = nxt
        supported = enough & enough[left] & enough[right] & good[:, None]
        delta = mean - np.maximum(mean[left], mean[right])
        contrast = np.fmax(contrast, np.where(supported, delta, np.nan)).astype("float32")
    candidate = echo & solid & (contrast >= cfg.minimum_contrast_db)
    candidate &= native.ranges[None, :] >= cfg.minimum_range_m

    empty = np.zeros(native.shape, bool)
    snr = native.fields.get("SNR", np.full(native.shape, np.nan))
    rho = native.fields.get("RHOHV", np.full(native.shape, np.nan))
    phi = native.fields.get("PHIDP", np.full(native.shape, np.nan))
    reliable = observed & native.field_available.get("SNR", empty) & (snr >= cfg.minimum_snr_db)
    pair = (
        reliable
        & native.field_available.get("RHOHV", empty)
        & native.field_available.get("PHIDP", empty)
    )
    pair &= np.isfinite(rho) & np.isfinite(phi)
    n = _sum(pair, cfg.phase_window_gates)
    phase_ok = pair & (n >= np.ceil(cfg.phase_window_gates * cfg.phase_minimum_fraction))
    angle = np.where(pair, phi, 0) * (2 * np.pi / cfg.phase_period_deg)
    c = _sum(np.where(pair, np.cos(angle), 0), cfg.phase_window_gates)
    s = _sum(np.where(pair, np.sin(angle), 0), cfg.phase_window_gates)
    variance = np.clip(1 - np.hypot(c, s) / np.maximum(n, 1), 0, 1)
    variance = np.where(phase_ok, variance, np.nan).astype("float32")
    rho_mean = _sum(np.where(pair, rho, 0), cfg.phase_window_gates) / np.maximum(n, 1)
    pol = (
        phase_ok
        & (rho < cfg.maximum_rhohv)
        & (rho_mean < cfg.maximum_rhohv)
        & (variance >= cfg.phase_variance_minimum)
    )
    return {
        "FRAGMENT_CANDIDATE_MASK": candidate.astype("uint8"),
        "FRAGMENT_POL_CONFIRMED_MASK": (candidate & pol).astype("uint8"),
        "FRAGMENT_LOCAL_POL_BAD_MASK": pol.astype("uint8"),
        "FRAGMENT_POL_AVAILABLE_MASK": phase_ok.astype("uint8"),
        "FRAGMENT_PHASE_CIRCULAR_VARIANCE": variance,
        "FRAGMENT_SHOULDER_CONTRAST_DB": contrast,
    }


def estimate_range_term(native):
    """Estimate shared processing range trend, NOT independent RFI evidence.

    Fit DBZH - source SNR - 20log10(r/km) across many rays. Disjoint ray
    groups must agree; no fitting of an arbitrary slope to a suspect ray.
    Unknown receiver/noise conventions disable this route on disagreement.
    """
    import warnings

    if "SNR" not in native.fields:
        return None, {"status": "snr_unavailable"}
    observed = native.field_available["DBZH"] & native.field_available["SNR"]
    observed &= native.geometry_good[:, None] & (native.ranges[None, :] >= 20000)
    observed &= native.fields["SNR"] >= 10
    values = (
        native.fields["DBZH"]
        - native.fields["SNR"]
        - 20 * np.log10(np.maximum(native.ranges[None, :], 1) / 1000)
    )
    estimates = []
    for parity in (0, 1):
        rows = native.original_indices % 2 == parity
        count = observed[rows].sum(axis=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            y = np.nanmedian(np.where(observed[rows], values[rows], np.nan), axis=0)
        ok = (count >= 5) & np.isfinite(y)
        if ok.sum() < 100 or np.ptp(native.ranges[ok]) < 200000:
            return None, {"status": "insufficient_paired_support"}
        x = native.ranges[ok] / 1000
        slope, intercept = np.polyfit(x, y[ok], 1)
        residual = float(np.percentile(abs(y[ok] - slope * x - intercept), 90))
        estimates.append(
            {
                "slope_db_per_km": float(slope),
                "residual_p90_db": residual,
                "range_gates": int(ok.sum()),
            }
        )
    if abs(estimates[0]["slope_db_per_km"] - estimates[1]["slope_db_per_km"]) > 0.002:
        return None, {"status": "inconsistent_ray_groups", "groups": estimates}
    if any(not 0 <= g["slope_db_per_km"] <= 0.03 or g["residual_p90_db"] > 0.5 for g in estimates):
        return None, {"status": "unsupported_range_relation", "groups": estimates}
    coefficient = float(np.mean([g["slope_db_per_km"] for g in estimates]))
    return coefficient, {
        "status": "measured_consistent",
        "method": "paired-moment-range-term-1",
        "coefficient_db_per_km": coefficient,
        "groups": estimates,
    }


def associate_fragments(native, anchor, range_model, cfg):
    """One-pass same-ray identity association; never fill gaps or propagate decisions.

    Both geometry and actual target reflectivity must be available. Intermediate
    missing/weather gates can separate fragments of an identity, but acquire no
    inherited action. Parents are frozen before this call and minimum-length vetted.
    """
    from .segments import runs

    shape = native.shape
    if np.shape(anchor) != shape or np.shape(range_model) != shape:
        raise ValueError("fragment anchor geometry differs")
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    z = native.fields["DBZH"]
    echo = observed & (z >= cfg.minimum_echo_dbz) & (native.ranges[None, :] >= cfg.minimum_range_m)
    ids = np.zeros(shape, "uint32")
    parent = np.full(shape, -1, "int32")
    distance = np.full(shape, np.nan, "float32")
    residual = np.full(shape, np.nan, "float32")
    linked = np.zeros(shape, bool)
    identity = 0
    for ray in range(shape[0]):
        for lo, hi in runs(anchor[ray] & echo[ray]):
            if (hi - lo) * native.gate_spacing_m >= cfg.association_minimum_anchor_m:
                identity += 1
                ids[ray, lo:hi] = identity
        seeds = np.flatnonzero(ids[ray])
        targets = np.flatnonzero(echo[ray] & ~anchor[ray])
        if not len(seeds) or not len(targets) or cfg.association_maximum_distance_m == 0:
            continue
        pos = np.searchsorted(seeds, targets)
        left = seeds[np.maximum(0, pos - 1)]
        right = seeds[np.minimum(len(seeds) - 1, pos)]
        origin = np.where(abs(targets - left) <= abs(targets - right), left, right)
        dist = abs(native.ranges[targets] - native.ranges[origin])
        correction = np.where(
            range_model[ray, origin],
            20
            * np.log10(
                np.maximum(native.ranges[targets], native.gate_spacing_m / 2)
                / np.maximum(native.ranges[origin], native.gate_spacing_m / 2)
            ),
            0,
        )
        delta = abs(z[ray, targets] - z[ray, origin] - correction)
        ok = (dist <= cfg.association_maximum_distance_m) & (delta <= cfg.association_difference_db)
        t, src = targets[ok], origin[ok]
        linked[ray, t] = True
        # Source IDs are read only from the immutable seed index array.
        ids[ray, t] = ids[ray, src]
        parent[ray, t] = src
        distance[ray, t] = dist[ok]
        residual[ray, t] = delta[ok]
    return {
        "FRAGMENT_LINKED_MASK": linked.astype("uint8"),
        "FRAGMENT_ASSOCIATION_ID": ids,
        "FRAGMENT_PARENT_GATE": parent,
        "FRAGMENT_PARENT_RAY": np.where(parent >= 0, native.original_indices[:, None], -1).astype(
            "int32"
        ),
        "FRAGMENT_PARENT_DISTANCE_M": distance,
        "FRAGMENT_PARENT_RESIDUAL_DB": residual,
    }


def apply_fragment_decision(
    native, baseline, cfg, profile, *, weather_support=None, cross_support=None
):
    from .decision import Action, Decision

    if cfg.phase_period_deg != profile.geometry.phase_period_deg:
        raise ValueError("fragment phase period differs from source profile")
    e = fragment_evidence(native, cfg)
    arrays = {k: v.copy() for k, v in baseline.arrays.items()}
    flags, quality = baseline.flags.copy(), baseline.quality.copy()
    confirmed = e["FRAGMENT_POL_CONFIRMED_MASK"] == 1
    range_candidate = np.zeros(native.shape, bool)
    range_record = {"status": "disabled"}
    if cfg.range_term_enabled:
        from .range_signature import range_signatures

        term, range_record = estimate_range_term(native)
        if term is not None:
            fit = range_signatures(native, profile.cross_radar, range_term_db_per_km=term)
            range_candidate = fit.arrays["V5_RANGE_CANDIDATE_MASK"] == 1
            e["FRAGMENT_RANGE_TERM_CANDIDATE_MASK"] = range_candidate.astype("uint8")
            e["FRAGMENT_RANGE_TERM_MODEL_CODE"] = fit.arrays["V5_RANGE_MODEL_CODE"]
            e["FRAGMENT_RANGE_TERM_RESIDUAL_P90_DB"] = fit.arrays["V5_RANGE_RESIDUAL_P90_DB"]
            range_record["objects"] = fit.summary
    linked = np.zeros(native.shape, bool)
    conflict = np.zeros(native.shape, bool)
    if cfg.association_maximum_distance_m > 0:
        old = baseline.arrays
        modes = old.get("V5_RANGE_MODEL_CODE", np.zeros(native.shape))
        models = (old.get("V5_RANGE_CANDIDATE_MASK", np.zeros(native.shape)) == 1) & np.isin(
            modes, [1, 2]
        )
        anchor = (
            (old["QC_ACTION"] == Action.REJECT)
            & ((baseline.flags & profile.flag_masks["RADIAL_INTERFERENCE"]) != 0)
        ) | models
        a = associate_fragments(native, anchor, models & (modes == 1), cfg)
        e.update(a)
        linked = a["FRAGMENT_LINKED_MASK"] == 1
        local_bad = e["FRAGMENT_LOCAL_POL_BAD_MASK"] == 1
        if cross_support is not None:
            if np.shape(cross_support) != native.shape:
                raise ValueError("fragment cross support geometry differs")
            conflict = linked & local_bad & (cross_support >= profile.context.strong_support)
        # Absence of a neighbour is never a pollution vote. Confirmation still
        # needs target-local, reliable polarimetric evidence, not the parent action.
        confirmed |= linked & local_bad & ~conflict
        e["FRAGMENT_CANDIDATE_MASK"] |= linked.astype("uint8")
        e["FRAGMENT_POL_CONFIRMED_MASK"] = confirmed.astype("uint8")
        e["FRAGMENT_CROSS_WEATHER_CONFLICT_MASK"] = conflict.astype("uint8")
        e["FRAGMENT_CROSS_SUPPORT_AVAILABLE_MASK"] = (
            np.isfinite(cross_support)
            if cross_support is not None
            else np.zeros(native.shape, bool)
        ).astype("uint8")
    object_records = []
    if cfg.interrupted_objects_enabled:
        from .interrupted_objects import interrupted_objects

        old = baseline.arrays
        anchor = (old["QC_ACTION"] == Action.REJECT) & (
            (baseline.flags & profile.flag_masks["RADIAL_INTERFERENCE"]) != 0
        )
        objects, object_ids, object_records = interrupted_objects(native, anchor)
        e["FRAGMENT_OBJECT_ID"] = object_ids
        e["FRAGMENT_OBJECT_CANDIDATE_MASK"] = objects.astype("uint8")
        e["FRAGMENT_CANDIDATE_MASK"] |= objects.astype("uint8")
        local_bad = e["FRAGMENT_LOCAL_POL_BAD_MASK"] == 1
        object_conflict = np.zeros(native.shape, bool)
        if cross_support is not None:
            if np.shape(cross_support) != native.shape:
                raise ValueError("object cross support geometry differs")
            object_conflict = (
                objects
                & local_bad
                & np.isfinite(cross_support)
                & (cross_support >= profile.context.strong_support)
            )
        # Identity cannot confer pollution: target-local polarimetry is mandatory.
        confirmed |= objects & local_bad & ~object_conflict
        conflict |= object_conflict
        e["FRAGMENT_POL_CONFIRMED_MASK"] = confirmed.astype("uint8")
        e["FRAGMENT_OBJECT_CONFLICT_MASK"] = object_conflict.astype("uint8")
    supported_weather = np.zeros(native.shape, bool)
    if cross_support is not None:
        if np.shape(cross_support) != native.shape:
            raise ValueError("range term cross support geometry differs")
        supported_weather = np.isfinite(cross_support) & (
            cross_support >= profile.context.strong_support
        )
    # Same-family range signature may withhold, never confirm pollution.
    range_withheld = range_candidate & ~supported_weather
    quarantine = (conflict | range_withheld) & ~confirmed & (arrays["QC_ACTION"] != Action.REJECT)
    e["FRAGMENT_CANDIDATE_MASK"] |= range_candidate.astype("uint8")
    added_quarantine = quarantine & (arrays["RFI_QUARANTINE_MASK"] != 1)
    arrays["QC_ACTION"][quarantine] = Action.DOWNWEIGHT
    arrays["RFI_QUARANTINE_MASK"][quarantine] = 1
    arrays["QPE_ELIGIBLE_MASK"][quarantine] = 0
    arrays["RFI_RISK_STATE"][quarantine] = 2
    quality[quarantine] = np.minimum(quality[quarantine], profile.residual.quarantine_quality)
    flags[quarantine] |= profile.flag_masks["LOW_QUALITY"]
    e["FRAGMENT_QUARANTINED_ADDITION_MASK"] = added_quarantine.astype("uint8")
    added = confirmed & (arrays["QC_ACTION"] != Action.REJECT)
    e["FRAGMENT_CONFIRMED_ADDITION_MASK"] = added.astype("uint8")
    arrays.update(e)
    arrays["QC_ACTION"][confirmed] = Action.REJECT
    arrays["RFI_QUARANTINE_MASK"][confirmed] = 0
    arrays["QPE_ELIGIBLE_MASK"][confirmed] = 0
    arrays["RFI_RISK_STATE"][confirmed] = 3
    if weather_support is not None:
        if np.shape(weather_support) != native.shape:
            raise ValueError("fragment weather support geometry differs")
        if "RFI_MIXED_MASK" in arrays:
            arrays["RFI_MIXED_MASK"] |= (
                confirmed & (weather_support >= profile.context.strong_support)
            ).astype("uint8")
    for name in ("REFLECTIVITY", "RHOHV", "ZDR", "PHIDP", "VR", "SW", "SNR"):
        key = name + "_TRUST_MASK"
        if key in arrays:
            arrays[key][confirmed | quarantine] = 0
    # Raw and all other retained observations remain byte-for-byte unchanged.
    arrays["DBZH_USABLE"][confirmed | quarantine] = np.nan
    quality[confirmed] = 0
    flags[confirmed] |= (
        profile.flag_masks["RADIAL_INTERFERENCE"]
        | profile.flag_masks["NON_METEOROLOGICAL"]
        | profile.flag_masks["LOW_QUALITY"]
    )
    return Decision(arrays, flags, quality), {
        "method": cfg.method,
        "parameters": cfg.model_dump(mode="json"),
        "candidate_gates": int((e["FRAGMENT_CANDIDATE_MASK"] == 1).sum()),
        "confirmed_additions": int(added.sum()),
        "linked_gates": int(linked.sum()),
        "interrupted_objects": object_records,
        "range_term": range_record,
        "quarantined_additions": int(added_quarantine.sum()),
        "newly_ineligible": int(
            ((confirmed | quarantine) & (baseline.arrays["QPE_ELIGIBLE_MASK"] == 1)).sum()
        ),
        "scope": "experimental_gate_corroboration_not_validated_weather_skill",
    }


def main():
    """Offline paired replay of frozen published decisions; never publishes assets."""
    import argparse
    import hashlib
    import json
    from pathlib import Path
    from time import perf_counter

    import zarr

    from ..qc import load_qc_profile
    from .adapters import adapt_sweep
    from .decision import Decision

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--flags", required=True)
    parser.add_argument("--association-distance-m", type=float, default=0)
    parser.add_argument("--range-term", action="store_true")
    args = parser.parse_args()
    p = load_qc_profile(args.profile, args.flags)
    cfg = FragmentConfig(
        phase_period_deg=p.geometry.phase_period_deg,
        association_maximum_distance_m=args.association_distance_m,
        range_term_enabled=args.range_term,
    )
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    records = []
    for d in sorted(Path(args.root).iterdir()):
        if not (d / "qc.zarr").exists():
            continue
        root = zarr.open_group(str(d / "normalized.zarr"), mode="r")
        n = adapt_sweep(root, "sweep_000", p)
        q = zarr.open_group(str(d / "qc.zarr"), mode="r")["sweep_000"]
        arrays = {k: q[k][:][n.original_indices] for k in q.array_keys() if q[k].shape == n.shape}
        np.testing.assert_equal(arrays["DBZH_RAW"], n.fields["DBZH"])
        np.testing.assert_equal(q["azimuth"][:][n.original_indices], n.azimuth)
        np.testing.assert_equal(q["range"][:], n.ranges)
        before = Decision(arrays, arrays["QC_FLAGS"], arrays["QUALITY_INDEX"])
        started = perf_counter()
        weather = np.fmax(
            arrays.get("V7_VERTICAL_SUPPORT_SCORE", np.full(n.shape, np.nan)),
            arrays.get("V7_CROSS_RADAR_SUPPORT_SCORE", np.full(n.shape, np.nan)),
        )
        after, stats = apply_fragment_decision(
            n,
            before,
            cfg,
            p,
            weather_support=weather,
            cross_support=arrays.get("V7_CROSS_RADAR_SUPPORT_SCORE"),
        )
        added = after.arrays["FRAGMENT_CONFIRMED_ADDITION_MASK"] == 1
        visible = (
            np.isfinite(arrays["DBZH_QC"]) & (arrays["QC_ACTION"] != 2) & (n.fields["DBZH"] >= 10)
        )
        from ...diagnostics.renderer import BUSINESS_HARD_REJECT_FLAG_NAMES

        hard_mask = np.uint32(0)
        for name in BUSINESS_HARD_REJECT_FLAG_NAMES:
            hard_mask |= p.flag_masks[name]
        business_before = (arrays["QPE_ELIGIBLE_MASK"] == 1) & ((before.flags & hard_mask) == 0)
        business_after = (after.arrays["QPE_ELIGIBLE_MASK"] == 1) & ((after.flags & hard_mask) == 0)
        old_candidate = np.zeros(n.shape, bool)
        for k, v in arrays.items():
            if "CANDIDATE" in k and k.endswith("MASK"):
                old_candidate |= v == 1
        stats.update(
            scan_id=d.name,
            radar=root.attrs.get("radar_id"),
            time=str(n.ray_time[0]),
            elapsed_seconds=perf_counter() - started,
            removed_visible_ge10=int((visible & added).sum()),
            removed_business_visible_ge10=int(
                (business_before & ~business_after & (n.fields["DBZH"] >= 10)).sum()
            ),
            visibility_definition="removed_visible_ge10 is action-only audit, NOT website; business uses eligibility and hard flags",
            visible_ge10=int(visible.sum()),
            previously_quarantined=int((added & (arrays["RFI_QUARANTINE_MASK"] == 1)).sum()),
            previously_unrecognized=int((added & ~old_candidate).sum()),
            high_rho_removed=int(
                (added & (n.fields.get("RHOHV", np.full(n.shape, np.nan)) >= 0.95)).sum()
            ),
            missing_removed=int((added & ~n.field_available["DBZH"]).sum()),
        )
        out = dest / d.name
        out.mkdir(exist_ok=True)
        np.savez_compressed(
            out / "evidence.npz",
            **{k: n.restore(v) for k, v in after.arrays.items() if k.startswith("FRAGMENT_")},
        )
        (out / "summary.json").write_text(json.dumps(stats, indent=2))
        # Paired raw-domain gates, same palette and coordinates. Never infill gaps.
        if stats["radar"] in ("z9591", "z9598"):
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            theta = np.deg2rad(n.azimuth)[:, None]
            rr = n.ranges[None, :] / 1000
            x = np.broadcast_to(rr * np.sin(theta), n.shape)
            y = np.broadcast_to(rr * np.cos(theta), n.shape)
            fig, axes = plt.subplots(1, 3, figsize=(15, 5), constrained_layout=True)
            for ax, (values, title) in zip(
                axes,
                [
                    (n.fields["DBZH"], "Raw"),
                    (
                        np.where(business_before, arrays["DBZH_QC"], np.nan),
                        "Baseline business QC",
                    ),
                    (
                        np.where(business_after, arrays["DBZH_QC"], np.nan),
                        "Experiment business QC",
                    ),
                ],
            ):
                ok = np.isfinite(values) & (values >= -5)
                im = ax.scatter(
                    x[ok],
                    y[ok],
                    c=values[ok],
                    s=0.45,
                    cmap="turbo",
                    vmin=-10,
                    vmax=70,
                    rasterized=True,
                )
                ax.set(
                    xlim=(-460, 460),
                    ylim=(-460, 460),
                    aspect="equal",
                    title=title,
                    xlabel="East (km)",
                    ylabel="North (km)",
                )
            fig.colorbar(im, ax=list(axes), label="dBZ", shrink=0.7)
            fig.suptitle(
                f"{stats['radar']} {stats['time']} UTC / {stats['removed_business_visible_ge10']} business-visible gates removed / experimental"
            )
            fig.savefig(out / "comparison.png", dpi=140)
            plt.close(fig)
        records.append(stats)
        print(
            json.dumps(
                {
                    k: stats[k]
                    for k in (
                        "radar",
                        "time",
                        "removed_visible_ge10",
                        "newly_ineligible",
                        "previously_unrecognized",
                        "elapsed_seconds",
                    )
                }
            ),
            flush=True,
        )
        (dest / "summary.json").write_text(
            json.dumps(
                {
                    "config": cfg.model_dump(mode="json"),
                    "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    "scope": "29 frozen lowest cuts, paired published baseline; no online publication or weather truth labels",
                    "cases": records,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
