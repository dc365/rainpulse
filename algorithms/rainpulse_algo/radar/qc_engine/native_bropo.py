"""Optional native bRopo detector execution, NOT a Python reimplementation.

Independent reference only; no restored DBZH is ever requested. Native dependency
absence is reported as not_executed (no zero mask or fabricated score). First
adapter intentionally accepts uniform, complete PPIs only; missing/sector
semantics must be validated before expanding this native reference boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path

import numpy as np
import zarr

from ..qc import load_qc_profile
from .adapters import adapt_sweep

NAMES = ("_polarscan", "_polarscanparam", "_fmiimage", "_ropogenerator")


def native_emitters(
    native,
    *,
    emitter_args=(-10, 20),
    emitter2_args=(-10, 20, 2),
    threshold_code=128,
    source_revision=None,
):
    """Call documented native APIs with explicit integer, native-unit arguments."""
    if (
        len(emitter_args) != 2
        or len(emitter2_args) != 3
        or any(type(x) is not int for x in (*emitter_args, *emitter2_args))
        or any(x < 1 for x in (*emitter_args[1:], *emitter2_args[1:]))
        or not -32 <= emitter_args[0] <= 80
        or not -32 <= emitter2_args[0] <= 80
        or type(threshold_code) is not int
        or not 1 <= threshold_code <= 255
    ):
        raise ValueError("explicit supported native integer arguments required")
    spacing = float(native.audit["azimuth_spacing_deg"])
    gaps = np.diff(np.r_[native.azimuth, native.azimuth[0] + 360])
    if (
        not native.full_ppi
        or not native.geometry_good.all()
        or not np.allclose(gaps, spacing, atol=0.05)
        or not native.field_available["DBZH"].all()
    ):
        return dict(
            status="not_executed_unsupported_native_geometry",
            reason="first reference adapter requires complete measured uniform PPI",
            masks=None,
            raw_scores=None,
            operational_eligible=False,
        )
    modules = {}
    try:
        modules = {name: importlib.import_module(name) for name in NAMES}
    except (ImportError, OSError) as error:
        return dict(
            status="not_executed_native_dependencies",
            reason=str(error),
            masks=None,
            raw_scores=None,
            operational_eligible=False,
        )
    if (
        source_revision is None
        or len(source_revision) != 40
        or any(x not in "0123456789abcdef" for x in source_revision)
    ):
        raise ValueError("declare frozen bRopo source revision before executing native code")
    values = native.fields["DBZH"]
    # Quantization is reference input only. No clamping, no source modification.
    encoded = np.rint((values + 33) / 0.5)
    if not np.isfinite(encoded).all() or np.any((encoded < 2) | (encoded > 254)):
        raise ValueError("DBZH cannot be represented without native input clipping")
    pad = max(8, emitter2_args[2] * 2)
    if pad >= native.shape[0] or max(emitter_args[1], emitter2_args[1]) > native.shape[1]:
        raise ValueError("native detector stencil exceeds reference PPI")
    image_data = np.pad(encoded.astype("uint8"), ((pad, pad), (0, 0)), mode="wrap")
    masks, scores = {}, {}
    for method, args in [("emitter", emitter_args), ("emitter2", emitter2_args)]:
        parameter = modules["_polarscanparam"].new()
        parameter.quantity = "DBZH"
        parameter.gain, parameter.offset = 0.5, -33.0
        parameter.nodata, parameter.undetect = 0.0, 1.0
        parameter.setData(image_data.copy())
        scan = modules["_polarscan"].new()
        scan.addParameter(parameter)
        scan.elangle = float(np.deg2rad(np.median(native.elevation)))
        scan.rscale = float(native.gate_spacing_m)
        image = modules["_fmiimage"].fromRave(scan, "DBZH")
        generator = modules["_ropogenerator"].new(image)
        getattr(generator, method)(*args)
        field = generator.classify().classification.toRaveField()
        score = np.asarray(field.getData())
        if score.shape != image_data.shape or not np.isfinite(score).all():
            raise ValueError("native bRopo classification shape/values differ")
        score = score[pad:-pad].copy()
        if np.any((score < 0) | (score > 255)):
            raise ValueError("native classification code outside byte domain")
        # Return ORIGINAL ray order just like the Worker artifacts.
        scores[method] = native.restore(score)
        masks[method] = native.restore((score >= threshold_code).astype("uint8"))
    return dict(
        status="computed_native_reference",
        masks=masks,
        raw_scores=scores,
        declared_source_revision=source_revision,
        binaries={
            name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
            for name, module in modules.items()
        },
        arguments=dict(
            emitter=list(emitter_args),
            emitter2=list(emitter2_args),
            threshold_code=threshold_code,
            padded_rays=pad,
        ),
        input_quantization_db=0.5,
        restored_reflectivity_used=False,
        semantics="raw_native_candidate_not_final_rejection_not_calibrated_probability",
        operational_eligible=False,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--normalized-zarr", type=Path, required=True)
    p.add_argument("--profile", type=Path, required=True)
    p.add_argument("--flags", type=Path, required=True)
    p.add_argument("--sweep", default="sweep_000")
    p.add_argument("--source-revision")
    p.add_argument("--emitter-args", nargs=2, type=int, default=(-10, 20))
    p.add_argument("--emitter2-args", nargs=3, type=int, default=(-10, 20, 2))
    p.add_argument("--threshold-code", type=int, default=128)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists() or not a.normalized_zarr.is_dir():
        raise ValueError("existing normalized directory and new output directory required")
    root = zarr.open_group(str(a.normalized_zarr), mode="r")
    profile = load_qc_profile(a.profile, a.flags)
    native = adapt_sweep(root, a.sweep, profile)
    result = native_emitters(
        native,
        emitter_args=tuple(a.emitter_args),
        emitter2_args=tuple(a.emitter2_args),
        threshold_code=a.threshold_code,
        source_revision=a.source_revision,
    )
    arrays = {}
    for kind in ("masks", "raw_scores"):
        arrays.update({kind + "_" + key: value for key, value in (result.pop(kind) or {}).items()})
    digest = hashlib.sha256()
    for name in ("azimuth", "range", "DBZH"):
        value = root[a.sweep][name][:]
        digest.update(name.encode())
        digest.update(str((value.dtype.str, value.shape)).encode())
        digest.update(value.tobytes())
    result["input_array_sha256"] = digest.hexdigest()
    result["sweep"] = a.sweep
    result["input_root_metadata_sha256"] = hashlib.sha256(
        (a.normalized_zarr / ".zattrs").read_bytes()
    ).hexdigest()
    a.output.mkdir(parents=True)
    if arrays:
        np.savez_compressed(a.output / "native-fields.npz", **arrays)
        result["fields_sha256"] = hashlib.sha256(
            (a.output / "native-fields.npz").read_bytes()
        ).hexdigest()
    (a.output / "receipt.json").write_text(json.dumps(result, allow_nan=False, indent=2))
    print(json.dumps({"status": result["status"], "output": str(a.output), "published": False}))


if __name__ == "__main__":
    main()
