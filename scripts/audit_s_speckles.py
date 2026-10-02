#!/usr/bin/env python3
"""Compare RAW-supported speckle policies on frozen native snapshots; no products."""

import argparse
import hashlib
import importlib
import json
import time
from pathlib import Path

import numpy as np
import yaml

from rainpulse_algo.radar.qc_engine.adapters import NativeSweep
from rainpulse_algo.radar.qc_engine.crossradar import (
    measurement_capability,
    pol_corroboration,
)
from rainpulse_algo.radar.qc_engine.physical_speckle import validate_serialized
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile
from rainpulse_algo.radar.qc_engine.speckle_review import speckle_candidates
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.isolation_config import (
    IsolationConfig,
)


def review(path, profile, output, plot):
    started = time.monotonic()
    with np.load(path, allow_pickle=False) as data:
        a = {k: data[k] for k in data.files}
    metadata = json.loads(str(a["METADATA"]))
    if not metadata.get("web_frame_identity"):
        raise ValueError("Web-bound original snapshot required")
    fields = {
        "DBZH": a["RAW"],
        **{k[7:]: v for k, v in a.items() if k.startswith("MOMENT_")},
    }
    available = {k[10:]: v for k, v in a.items() if k.startswith("AVAILABLE_")}
    delta = (np.roll(a["AZIMUTH"], -1) - a["AZIMUTH"]) % 360
    spacing = float(np.median(delta[(delta > 0.01) & (delta < 3.0)]))
    native = NativeSweep(
        path.stem,
        a["AZIMUTH"],
        a["ELEVATION"],
        a["RANGE"],
        a["RAY_TIME"],
        fields,
        available,
        np.arange(len(a["AZIMUTH"])),
        not a["GAP_AFTER"].any(),
        a["GEOMETRY_GOOD"],
        a["GAP_AFTER"],
        {},
        {"azimuth_spacing_deg": spacing},
    )
    blocked = (a["WEATHER"] == 1) | (a["CONFLICTS"] == 1) | (a["RV2_BARRED_MASK"] == 1)
    polar, _, _ = pol_corroboration(native, profile)
    _, _, _, reliable = measurement_capability(native, profile.residual)
    polar &= reliable & available["DBZH"]
    cfg = profile.residual.model_copy(
        update={
            "speckle_physical_support": IsolationConfig(weak_diagnostic_enabled=False)
        }
    )
    kwargs = dict(
        baseline_eligible=available["DBZH"],
        protected=blocked,
        pol_bad=polar,
        low_snr=available["SNR"] & (fields["SNR"] < profile.echo.low_snr_db),
    )
    old, _ = speckle_candidates(native, profile.residual, **kwargs)
    new, detail = speckle_candidates(native, cfg, **kwargs)
    replay = {
        **new,
        **{k + "_RAW": v for k, v in fields.items()},
        "azimuth": native.azimuth,
        "elevation": native.elevation,
        "range": native.ranges,
        "VALID_MASK": available["DBZH"].astype("uint8"),
        "V6_BASELINE_ELIGIBLE_MASK": available["DBZH"].astype("uint8"),
    }
    validate_serialized(replay, cfg.model_dump(mode="json"))
    hit = new["V6_SPECKLE_CANDIDATE_MASK"] == 1
    previous = old["V6_SPECKLE_CANDIDATE_MASK"] == 1
    if (hit & blocked).any():
        raise ValueError("physical speckle crossed weather/protection")
    if not np.array_equal(fields["DBZH"], a["RAW"], equal_nan=True):
        raise ValueError("RAW changed")
    records = [
        dict(
            row=int(r),
            column=int(g),
            raw_dbzh=float(fields["DBZH"][r, g]),
            snr_db=float(fields["SNR"][r, g]),
            polar_evidence=bool(polar[r, g]),
            original_object_id=int(new["V6_PHYSICAL_SPECKLE_OBJECT_ID"][r, g]),
            known_fraction=float(new["V6_RAW_NEIGHBOUR_OBS_FRACTION"][r, g]),
        )
        for r, g in zip(*np.where(hit))
    ]
    image_path = None
    if plot:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        theta = np.deg2rad(native.azimuth[:, None])
        radius = native.ranges[None, :] / 1000
        x, y = radius * np.sin(theta), radius * np.cos(theta)
        echo = available["DBZH"] & (fields["DBZH"] >= 5)
        fig, axes = plt.subplots(1, 2, figsize=(12, 6), layout="constrained")
        for ax, title, mask in zip(
            axes,
            ["Complete RAW", "Physical speckle candidates (not published)"],
            [np.zeros(native.shape, bool), hit],
        ):
            im = ax.scatter(
                x[echo],
                y[echo],
                c=fields["DBZH"][echo],
                cmap="turbo",
                vmin=5,
                vmax=70,
                s=0.5,
            )
            if mask.any():
                ax.scatter(
                    x[mask],
                    y[mask],
                    color="#ff00ff",
                    s=50,
                    facecolors="none",
                    label=f"{mask.sum()} candidate gates",
                )
                ax.legend()
            ax.set(
                title=title,
                xlabel="East (km)",
                ylabel="North (km)",
                xlim=(-480, 480),
                ylim=(-480, 480),
                aspect="equal",
            )
            ax.grid(alpha=0.2)
        fig.suptitle(path.stem + " | original geometry; no weather-truth claim")
        fig.colorbar(im, ax=list(axes), label="dBZ", shrink=0.7)
        image_path = output / (path.stem + ".png")
        fig.savefig(image_path, dpi=150)
        plt.close(fig)
    return dict(
        snapshot=str(path),
        snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        scan_id=metadata["scan_id"],
        whole_raw_legacy_candidates=int(previous.sum()),
        whole_raw_physical_candidates=int(hit.sum()),
        new_raw_candidates=int((hit & ~previous).sum()),
        legacy_only_retained=int((previous & ~hit).sum()),
        target_low_snr_db=profile.echo.low_snr_db,
        objects=detail["objects"],
        small_objects=detail["small_objects"],
        isolated_objects=detail["isolated_objects"],
        noise_limited_support=detail["noise_limited_support_gates"],
        protected_overlap=0,
        rejection_diagnostics={
            "small_raw_gates": int(new["V6_PHYSICAL_SPECKLE_SMALL_MASK"].sum()),
            "limited_small_gates": int(
                (
                    new["V6_PHYSICAL_SPECKLE_SMALL_MASK"]
                    & new["V6_PHYSICAL_SPECKLE_GEOMETRY_LIMITED_MASK"]
                ).sum()
            ),
            "measured_isolated_gates": int(
                new["V6_PHYSICAL_SPECKLE_ISOLATED_MASK"].sum()
            ),
            "isolated_with_current_evidence": int(
                (
                    new["V6_PHYSICAL_SPECKLE_ISOLATED_MASK"]
                    & new["V6_PHYSICAL_SPECKLE_CURRENT_MASK"]
                ).sum()
            ),
        },
        raw_unchanged=True,
        serialized_raw_replay=True,
        seconds=round(time.monotonic() - started, 3),
        records=records,
        image=None if image_path is None else str(image_path),
        image_sha256=None
        if image_path is None
        else hashlib.sha256(image_path.read_bytes()).hexdigest(),
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("snapshots", type=Path, nargs="+")
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--plot", action="store_true")
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    profile = OpenSourceQCProfile.model_validate(
        yaml.safe_load(args.profile.read_text())
    )
    if profile.residual.speckle_physical_support is not None:
        raise ValueError("legacy parent required")
    args.output.mkdir(parents=True)
    results = []
    seen = set()
    for path in args.snapshots:
        report = review(path, profile, args.output, args.plot)
        identity = (report["scan_id"], path.stem)
        if identity in seen:
            raise ValueError("duplicate original scan/cut")
        seen.add(identity)
        results.append(report)
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in [
                        "snapshot",
                        "whole_raw_legacy_candidates",
                        "whole_raw_physical_candidates",
                        "seconds",
                    ]
                }
            ),
            flush=True,
        )
    report = dict(
        scope="complete_RAW_speckle_comparison_not_Web_delta",
        product_writes=False,
        action_authority=False,
        independent_weather_truth=False,
        profile_sha256=hashlib.sha256(args.profile.read_bytes()).hexdigest(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        modules={
            name: hashlib.sha256(
                Path(
                    importlib.import_module(
                        "rainpulse_algo.radar.qc_engine." + name
                    ).__file__
                ).read_bytes()
            ).hexdigest()
            for name in [
                "physical_speckle",
                "speckle_review",
                "residual_profile",
                "crossradar",
                "volume_review.clutter_fusion.isolation_geometry",
                "volume_review.clutter_fusion.isolation_config",
            ]
        },
        results=results,
    )
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
