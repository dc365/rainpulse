#!/usr/bin/env python3
"""Audit past original seed cells and RAW isolated objects; never write products."""

import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from rainpulse_algo.radar.qc_engine.review_extension.radial_revision import (
    source_temporal,
)
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.isolation_config import (
    IsolationConfig,
)
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.isolation_geometry import (
    inspect,
)
from rainpulse_algo.radar.qc_engine.volume_review.data import Sweep


def load(path):
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    with np.load(path, allow_pickle=False) as a:
        metadata = json.loads(str(a["METADATA"]))
        if (
            metadata.get("web_frame_identity", {}).get("web_scan_id")
            != metadata["scan_id"]
        ):
            raise ValueError("Web-bound original scan required")
        time = a["RAY_TIME"]
        if time.dtype.kind != "M" or np.isnat(time).any():
            raise ValueError("explicit original UTC acquisition times required")
        fields = {
            "DBZH": a["RAW"],
            **{k[7:]: a[k] for k in a.files if k.startswith("MOMENT_")},
        }
        available = {k[10:]: a[k] for k in a.files if k.startswith("AVAILABLE_")}
        sweep = Sweep(
            f"sweep_{metadata['sweep']:03d}",
            a["AZIMUTH"],
            a["ELEVATION"],
            a["RANGE"],
            fields,
            available,
            a["GEOMETRY_GOOD"],
            a["GAP_AFTER"],
            time.astype("datetime64[ns]").astype("int64") / 1e9,
        )
        seed = a["RV2_SOURCE_LEDGER_SEED_ID"]
        for name, expected in (
            ("RANGE_M", np.broadcast_to(sweep.ranges, sweep.shape)),
            ("SPACING_M", np.full(sweep.shape, sweep.dr)),
        ):
            actual = a["RV2_SOURCE_LEDGER_" + name]
            if not np.allclose(actual[seed > 0], expected[seed > 0], rtol=0, atol=0.01):
                raise ValueError("original source native coordinate mismatch")
        if not np.array_equal(seed > 0, a["RV2_SOURCE_LEDGER_SEED_MASK"] == 1):
            raise ValueError("original seed mask/identity mismatch")
        lineage = {
            "profile_sha256": metadata["profile_sha256"],
            "source_config": metadata["config"],
            "source_module": metadata["module_sha256"]["source_ledger"],
        }
        lineage_id = hashlib.sha256(
            json.dumps(lineage, sort_keys=True).encode()
        ).hexdigest()
        blocked = (
            (a["WEATHER"] == 1) | (a["CONFLICTS"] == 1) | (a["RV2_BARRED_MASK"] == 1)
        )
        frame = source_temporal.SourceFrame(
            sweep,
            seed,
            a["RV2_SOURCE_LEDGER_SOURCE_HOLD"],
            blocked,
            metadata["radar_id"],
            metadata["scan_id"],
            sha,
            lineage_id,
        )
    return frame, metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshots", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    loaded = [(path, *load(path)) for path in args.snapshots]
    identities = [(f.radar_id, f.scan_id, f.sweep.name) for _, f, _ in loaded]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate original scan/cut")
    args.output.mkdir(parents=True)
    results = []
    for path, current, metadata in loaded:
        # Only past snapshots of this site/cut are supplied; neither future data
        # nor matching-only history is selected. Module enforces age and lineage.
        past = [
            f
            for _, f, _ in loaded
            if f.radar_id == current.radar_id
            and f.sweep.name == current.sweep.name
            and f.sweep.ray_time_s.max() < current.sweep.ray_time_s.min()
        ]
        past.sort(key=lambda f: -float(f.sweep.ray_time_s.max()))
        arrays, detail = source_temporal.diagnose(current, past[:4])
        cfg = SimpleNamespace(
            isolated_objects=IsolationConfig(weak_diagnostic_enabled=False),
            minimum_range_m=10000,
            maximum_range_m=float(current.sweep.ranges[-1]),
            protected_dbz=30,
        )
        protection = source_temporal.weather(current)
        geometry = inspect(
            current.sweep, cfg, {"CF_LEGACY_PROTECTED_MASK": protection}, quiet_snr_db=0
        )
        isolated = geometry.arrays["CF_ISO_ISOLATED_MASK"] == 1
        arrays["ST_RAW_ISOLATED_MASK"] = isolated.astype("uint8")
        output = args.output / (path.stem + ".npz")
        np.savez_compressed(output, **arrays)
        record = dict(
            snapshot=str(path),
            snapshot_sha256=current.source_sha256,
            local_time=metadata["local_time"],
            radar_id=current.radar_id,
            scan_id=current.scan_id,
            lineage_sha256=current.lineage_id,
            input_unchanged=current.digest == detail["current_raw_sha256"],
            **detail,
            isolated_gates=int(isolated.sum()),
            isolated_measured_past=int(
                (isolated & (arrays["ST_MEASURED_MASK"] == 1)).sum()
            ),
            isolated_matching_original_seed=int(
                (isolated & (arrays["ST_MATCH_MASK"] == 1)).sum()
            ),
            isolated_boundary_supported=int(
                (isolated & (arrays["ST_BOUNDARY_SUPPORTED_MASK"] == 1)).sum()
            ),
            diagnostics=str(output),
            diagnostics_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        )
        results.append(record)
        print(
            json.dumps(
                {
                    k: record[k]
                    for k in (
                        "radar_id",
                        "local_time",
                        "matched_gates",
                        "isolated_gates",
                        "isolated_matching_original_seed",
                        "isolated_boundary_supported",
                    )
                }
            ),
            flush=True,
        )
    from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion import (
        context,
        isolation_config,
        isolation_geometry,
    )
    from rainpulse_algo.radar.qc_engine.volume_review import data, geometry
    from rainpulse_algo.radar.qc_engine import raw_reuse

    report = dict(
        scope="past_original_seed_recurrence_not_published_QC_delta",
        product_writes=False,
        action_authority=False,
        independent_weather_truth=False,
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        modules={
            m.__name__: hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest()
            for m in (
                source_temporal,
                context,
                isolation_config,
                isolation_geometry,
                data,
                geometry,
                raw_reuse,
            )
        },
        results=results,
    )
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
