"""Bind actual normalized/QC bytes and expose the original ray/gate geometry."""

from pathlib import Path

import numpy as np
import zarr

from ...qc import load_qc_profile
from ..adapters import FIELD_NAMES, adapt_sweep
from .io import checked, digest, file_hash, load_npz
from .schema import Case


class FrozenCase:
    def __init__(self, manifest_path):
        path = Path(manifest_path).resolve()
        self.manifest_path = path
        self.manifest_sha256 = file_hash(path)
        self.spec = Case.model_validate_json(path.read_text())
        spec, base = self.spec, path.parent
        self.profile = load_qc_profile(checked(base, spec.profile), checked(base, spec.flags))
        if self.profile.pipeline_version != "qc-opensource-7.0.0":
            raise ValueError("V8 experiments currently require a frozen V7 baseline profile")
        self.normalized = zarr.open_group(
            str(checked(base, spec.normalized, directory=True)), mode="r"
        )
        self.qc = zarr.open_group(str(checked(base, spec.qc, directory=True)), mode="r")
        if self.normalized.attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
            raise ValueError("wrong normalized radar contract")
        if self.qc.attrs.get("contract_name") != "rainpulse.qc-radar-volume":
            raise ValueError("wrong baseline QC contract")
        for key, expected in (
            ("scan_id", spec.expected_scan_id),
            ("radar_id", spec.expected_radar_id),
        ):
            if str(self.normalized.attrs.get(key, "")).lower() != expected.lower():
                raise ValueError("normalized identity mismatch: " + key)
            if str(self.qc.attrs.get(key, "")).lower() != expected.lower():
                raise ValueError("QC identity mismatch: " + key)
        if str(self.qc.attrs.get("asset_id")) != spec.expected_qc_asset_id:
            raise ValueError("QC asset identity mismatch")
        if self.qc.attrs.get("qc_pipeline_version") != self.profile.pipeline_version:
            raise ValueError("QC pipeline mismatch")
        if self.qc.attrs.get("qc_parameters_sha256") != self.profile.parameters_hash:
            raise ValueError("QC parameter hash differs from baseline config")
        if self.qc.attrs.get("context_fingerprint") != spec.expected_context_fingerprint:
            raise ValueError("QC context fingerprint mismatch")
        if self.qc.attrs.get("flag_definition_version") != "qc-flags-v2":
            raise ValueError("unsupported baseline flags")
        self.context = {}
        if spec.context is not None:
            self.context = load_npz(checked(base, spec.context))
            if (
                str(self.context.pop("context_fingerprint", ""))
                != spec.expected_context_fingerprint
            ):
                raise ValueError("prepared context snapshot differs from frozen QC context")
        if not spec.sweeps or len(set(spec.sweeps)) != len(spec.sweeps):
            raise ValueError("unique selected cuts required")
        self.identity = digest(spec.model_dump(mode="json"))

    def verify_unchanged(self):
        if file_hash(self.manifest_path) != self.manifest_sha256:
            raise ValueError("case manifest changed during execution")
        for key in ("normalized", "qc", "profile", "flags", "context"):
            reference = getattr(self.spec, key)
            if reference is not None:
                checked(self.manifest_path.parent, reference, directory=key in {"normalized", "qc"})

    def sweep(self, name):
        if name not in self.spec.sweeps or name not in self.qc or name not in self.normalized:
            raise ValueError("cut not included in frozen manifest")
        native = adapt_sweep(self.normalized, name, self.profile)
        group = self.qc[name]
        for field in ("azimuth", "elevation", "range", "ray_time"):
            if not np.array_equal(self.normalized[name][field][:], group[field][:]):
                raise ValueError("normalized/QC coordinates differ: " + field)
        order = native.original_indices
        for field in FIELD_NAMES:
            key = field + "_RAW"
            if field in native.fields:
                if key not in group or not np.array_equal(
                    group[key][:][order], native.fields[field], equal_nan=True
                ):
                    raise ValueError("normalized/QC raw measurement mismatch: " + field)
        baseline_names = {
            "DBZH_RAW",
            "DBZH_QC",
            "DBZH_USABLE",
            "QC_ACTION",
            "QC_FLAGS",
            "VALID_MASK",
            "LOW_QUALITY_MASK",
            "QUALITY_INDEX",
            "QI_METEO",
            "QI_INTERFERENCE",
            "RFI_RISK_STATE",
            "RFI_QUARANTINE_MASK",
            "QPE_ELIGIBLE_MASK",
            "REFLECTIVITY_TRUST_MASK",
            "RHOHV_TRUST_MASK",
            "ZDR_TRUST_MASK",
            "SNR_TRUST_MASK",
            "PHIDP_TRUST_MASK",
            "VR_TRUST_MASK",
            "SW_TRUST_MASK",
        }
        baseline = {
            k: group[k][:][order]
            for k in baseline_names
            if k in group and group[k].shape == native.shape
        }
        required = (
            "QC_ACTION",
            "QC_FLAGS",
            "RFI_QUARANTINE_MASK",
            "QPE_ELIGIBLE_MASK",
            "QUALITY_INDEX",
            "VALID_MASK",
            "REFLECTIVITY_TRUST_MASK",
            "DBZH_USABLE",
        )
        if any(k not in baseline for k in required):
            raise ValueError("baseline has incomplete actions/quality")
        observed = native.field_available["DBZH"]
        if not np.array_equal(baseline["VALID_MASK"] == 1, observed):
            raise ValueError("baseline original validity mismatch")
        action = baseline["QC_ACTION"]
        q = baseline["RFI_QUARANTINE_MASK"] == 1
        eligible = baseline["QPE_ELIGIBLE_MASK"] == 1
        if (
            np.any(action > 3)
            or not np.array_equal(action == 3, ~observed)
            or np.any(q & ((action != 1) | ~observed | eligible))
            or np.any(eligible & ((action == 2) | ~observed))
        ):
            raise ValueError("invalid baseline action/eligibility semantics")
        context = {}
        for key in ("weather_support", "temporal_persistence", "temporal_samples"):
            entry = self.context.get(name + "__" + key)
            if entry is not None:
                if entry.shape != native.shape:
                    raise ValueError("context cut geometry mismatch")
                context[key] = entry[order]
        return native, baseline, context

    def metadata(self, name):
        s = self.spec
        return {
            "case_id": s.case_id,
            "case_identity": self.identity,
            "process_id": s.process_id,
            "partition": s.partition,
            "data_kind": s.data_kind,
            "scan_id": s.expected_scan_id,
            "radar_id": s.expected_radar_id,
            "qc_asset_id": s.expected_qc_asset_id,
            "normalized_sha256": s.normalized.sha256,
            "qc_sha256": s.qc.sha256,
            "context_fingerprint": s.expected_context_fingerprint,
            "sweep": name,
            "comparison": "fixed_published_V7_artifact_no_context_repreparation",
        }
