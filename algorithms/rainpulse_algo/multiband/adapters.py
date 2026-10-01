# ruff: noqa: E501, I001
"""Format adapters. Waveband is NOT a file format. No vendor bytes are guessed."""

from __future__ import annotations

from rainpulse_algo.performance import (timed as _perf_timed)

import numpy as np

from .attenuation import copy_path_provenance, PATH_INPUT_FIELDS
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

# The cut budget includes these actual optional matrices; no missing values are filled.
X_QC_FIELDS |= {"ZDR", "VR", "SW", "MIXED_WEATHER_MASK"}
X_QC_FIELDS |= PATH_INPUT_FIELDS
X_QC_FIELDS |= {name + suffix for name in ("DBZH", "SNR", "SNRH", "RHOHV", "ZDR", "PHIDP", "VR", "SW")
                for suffix in ("_AVAILABLE_MASK", "_VALID_MASK")}


def _station_registered_calibration(attrs, station) -> str:
    """Volume calibration identity, inheriting a verified station registration.

    A station whose calibration has been verified through a registered study
    (e.g. cross-calibration against the network reference) extends that
    identity to volumes that declare none. Volumes carrying their own explicit
    identity are never overridden, so mid-stream calibration changes remain
    detectable through the mismatch path.
    """
    declared = attrs.get("calibration_id", "unverified")
    if station.calibration_verified and declared == "unverified":
        return station.calibration_id
    return declared


def _normalized_attenuation_status(attrs, station) -> str:
    """Upstream attenuation provenance for adapter-loaded volumes.

    The normalized-radar-volume contract publishes decoded, uncorrected
    moments: no attenuation correction exists anywhere in the decode path, so
    volumes of this source that declare no explicit status are raw by
    contract. Explicit declarations are never overridden.
    """
    declared = attrs.get("attenuation_status")
    if declared is not None:
        return declared
    return "raw" if station.source == "normalized_zarr" else "unknown"


def _normalized_phase_anchor(attrs, station) -> tuple[bool, float | None]:
    """First-gate PIA anchor for adapter-loaded volumes.

    The Z-Phi recursion starts at the first sampled gate; for the normalized
    contract that gate sits at metres-scale range where accumulated
    attenuation is zero by construction. Volumes declaring their own anchor
    values are never overridden.
    """
    verified = attrs.get("phase_anchor_verified")
    if verified is None:
        verified = station.source == "normalized_zarr"
    pia = attrs.get("pia_at_first_gate_db")
    if pia is None and station.source == "normalized_zarr":
        pia = 0.0
    return verified is True, pia


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


def _native_cut_sampling(attrs, group):
    """Preserve checked cut identity; never promote opaque waveform semantics."""
    record = group.attrs.get("native_cut_sampling")
    if record is None:
        return None
    if (not isinstance(record, dict)
            or record.get("version") != "native-cut-sampling-v1"
            or record.get("semantic_verification") is not False):
        raise ValueError("invalid native cut sampling record")
    raw_sha = record.get("input_sha256")
    if (not isinstance(raw_sha, str) or len(raw_sha) != 64
            or any(c not in "0123456789abcdef" for c in raw_sha)
            or raw_sha != attrs.get("input_sha256")
            or not record.get("radar_config_version")
            or record["radar_config_version"] != attrs.get("radar_config_version")
            or record.get("source_sweep_number") != group.attrs.get("source_sweep_number")):
        raise ValueError("native cut sampling identity differs from source")
    for key in ("source_sweep_number", "process_mode_code", "waveform_code"):
        if type(record.get(key)) is not int:
            raise ValueError("native cut sampling integer code required")
    for key in ("dealiasing_mode_code", "sample_count1", "sample_count2", "phase_mode_code"):
        if key in record and type(record[key]) is not int:
            raise ValueError("native cut sampling integer processing measurement required")
    if "atmospheric_loss_db_per_km" in record:
        value = record["atmospheric_loss_db_per_km"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError("native cut sampling finite processing measurement required")
    for key in ("prf1_hz", "prf2_hz", "log_resolution_m", "doppler_resolution_m", "nyquist_velocity_m_s"):
        value = record.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
            raise ValueError("native cut sampling finite measurement required")
    if ("nyquist_velocity_m_s" in group.attrs
            and record["nyquist_velocity_m_s"] != group.attrs["nyquist_velocity_m_s"]):
        raise ValueError("native cut sampling Nyquist differs from sweep")
    return dict(record)


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
    if station.source == "normalized_zarr" and attrs.get("radar_band") != station.band:
        raise ValueError("native radar band differs from frozen station configuration")
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
    sampling = {}
    byte_count, gate_count = 0, 0
    for number in numbers[:]:
        number = int(number)
        g = root[f"sweep_{number:03d}"]
        field = "DBZH_RAW" if station.source == "s_qc_zarr" else "DBZH"
        if field not in g:
            continue  # No reflectivity is manufactured for Doppler-only cuts.
        native_sampling = _native_cut_sampling(attrs, g)
        if native_sampling is not None:
            sampling[str(number)] = native_sampling
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
        names = set(X_QC_FIELDS if station.band == "X" else FIELDS)
        if station.source == "s_qc_zarr":
            names |= {name for name in g.array_keys() if name.endswith("CR_WITHHELD_MASK")}
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
        "calibration_id": _station_registered_calibration(attrs, station),
        "attenuation_status": _normalized_attenuation_status(attrs, station),
        "radar_config_version": attrs.get("radar_config_version"),
        "clutter_context_contract": attrs.get("clutter_context_contract", {}),
        "doppler_verification_id": attrs.get("doppler_verification_id"),
        "doppler_waveform": attrs.get("doppler_waveform"),
        "nyquist_velocity_mps": attrs.get("nyquist_velocity_mps"),
        "phase_anchor_verified": _normalized_phase_anchor(attrs, station)[0],
        "pia_at_first_gate_db": _normalized_phase_anchor(attrs, station)[1],
        "qc_pipeline_version": attrs.get("qc_pipeline_version"),
        "no_echo_semantics": "explicit_mask_or_unknown_no_return",
    }
    if sampling:
        metadata["native_cut_sampling"] = sampling
    copy_path_provenance(attrs, metadata)
    # If a precise ingest availability exists it may strengthen, never weaken,
    # the catalog's availability cutoff.
    if attrs.get("ingest_available_at_utc") and epoch(attrs["ingest_available_at_utc"]) > epoch(
        metadata["available_at"]
    ):
        metadata["available_at"] = attrs["ingest_available_at_utc"]
    return Volume(metadata, result)


@_perf_timed("input.decode_adapt")
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


@_perf_timed("input.decode_adapt_x")
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
    if attrs.get("radar_band") != station.band:
        raise ValueError("native radar band differs from frozen station configuration")
    if str(attrs.get("radar_id")) != station.radar_id or str(attrs.get("scan_id")) != source["scan_id"]:
        raise ValueError("stored observation identity differs from frozen catalog selection")
    group_name = f"sweep_{sweep_number:03d}"
    if group_name not in root:
        raise ValueError("native sweep is absent")
    group = root[group_name]
    if "DBZH" not in group:
        return None, 0
    native_sampling = _native_cut_sampling(attrs, group)
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
                "calibration_id": _station_registered_calibration(attrs, station),
                "attenuation_status": _normalized_attenuation_status(attrs, station),
        "radar_config_version": attrs.get("radar_config_version"),
        "clutter_context_contract": attrs.get("clutter_context_contract", {}),
        "doppler_verification_id": attrs.get("doppler_verification_id"),
        "doppler_waveform": attrs.get("doppler_waveform"),
        "nyquist_velocity_mps": attrs.get("nyquist_velocity_mps"),
                "phase_anchor_verified": _normalized_phase_anchor(attrs, station)[0],
                "pia_at_first_gate_db": _normalized_phase_anchor(attrs, station)[1],
                "no_echo_semantics": "explicit_mask_or_unknown_no_return"}
    if native_sampling is not None:
        metadata["native_cut_sampling"] = {str(sweep_number): native_sampling}
    copy_path_provenance(attrs, metadata)
    sweep = Sweep(sweep_number, np.asarray(group["azimuth"][:], dtype=float), np.asarray(group["range"][:], dtype=float),
                  np.asarray(group["elevation"][:], dtype=float),
                  ray_seconds(np.asarray(group["ray_time"][:]), dict(group["ray_time"].attrs).get("units")), fields)
    return Volume(metadata, [sweep]), byte_count
