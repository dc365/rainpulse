#!/usr/bin/env python3
"""One-cycle, non-publishing atlas A/B replay with frozen common inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import numpy as np
from rainpulse_algo.nowcast.nowcastnet_official_backend import OfficialNowcastNetBackend
from rainpulse_algo.nowcast.nowcastnet_shadow_service import (
    fetch_catalog,
    load_analysis_frame,
    parse_analysis_catalog,
)
from rainpulse_algo.nowcast.nowcastnet_shadow_worker import (
    _load_runtime,
    run_fixed_tile_atlas,
)
from rainpulse_algo.nowcast.nowcastnet_tile_atlas import load_tile_atlas, stitch_member_tiles
from rainpulse_algo.nowcast.temporal_adapter import adapt_members_to_five_minutes
from rainpulse_algo.worker.object_store import (
    ArtifactObjectReader,
    minio_client_from_environment,
)


def field_metrics(field: np.ndarray, truth: np.ndarray | None) -> dict:
    valid = np.isfinite(field)
    dx, dy = np.abs(np.diff(field, axis=1)), np.abs(np.diff(field, axis=0))
    # Fixed old-atlas boundaries: do not choose favourable seams after seeing outputs.
    seams = np.concatenate([dx[:, np.array([96, 192, 288, 384, 480]) - 1].ravel(), dy[95].ravel()])
    finite_seams = seams[np.isfinite(seams)]
    result = {
        "valid_cells": int(valid.sum()),
        "max_mm_h": float(np.max(field[valid])),
        "mean_mm_h": float(np.mean(field[valid])),
        "p99_mm_h": float(np.percentile(field[valid], 99)),
        "area_ge10_cells": int(np.count_nonzero(field >= 10)),
        "area_ge25_cells": int(np.count_nonzero(field >= 25)),
        "old_seam_mean_jump_mm_h": float(np.mean(finite_seams)) if finite_seams.size else None,
        "old_seam_p95_jump_mm_h": float(np.percentile(finite_seams, 95))
        if finite_seams.size
        else None,
        "x192_mean_jump_mm_h": float(np.nanmean(dx[:, 191])),
        "all_grid_mean_jump_mm_h": float(np.nanmean(np.concatenate([dx.ravel(), dy.ravel()]))),
    }
    if truth is not None:
        common = valid & np.isfinite(truth)
        result["truth_common_cells"] = int(common.sum())
        result["mae_mm_h"] = (
            float(np.mean(np.abs(field[common] - truth[common]))) if common.any() else None
        )
        for threshold in [10, 25]:
            pred, obs = (field >= threshold) & common, (truth >= threshold) & common
            union = np.count_nonzero(pred | obs)
            result[f"csi_{threshold}"] = (
                float(np.count_nonzero(pred & obs) / union) if union else None
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default="http://127.0.0.1:8080")
    parser.add_argument("--issue-time", required=True, help="RFC3339 with UTC offset")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", default="cuda:0")
    args = parser.parse_args()
    issue = datetime.fromisoformat(args.issue_time.replace("Z", "+00:00"))
    if issue.utcoffset() is None:
        raise ValueError("issue time must include UTC offset")
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    config = root / "configs"
    paths = [
        config / "nowcast" / "fujian-nowcastnet-shadow-v2.yaml",
        config / "nowcast" / "rp026-nowcastnet-offline-v1.yaml",
        config / "nowcast" / "fujian-nowcastnet-tile-atlas-v1.yaml",
        config / "grids" / "fuzhou-0p01deg-v1.yaml",
        config / "products" / "rp015-application-products-v1.yaml",
    ]
    runtime = _load_runtime(
        *(str(p) for p in paths), str(root / "runtime/nowcastnet/official-v1"), args.device
    )
    # Same batch schedule in both arms; no GPU service or live queue is modified.
    runtime = replace(runtime, task=replace(runtime.task, gpu_batch_size=1))
    items, seen, cursor = [], set(), ""
    while True:
        query = {"status": "ANALYSIS_READY", "limit": 200}
        if cursor:
            query["cursor"] = cursor
        page = fetch_catalog(
            args.api_base + "/api/v1/analysis-cycles?" + urlencode(query), timeout_seconds=30
        )
        items.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if cursor in seen:
            raise ValueError("repeated analysis cursor")
        seen.add(cursor)
    refs = {
        r.analysis_time: r
        for r in parse_analysis_catalog({"items": items}, grid_id=runtime.grid.grid_id)
    }
    history = [refs[issue - timedelta(minutes=m)] for m in range(80, -1, -10)]
    reader = ArtifactObjectReader(minio_client_from_environment())
    loaded = [load_analysis_frame(reader, ref) for ref in history]
    rates, masks = np.stack([x[0] for x in loaded]), np.stack([x[1] for x in loaded])
    leads = [30, 60, 110, 115, 120]
    truth = {}
    for lead in leads:
        ref = refs.get(issue + timedelta(minutes=lead))
        if ref:
            rate, mask = load_analysis_frame(reader, ref)
            truth[lead] = np.where(mask == 1, rate, np.nan)
    seed = int(issue.timestamp()) % 2**32
    report = {
        "issue_time": issue.isoformat(),
        "random_seed": seed,
        "batch_size": 1,
        "operational_eligible": False,
        "published": False,
        "input_analysis_ids": [r.analysis_id for r in history],
        "input_array_sha256": hashlib.sha256(rates.tobytes() + masks.tobytes()).hexdigest(),
        "variants": {},
    }
    arrays = {}
    cached_tiles = []

    def capture_tiles(values, *, output_shape):
        cached_tiles[:] = [(tile, value.copy()) for tile, value in values]
        return stitch_member_tiles(values, output_shape=output_shape)

    for version in ["v1", "v2", "v2-sharp", "v2-rain-midpoint", "v3", "v3-rain-midpoint"]:
        atlas_path = (
            config / "nowcast" / f"fujian-nowcastnet-tile-atlas-{version.split('-')[0]}.yaml"
        )
        atlas = load_tile_atlas(atlas_path)
        candidate = replace(
            runtime, atlas=atlas, task=replace(runtime.task, tile_atlas_version=atlas.atlas_version)
        )
        print(f"starting {version}, {len(atlas.tiles)} tiles", flush=True)
        started = time.monotonic()
        if version in ("v2-sharp", "v2-rain-midpoint", "v3-rain-midpoint"):
            native, valid = stitch_member_tiles(
                cached_tiles,
                output_shape=atlas.grid_shape,
                weight_power=2 if version == "v2-sharp" else 1,
            )
            valid &= masks[-1][None, None]
            native = np.where(valid == 1, native, np.nan)
            diagnostics = {"reuses_exact_tile_predictions": True}
        else:
            # Intercept only this standalone process; live worker remains untouched.
            with patch(
                "rainpulse_algo.nowcast.nowcastnet_shadow_worker.stitch_member_tiles", capture_tiles
            ):
                native, valid, diagnostics = run_fixed_tile_atlas(
                    rates,
                    masks,
                    runtime=candidate,
                    random_seed=seed,
                    backend_factory=lambda profile: OfficialNowcastNetBackend(
                        runtime.capsule_root, profile=profile, device=args.device
                    ),
                )
        adapted = adapt_members_to_five_minutes(
            rates[-1],
            masks[-1],
            native,
            valid,
            native_leads=runtime.task.native_output_lead_minutes,
            midpoint_space="rain" if version.endswith("rain-midpoint") else "log",
        )
        # All members share publication support. Avoid converting missing to dry.
        mean = np.mean(adapted.rain_rate_mm_h, axis=0)
        arrays[version] = mean
        np.savez_compressed(args.output / f"{version}.npz", rain_rate_mm_h=mean)
        report["variants"][version] = {
            "atlas_version": atlas.atlas_version,
            "atlas_sha256": hashlib.sha256(atlas_path.read_bytes()).hexdigest(),
            "blend_policy": "raised-edge-squared-v1" if version == "v2-sharp" else "raised-edge-v1",
            "temporal_derivation": adapted.frames[0].derivation,
            "tile_prediction_sha256": hashlib.sha256(
                b"".join(value.tobytes() for _, value in cached_tiles)
            ).hexdigest(),
            "seconds": round(time.monotonic() - started, 2),
            "diagnostics": diagnostics,
            "metrics": {
                str(lead): field_metrics(mean[lead // 5 - 1], truth.get(lead)) for lead in leads
            },
        }
        (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
        print(f"completed {version}", flush=True)
    # Evaluate every variant on the SAME cells, including derived-frame masks.
    for lead in leads:
        index = lead // 5 - 1
        common = np.logical_and.reduce([np.isfinite(value[index]) for value in arrays.values()])
        if lead in truth:
            common &= np.isfinite(truth[lead])
        for version, value in arrays.items():
            report["variants"][version].setdefault("paired_metrics", {})[str(lead)] = field_metrics(
                np.where(common, value[index], np.nan), truth.get(lead)
            )
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap

    cmap = ListedColormap(
        [
            "#eeeeee",
            "#9dd9ff",
            "#4fabe3",
            "#2478b8",
            "#47a447",
            "#d6ce37",
            "#e88b32",
            "#d94136",
            "#a55bb0",
        ]
    )
    norm = BoundaryNorm([0, 0.1, 1, 2.5, 5, 10, 25, 50, 100, 200], cmap.N)
    fig, axes = plt.subplots(
        len(arrays), len(leads), figsize=(18, 2.5 * len(arrays)), constrained_layout=True
    )
    for row, version in enumerate(arrays):
        for col, lead in enumerate(leads):
            ax = axes[row, col]
            im = ax.imshow(arrays[version][lead // 5 - 1], origin="lower", cmap=cmap, norm=norm)
            ax.set_title(f"{version} +{lead} {'derived' if lead % 10 else 'native'}")
            ax.set_xticks([96, 192, 288, 384, 480])
            ax.set_yticks([96])
    fig.colorbar(im, ax=axes, label="mm/h", shrink=0.8)
    fig.savefig(args.output / "comparison.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
