"""Engineering fixtures ONLY. Labels reflect injected classes, not weather validation."""

import csv
from pathlib import Path

import numpy as np
import zarr

from ...qc import load_qc_profile
from .io import file_hash, tree_hash, write_json
from .schema import Case


def make_case(root, repo, *, partition="inspect", index=0, process=None, missing=False):
    """Create real arrays/contracts for IO tests; baseline is synthetic ALL-KEEP.

    It is deliberately not called a V7 numerical replay or atmospheric simulation.
    """
    root, repo = Path(root), Path(repo)
    root.mkdir(parents=True, exist_ok=False)
    profile_source = repo / "configs/qc/fujian-qc-evidence-graph-v7.yaml"
    flags_source = repo / "configs/qc/flag-definitions-v2.yaml"
    (root / "v7.yaml").write_bytes(profile_source.read_bytes())
    (root / "flags.yaml").write_bytes(flags_source.read_bytes())
    profile = load_qc_profile(root / "v7.yaml", root / "flags.yaml")
    nr, ng = 180, 256
    az = np.arange(nr, dtype=float) * 2
    ranges = np.arange(ng, dtype=float) * 250 + 125
    el = np.full(nr, 0.5)
    time = np.datetime64("2026-08-28T00:00:00", "ns") + np.arange(nr) * np.timedelta64(1, "s")
    rng = np.random.default_rng(910 + index)
    z = np.full((nr, ng), -20, "float32")
    label = np.full((nr, ng), -1, "int8")
    # Spatially distinct synthetic archetypes; no claim these exhaust real cases.
    label[15:35, 45:210] = 0
    label[65:67, 45:210] = 1
    label[105:110, 45:210] = 2
    z[label == 0] = 20 + rng.normal(0, 2, (label == 0).sum())
    z[label == 1] = 55 + rng.normal(0, 1, (label == 1).sum())
    z[label == 2] = 37 + rng.normal(0, 5, (label == 2).sum())
    rho = np.full(z.shape, 0.985, "float32")
    rho[label == 1] = 0.72
    rho[label == 2] = 0.89
    zdr = np.full(z.shape, 0.5, "float32")
    zdr[label == 1] = 8
    zdr[label == 2] = 3
    snr = np.full(z.shape, 25, "float32")
    phase = np.broadcast_to(np.arange(ng) * 0.5, z.shape).astype("float32").copy()
    phase[label == 1] = (np.indices(z.shape)[1][label == 1] % 2) * 110
    velocity = np.full(z.shape, 5, "float32")
    sw = np.full(z.shape, 1, "float32")
    if missing:
        z[:10, :15] = np.nan
    observed = np.isfinite(z)
    matrices = {
        "DBZH": z,
        "RHOHV": rho,
        "ZDR": zdr,
        "SNR": snr,
        "PHIDP": phase,
        "VR": velocity,
        "SW": sw,
    }
    radar, scan = f"synthetic-radar-{index % 2}", f"synthetic-scan-{index}"
    fingerprint = f"{index + 1:064x}"
    n = zarr.open_group(str(root / "normalized.zarr"), mode="w")
    n.attrs.update(
        contract_name="rainpulse.normalized-radar-volume",
        asset_id=f"synthetic-input-{index}",
        radar_id=radar,
        scan_id=scan,
        source="synthetic_engineering_fixture",
    )
    q = zarr.open_group(str(root / "qc.zarr"), mode="w")
    q.attrs.update(
        contract_name="rainpulse.qc-radar-volume",
        asset_id=f"synthetic-qc-{index}",
        radar_id=radar,
        scan_id=scan,
        qc_pipeline_version=profile.pipeline_version,
        qc_parameters_sha256=profile.parameters_hash,
        context_fingerprint=fingerprint,
        flag_definition_version="qc-flags-v2",
        operational_eligible=False,
        source="synthetic_all_keep_baseline_NOT_actual_V7_replay",
    )
    n.array("sweep_number", np.array([0], "int32"))
    q.array("sweep_number", np.array([0], "int32"))
    a, b = n.create_group("sweep_000"), q.create_group("sweep_000")
    for name, arr in (("azimuth", az), ("elevation", el), ("range", ranges), ("ray_time", time)):
        a.array(name, arr)
        b.array(name, arr)
    for key, arr in matrices.items():
        a.array(key, arr)
        b.array(key + "_RAW", arr)
    baseline = {
        "DBZH_USABLE": np.where(observed, z, np.nan).astype("float32"),
        "DBZH_QC": z,
        "QC_ACTION": np.where(observed, 0, 3).astype("uint8"),
        "QC_FLAGS": np.zeros(z.shape, "uint32"),
        "QUALITY_INDEX": np.where(observed, 1, 0).astype("float32"),
        "RFI_QUARANTINE_MASK": np.zeros(z.shape, "uint8"),
        "RFI_RISK_STATE": np.zeros(z.shape, "uint8"),
        "LOW_QUALITY_MASK": np.zeros(z.shape, "uint8"),
    }
    for key in (
        "VALID_MASK",
        "QPE_ELIGIBLE_MASK",
        "REFLECTIVITY_TRUST_MASK",
        "RHOHV_TRUST_MASK",
        "ZDR_TRUST_MASK",
        "SNR_TRUST_MASK",
        "PHIDP_TRUST_MASK",
        "VR_TRUST_MASK",
        "SW_TRUST_MASK",
    ):
        baseline[key] = observed.astype("uint8")
    for key in ("QI_METEO", "QI_INTERFERENCE"):
        baseline[key] = np.where(observed, 1, np.nan).astype("float32")
    for key, arr in baseline.items():
        b.array(key, arr)
    refs = {
        key: {
            "path": name,
            "sha256": tree_hash(root / name)
            if key in {"qc", "normalized"}
            else file_hash(root / name),
        }
        for key, name in (
            ("qc", "qc.zarr"),
            ("normalized", "normalized.zarr"),
            ("profile", "v7.yaml"),
            ("flags", "flags.yaml"),
        )
    }
    spec = Case(
        **refs,
        case_id=f"case-{index}",
        process_id=process or f"process-{index}",
        partition=partition,
        data_kind="synthetic",
        expected_qc_asset_id=f"synthetic-qc-{index}",
        expected_scan_id=scan,
        expected_radar_id=radar,
        expected_context_fingerprint=fingerprint,
        sweeps=("sweep_000",),
    )
    write_json(root / "case.json", spec.model_dump(mode="json"))
    with (root / "labels.csv").open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ray", "gate", "label"])
        for cls, name in enumerate(("weather", "interference", "mixed")):
            coords = np.argwhere(label == cls)
            selected = coords[rng.choice(len(coords), min(120, len(coords)), replace=False)]
            for ray, gate in selected:
                writer.writerow([int(ray), int(gate), name])
    return root / "case.json"
