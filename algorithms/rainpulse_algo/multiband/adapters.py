# ruff: noqa: E501, I001
"""Format adapters. Waveband is NOT a file format. No vendor bytes are guessed."""

from __future__ import annotations

import numpy as np

from .codec import decode_volume
from .quality import _shared
from .model import MAX_GATES, MAX_SWEEPS, Station, Sweep, Volume, epoch

FIELDS = {
    "DBZH",
    "DBZH_RAW",
    "DBZH_QC",
    "OBSERVED_MASK",
    "NO_ECHO_MASK",
    "VALID_MASK",
    "RHOHV",
    "ZDR",
    "PHIDP",
    "KDP",
    "SNRH",
    "SNR",
    "VR",
    "SW",
    "PHASE_VALID_MASK",
    "LIQUID_MASK",
    "ATTENUATION_VALID_MASK",
    "ATTENUATION_UNRELIABLE_MASK",
    "PIA_DB",
    "CONFIRMED_NONMET_MASK",
    "WEATHER_PROTECTED_MASK",
    "BLOCKAGE_FRACTION",
    "QUALITY_INDEX",
    "QC_FLAGS",
    "REFLECTIVITY_ELIGIBLE_FOR_CR",
    "CR_UNCERTAIN_MASK",
    "RHOHV_RAW",
    "SNR_RAW",
    "NMR_PROTECTED_MASK",
    "CF_HARD_WEATHER_MASK",
    "CF_LOCAL_WEATHER_MASK",
    "CF_BG_MATCH_MASK",
    "CF_BG_STABLE_MASK",
    "CF_BG_DBZH_DEPARTURE_DB",
    "NMR_LOW_SNR_UNCERTAIN_MASK",
    "NMR_NONMET_CANDIDATE_MASK",
    "NMR_NONMET_FRACTION",
    "NMR_POL_SAMPLE_COUNT",
}
X_QC_FIELDS = {
    "DBZH", "OBSERVED_MASK", "NO_ECHO_MASK", "VALID_MASK", "RHOHV", "PHIDP", "SNRH", "SNR",
    "PHASE_VALID_MASK", "LIQUID_MASK", "ATTENUATION_VALID_MASK", "ATTENUATION_UNRELIABLE_MASK",
    "PIA_DB", "CONFIRMED_NONMET_MASK", "WEATHER_PROTECTED_MASK", "BLOCKAGE_FRACTION",
}


def ray_seconds(values: np.ndarray, units: str | None) -> np.ndarray:
    if values.dtype.kind == "M":
        if np.isnat(values).any():
            raise ValueError("native ray time contains NaT")
        return values.astype("datetime64[ns]").astype(np.float64) / 1e9
    if values.dtype.kind not in "fiu":
        raise ValueError("ray time must be CF numeric time or datetime64")
    # Existing RainPulse native contract declares numeric ray_time in Unix
    # seconds even if the per-array units attribute is absent.
    if not units:
        return values.astype(np.float64)
    unit, sep, origin = units.partition(" since ")
    scales = {"seconds": 1.0, "milliseconds": 0.001, "microseconds": 1e-6, "nanoseconds": 1e-9}
    if not sep or unit not in scales:
        raise ValueError("unsupported ray time units")
    origin = origin.replace(" ", "T", 1)
    # CF epochs commonly omit a timezone but are declared UTC by the contract.
    if origin.endswith(" UTC"):
        origin = origin[:-4] + "Z"
    if origin == "1970-01-01T00:00:00":
        origin += "Z"
    return values.astype(np.float64) * scales[unit] + epoch(origin)


def from_group(
    root,
    station: Station,
    source: dict,
    *,
    asset_sha256: str,
    maximum_bytes: int,
    s_reject_mask: int = 0,
    expected_flag_version: str = "",
) -> Volume:
    attrs = dict(root.attrs)
    expected_contract = (
        "rainpulse.qc-radar-volume"
        if station.source == "s_qc_zarr"
        else "rainpulse.normalized-radar-volume"
    )
    if attrs.get("contract_name") != expected_contract:
        raise ValueError("selected format adapter and input contract differ")
    if (
        str(attrs.get("radar_id")) != station.radar_id
        or str(attrs.get("scan_id")) != source["scan_id"]
    ):
        raise ValueError("stored observation identity differs from frozen catalog selection")
    if station.source == "s_qc_zarr" and (
        not expected_flag_version
        or attrs.get("flag_definition_version") != expected_flag_version
        or s_reject_mask <= 0
    ):
        raise ValueError("legacy S input requires a matching frozen hard-reject flag definition")
    numbers = root["sweep_number"]
    if np.prod(numbers.shape) > MAX_SWEEPS:
        raise ValueError("too many native sweeps")
    result = []
    byte_count, gate_count = 0, 0
    for number in numbers[:]:
        number = int(number)
        g = root[f"sweep_{number:03d}"]
        field = "DBZH_RAW" if station.source == "s_qc_zarr" else "DBZH"
        if field not in g:
            continue  # No reflectivity is manufactured for Doppler-only cuts.
        arr = g[field]
        if (
            len(arr.shape) != 2
            or not 1 <= arr.shape[0] <= 4096
            or not 1 <= arr.shape[1] <= 16384
        ):
            raise ValueError("unexpected native moment dimensions")
        gate_count += int(np.prod(arr.shape))
        if gate_count > MAX_GATES:
            raise ValueError("native volume exceeds decoded gate budget")
        names = FIELDS | {name for name in g.array_keys() if name.endswith("CR_WITHHELD_MASK")}
        to_read = {name for name in names if name in g}
        for key in (*to_read, "azimuth", "range", "elevation", "ray_time"):
            a = g[key]
            dtype = np.dtype(a.dtype)
            expected_shape = (
                arr.shape
                if key in to_read
                else (arr.shape[1],)
                if key == "range"
                else (arr.shape[0],)
            )
            if tuple(a.shape) != tuple(expected_shape):
                raise ValueError("native field shape differs from reflectivity coordinates")
            if dtype.kind not in "buifM" or len(a.shape) > 2:
                raise ValueError("unsupported native array type")
            byte_count += int(np.prod(a.shape)) * dtype.itemsize
            if byte_count > maximum_bytes:
                raise ValueError("selected decoded fields exceed memory budget")
        fields = {name: np.asarray(g[name][:]) for name in to_read}
        fields["DBZH"] = _shared(fields[field])
        if "SNRH" not in fields and "SNR" in fields:
            fields["SNRH"] = _shared(fields["SNR"])
        noecho = (
            fields["NO_ECHO_MASK"] if "NO_ECHO_MASK" in fields else np.zeros(arr.shape, np.uint8)
        )
        if "OBSERVED_MASK" not in fields:
            # No-return semantics are not inferred from a missing/NaN value.
            obs = np.isfinite(fields["DBZH"]) | (noecho == 1)
            if "VALID_MASK" in fields:
                obs &= fields["VALID_MASK"] == 1
            fields["OBSERVED_MASK"] = obs.astype(np.uint8)
        fields["NO_ECHO_MASK"] = noecho
        if station.source == "s_qc_zarr":
            if "QC_FLAGS" not in fields or "REFLECTIVITY_ELIGIBLE_FOR_CR" not in fields:
                raise ValueError("S QC input lacks CR admission evidence")
            withheld = (fields["QC_FLAGS"].astype(np.uint32) & np.uint32(s_reject_mask)) != 0
            for key in to_read:
                if key.endswith("CR_WITHHELD_MASK"):
                    withheld |= fields[key] == 1
            if attrs.get("qc_near_measurement_sha256") is not None:
                # Adapter retains the existing S-only gate veto. The generic
                # fusion engine does not know any of these internal QC branches.
                from rainpulse_algo.radar.qc_engine.volume_review.near_object import (
                    evaluate_near_object,
                )

                legacy = {**fields, "range": g["range"][:], "azimuth": g["azimuth"][:]}
                withheld |= evaluate_near_object(legacy, near_active=True)
            fields["CR_WITHHELD_MASK"] = withheld.astype(np.uint8)
        result.append(
            Sweep(
                number,
                np.asarray(g["azimuth"][:], dtype=float),
                np.asarray(g["range"][:], dtype=float),
                np.asarray(g["elevation"][:], dtype=float),
                ray_seconds(np.asarray(g["ray_time"][:]), dict(g["ray_time"].attrs).get("units")),
                fields,
            )
        )
    if not result:
        raise ValueError("no usable native reflectivity sweep")
    start = source["volume_start"]
    end = source["volume_end"]
    for key, expected in (("volume_start_time_utc", start), ("volume_end_time_utc", end)):
        if attrs.get(key) is not None and epoch(attrs[key]) != epoch(expected):
            raise ValueError("native time and catalog time differ")
    metadata = {
        "radar_id": station.radar_id,
        "scan_id": source["scan_id"],
        "band": station.band,
        "frequency_hz": attrs.get("frequency_hz", station.frequency_hz),
        "frequency_origin": "native_header"
        if "frequency_hz" in attrs
        else "verified_station_configuration",
        "altitude_m_msl": station.altitude_m_msl,
        "height_datum": "MSL",
        "height_origin": "verified_station_configuration",
        "longitude_deg": attrs.get("site_longitude_deg", station.longitude_deg),
        "latitude_deg": attrs.get("site_latitude_deg", station.latitude_deg),
        "volume_start": start,
        "volume_end": end,
        "available_at": source["available_at"],
        "asset_sha256": asset_sha256,
        "scan_type": attrs.get("scan_type", "volume"),
        "scan_type_origin": "native"
        if "scan_type" in attrs
        else "catalog_volume_unverified_completeness",
        "calibration_id": attrs.get("calibration_id", "unverified"),
        "attenuation_status": attrs.get("attenuation_status", "unknown"),
        "phase_anchor_verified": attrs.get("phase_anchor_verified") is True,
        "pia_at_first_gate_db": attrs.get("pia_at_first_gate_db"),
        "qc_pipeline_version": attrs.get("qc_pipeline_version"),
        "no_echo_semantics": "explicit_mask_or_unknown_no_return",
    }
    # If a precise ingest availability exists it may strengthen, never weaken,
    # the catalog's availability cutoff.
    if attrs.get("ingest_available_at_utc") and epoch(attrs["ingest_available_at_utc"]) > epoch(
        metadata["available_at"]
    ):
        metadata["available_at"] = attrs["ingest_available_at_utc"]
    return Volume(metadata, result)


def read_volume(
    objects: dict[str, bytes],
    station: Station,
    source: dict,
    *,
    asset_sha256: str,
    maximum_bytes: int,
    s_reject_mask: int = 0,
    expected_flag_version: str = "",
) -> Volume:
    if station.source == "native_bundle":
        v = decode_volume(objects, maximum_bytes=maximum_bytes, asset_sha256=asset_sha256)
        if (
            v.metadata["scan_id"] != source["scan_id"]
            or epoch(v.metadata["volume_end"]) != epoch(source["volume_end"])
            or epoch(v.metadata["volume_start"]) != epoch(source["volume_start"])
        ):
            raise ValueError("native bundle identity differs from selected scan")
        # An explicit later catalog availability cannot be ignored by replay.
        if epoch(source["available_at"]) > epoch(v.metadata["available_at"]):
            v.metadata["available_at"] = source["available_at"]
        return v
    import zarr
    from zarr.storage import MemoryStore

    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    return from_group(
        root,
        station,
        source,
        asset_sha256=asset_sha256,
        maximum_bytes=maximum_bytes,
        s_reject_mask=s_reject_mask,
        expected_flag_version=expected_flag_version,
    )


def read_x_qc_sweep(objects: dict[str, bytes], station: Station, source: dict, sweep_number: int, *, asset_sha256: str, maximum_bytes: int) -> tuple[Volume | None, int]:
    """Decode one X sweep and only the moments consumed by standalone QC.

    The caller bounds the sum of decoded bytes across a full volume. Keeping a
    single sweep resident makes 39/40-cut PPI previews practical in the Worker.
    """
    if station.band != "X" or station.source != "normalized_zarr":
        raise ValueError("sweep streaming requires a normalized X Zarr source")
    import zarr
    from zarr.storage import MemoryStore
    store = MemoryStore()
    store.update(objects)
    root = zarr.open_group(store=store, mode="r")
    attrs = dict(root.attrs)
    if attrs.get("contract_name") != "rainpulse.normalized-radar-volume":
        raise ValueError("selected X adapter and input contract differ")
    if str(attrs.get("radar_id")) != station.radar_id or str(attrs.get("scan_id")) != source["scan_id"]:
        raise ValueError("stored observation identity differs from frozen catalog selection")
    group_name = f"sweep_{sweep_number:03d}"
    if group_name not in root:
        raise ValueError("native sweep is absent")
    group = root[group_name]
    if "DBZH" not in group:
        return None, 0
    reflectivity = group["DBZH"]
    if (
        len(reflectivity.shape) != 2
        or not 1 <= reflectivity.shape[0] <= 4096
        or not 1 <= reflectivity.shape[1] <= 16384
    ):
        raise ValueError("unexpected native moment dimensions")
    gates = int(np.prod(reflectivity.shape))
    if gates > MAX_GATES:
        raise ValueError("native sweep exceeds decoded gate budget")
    names = {name for name in X_QC_FIELDS if name in group}
    fields_to_read = names | {"DBZH"}
    expected_shapes = {
        **{key: reflectivity.shape for key in fields_to_read},
        "azimuth": (reflectivity.shape[0],),
        "elevation": (reflectivity.shape[0],),
        "ray_time": (reflectivity.shape[0],),
        "range": (reflectivity.shape[1],),
    }
    byte_count = 0
    for key, expected_shape in expected_shapes.items():
        array = group[key]
        dtype = np.dtype(array.dtype)
        if tuple(array.shape) != tuple(expected_shape):
            raise ValueError("native X array shape differs from DBZH coordinates")
        if dtype.kind not in "buifM" or len(array.shape) > 2:
            raise ValueError("unsupported native array type")
        byte_count += int(np.prod(array.shape)) * dtype.itemsize
        if byte_count > maximum_bytes:
            raise ValueError("selected decoded X sweep exceeds memory budget")
    fields = {name: np.asarray(group[name][:]) for name in fields_to_read}
    if "SNRH" not in fields and "SNR" in fields:
        fields["SNRH"] = fields["SNR"]
    noecho = fields.get("NO_ECHO_MASK", np.zeros(reflectivity.shape, np.uint8))
    if "OBSERVED_MASK" not in fields:
        observed = np.isfinite(fields["DBZH"]) | (noecho == 1)
        if "VALID_MASK" in fields:
            observed &= fields["VALID_MASK"] == 1
        fields["OBSERVED_MASK"] = observed.astype(np.uint8)
    fields["NO_ECHO_MASK"] = noecho
    start, end = source["volume_start"], source["volume_end"]
    for key, expected in (("volume_start_time_utc", start), ("volume_end_time_utc", end)):
        if attrs.get(key) is not None and epoch(attrs[key]) != epoch(expected):
            raise ValueError("native time and catalog time differ")
    metadata = {"radar_id": station.radar_id, "scan_id": source["scan_id"], "band": "X",
                "frequency_hz": attrs.get("frequency_hz", station.frequency_hz),
                "altitude_m_msl": station.altitude_m_msl, "height_datum": "MSL",
                "longitude_deg": attrs.get("site_longitude_deg", station.longitude_deg),
                "latitude_deg": attrs.get("site_latitude_deg", station.latitude_deg),
                "volume_start": start, "volume_end": end, "available_at": source["available_at"],
                "asset_sha256": asset_sha256, "scan_type": attrs.get("scan_type", "volume"),
                "calibration_id": attrs.get("calibration_id", "unverified"),
                "attenuation_status": attrs.get("attenuation_status", "unknown"),
                "phase_anchor_verified": attrs.get("phase_anchor_verified") is True,
                "pia_at_first_gate_db": attrs.get("pia_at_first_gate_db"),
                "no_echo_semantics": "explicit_mask_or_unknown_no_return"}
    sweep = Sweep(sweep_number, np.asarray(group["azimuth"][:], dtype=float), np.asarray(group["range"][:], dtype=float),
                  np.asarray(group["elevation"][:], dtype=float),
                  ray_seconds(np.asarray(group["ray_time"][:]), dict(group["ray_time"].attrs).get("units")), fields)
    return Volume(metadata, [sweep]), byte_count
