from __future__ import annotations

import bz2
import hashlib
import re
import struct
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import numpy as np

from .config import FieldMapping, RadarDecoderConfig

DECODER_ID = "rainpulse.cma-rstm"
DECODER_VERSION = "cma-rstm-2.1.0"
ABSENT_RAW_GATE_CODE = np.uint32(np.iinfo("uint32").max)
MAGIC_NUMBER = 0x4D545352

GENERIC_HEADER = struct.Struct("<Ihhii16s")
SITE_CONFIG = struct.Struct("<8s32sffii fff i hhhhh 46s")
TASK_CONFIG = struct.Struct("<32s128siiiii 9f 40s")
CUT_CONFIG = struct.Struct("<ii ff i ffffff ii iii iii ff qq i 7f 4s iiiii 12s ii hhhh 72s")
RADIAL_HEADER = struct.Struct("<iiiii ff iiii hhh c 13s")
MOMENT_HEADER = struct.Struct("<iii hh i 12s")

SOURCE_MOMENT_NAMES = {
    1: "TREF",
    2: "REF",
    3: "VEL",
    4: "SW",
    5: "SQI",
    6: "CPA",
    7: "ZDR",
    8: "LDR",
    9: "RHO",
    10: "PHI",
    11: "KDP",
    12: "CP",
    14: "HCL",
    15: "CF",
    16: "SNRH",
    17: "SNRV",
}


class DecodeError(ValueError):
    """Raised when an RSTM volume violates its declared structure."""


@dataclass(frozen=True)
class SourceSite:
    code: str
    name: str
    latitude_deg: float
    longitude_deg: float
    antenna_altitude_m: int
    ground_altitude_m: int
    frequency_mhz: float
    beam_width_horizontal_deg: float
    beam_width_vertical_deg: float
    rda_version: int
    radar_type_code: int


@dataclass(frozen=True)
class SourceTask:
    name: str
    description: str
    polarization_type: int
    scan_type: int
    pulse_width_ns: int
    scan_start_time: datetime
    cut_number: int


@dataclass(frozen=True)
class SourceCut:
    number: int
    process_mode: int
    waveform: int
    prf1_hz: float
    prf2_hz: float
    nominal_elevation_deg: float
    angular_resolution_deg: float
    scan_speed_deg_s: float
    log_resolution_m: int
    doppler_resolution_m: int
    max_range1_m: int
    max_range2_m: int
    start_range_m: int
    nyquist_velocity_m_s: float
    moments_mask: int


@dataclass(frozen=True)
class FieldMetadata:
    mapping: FieldMapping
    source_code: int
    raw_scale: int
    raw_offset: int
    raw_bin_length: int
    source_flags: int


@dataclass(frozen=True)
class DecodedSweep:
    source_sweep_number: int
    nominal_elevation_deg: float
    azimuth_deg: np.ndarray
    elevation_deg: np.ndarray
    ray_time: np.ndarray
    horizontal_noise_dbm: np.ndarray
    vertical_noise_dbm: np.ndarray
    range_m: np.ndarray
    fields: dict[str, np.ndarray]
    raw_gate_codes: dict[str, np.ndarray]
    field_metadata: dict[str, FieldMetadata]
    source_moments: tuple[str, ...]
    radial_state_counts: dict[int, int]
    nyquist_velocity_m_s: float

    @property
    def ray_count(self) -> int:
        return int(self.azimuth_deg.size)

    @property
    def gate_count(self) -> int:
        return int(self.range_m.size)


@dataclass(frozen=True)
class DecodedRadarVolume:
    source_path: Path
    source_filename: str
    input_sha256: str
    input_size_bytes: int
    format_major_version: int
    format_minor_version: int
    site: SourceSite
    task: SourceTask
    cuts: tuple[SourceCut, ...]
    sweeps: tuple[DecodedSweep, ...]
    volume_start_time: datetime
    volume_end_time: datetime
    filename_time: datetime | None
    warnings: tuple[str, ...]

    @property
    def ray_count(self) -> int:
        return sum(sweep.ray_count for sweep in self.sweeps)

    @property
    def canonical_fields(self) -> tuple[str, ...]:
        return tuple(sorted({name for sweep in self.sweeps for name in sweep.fields}))


@dataclass(frozen=True)
class _Moment:
    code: int
    scale: int
    offset: int
    bin_length: int
    flags: int
    raw: np.ndarray


@dataclass(frozen=True)
class _Radial:
    state: int
    azimuth_deg: float
    elevation_deg: float
    time_ns: int
    horizontal_noise_dbm: float
    vertical_noise_dbm: float
    moments: dict[int, _Moment]


@dataclass
class _SweepBuilder:
    number: int
    radials: list[_Radial] = field(default_factory=list)
    source_codes: set[int] = field(default_factory=set)


def decode_fmt_volume(path: str | Path, config: RadarDecoderConfig) -> DecodedRadarVolume:
    source_path = Path(path)
    if not source_path.is_file():
        raise DecodeError(f"radar source does not exist: {source_path}")
    input_sha256, input_size = _hash_file(source_path)
    warnings: list[str] = []

    with _open_source(source_path) as stream:
        generic = GENERIC_HEADER.unpack(_read_exact(stream, GENERIC_HEADER.size, "generic header"))
        magic, major, minor, generic_type, _product_type, _reserved = generic
        if magic != MAGIC_NUMBER:
            raise DecodeError(f"invalid RSTM magic number 0x{magic:08x}")
        if (major, minor) != (2, 0):
            raise DecodeError(f"unsupported RSTM version {major}.{minor}")
        if generic_type != 1:
            raise DecodeError(f"unsupported RSTM generic type {generic_type}")

        site = _parse_site(_read_exact(stream, SITE_CONFIG.size, "site configuration"))
        task = _parse_task(_read_exact(stream, TASK_CONFIG.size, "task configuration"))
        if not 1 <= task.cut_number <= 64:
            raise DecodeError(f"invalid cut count {task.cut_number}")
        cuts = tuple(
            _parse_cut(number, _read_exact(stream, CUT_CONFIG.size, f"cut {number}"))
            for number in range(1, task.cut_number + 1)
        )
        _validate_header(config, site, task, cuts)

        builders: dict[int, _SweepBuilder] = {}
        radial_total = 0
        while True:
            header_bytes = stream.read(RADIAL_HEADER.size)
            if not header_bytes:
                break
            if len(header_bytes) != RADIAL_HEADER.size:
                raise DecodeError("truncated radial header")
            values = RADIAL_HEADER.unpack(header_bytes)
            (
                radial_state,
                _spot_blank,
                _sequence_number,
                _radial_number,
                elevation_number,
                azimuth,
                elevation,
                seconds,
                microseconds,
                data_length,
                moment_number,
                _reserved,
                horizontal_noise,
                vertical_noise,
                zip_type,
                _reserved2,
            ) = values
            if not 1 <= elevation_number <= task.cut_number:
                raise DecodeError(f"radial references invalid cut {elevation_number}")
            if not 0 <= moment_number <= 64:
                raise DecodeError(f"invalid moment count {moment_number}")
            if zip_type != b"\x00":
                raise DecodeError(
                    f"compressed radial blocks are not supported (zip_type={zip_type.hex()})"
                )
            if not 0 <= microseconds < 1_000_000:
                raise DecodeError(f"invalid radial microseconds {microseconds}")

            moments: dict[int, _Moment] = {}
            parsed_length = 0
            for _ in range(moment_number):
                moment_bytes = _read_exact(stream, MOMENT_HEADER.size, "moment header")
                code, scale, offset, bin_length, flags, block_length, _ = MOMENT_HEADER.unpack(
                    moment_bytes
                )
                if bin_length not in {1, 2}:
                    raise DecodeError(f"unsupported moment bin length {bin_length}")
                if scale == 0 or block_length < 0 or block_length % bin_length:
                    raise DecodeError("invalid moment scale or block length")
                body = _read_exact(stream, block_length, "moment body")
                dtype = np.dtype("u1" if bin_length == 1 else "<u2")
                moments[code] = _Moment(
                    code=code,
                    scale=scale,
                    offset=offset,
                    bin_length=bin_length,
                    flags=flags,
                    raw=np.frombuffer(body, dtype=dtype).copy(),
                )
                parsed_length += MOMENT_HEADER.size + block_length
            if data_length not in {parsed_length, parsed_length + RADIAL_HEADER.size}:
                raise DecodeError(
                    f"radial data length mismatch: declared={data_length} parsed={parsed_length}"
                )

            builder = builders.setdefault(elevation_number, _SweepBuilder(elevation_number))
            builder.radials.append(
                _Radial(
                    state=radial_state,
                    azimuth_deg=float(azimuth) % 360.0,
                    elevation_deg=float(elevation),
                    time_ns=int(seconds) * 1_000_000_000 + int(microseconds) * 1_000,
                    horizontal_noise_dbm=_decode_noise(horizontal_noise),
                    vertical_noise_dbm=_decode_noise(vertical_noise),
                    moments=moments,
                )
            )
            builder.source_codes.update(moments)
            radial_total += 1

    if radial_total == 0:
        raise DecodeError("RSTM volume has no radials")
    if set(builders) != set(range(1, task.cut_number + 1)):
        raise DecodeError("RSTM volume is missing one or more configured cuts")

    sweeps = tuple(
        _finalize_sweep(builders[number], cuts[number - 1], config)
        for number in range(1, task.cut_number + 1)
    )
    if not any("DBZH" in sweep.fields for sweep in sweeps):
        raise DecodeError("decoded volume has no configured DBZH field")

    ray_times = np.concatenate([sweep.ray_time.astype("datetime64[ns]") for sweep in sweeps])
    start_ns = int(ray_times.astype("int64").min())
    end_ns = int(ray_times.astype("int64").max())
    volume_start = datetime.fromtimestamp(start_ns / 1_000_000_000, UTC)
    volume_end = datetime.fromtimestamp(end_ns / 1_000_000_000, UTC)
    filename_time = _filename_time(source_path.name)
    if filename_time is not None:
        delta_seconds = abs((filename_time - volume_start).total_seconds())
        if delta_seconds > 600:
            warnings.append(
                "filename timestamp differs from immutable RSTM radial UTC time by "
                f"{int(delta_seconds)} seconds; header time is authoritative"
            )

    return DecodedRadarVolume(
        source_path=source_path,
        source_filename=source_path.name,
        input_sha256=input_sha256,
        input_size_bytes=input_size,
        format_major_version=major,
        format_minor_version=minor,
        site=site,
        task=task,
        cuts=cuts,
        sweeps=sweeps,
        volume_start_time=volume_start,
        volume_end_time=volume_end,
        filename_time=filename_time,
        warnings=tuple(warnings),
    )


def _finalize_sweep(
    builder: _SweepBuilder,
    cut: SourceCut,
    config: RadarDecoderConfig,
) -> DecodedSweep:
    radials = builder.radials
    states = Counter(radial.state for radial in radials)
    if radials[0].state not in {0, 3} or radials[-1].state not in {2, 4, 6}:
        raise DecodeError(f"cut {builder.number} has incomplete radial boundaries")
    times = np.asarray([radial.time_ns for radial in radials], dtype="int64")
    if np.any(np.diff(times) < 0):
        raise DecodeError(f"cut {builder.number} radial times are not monotonic")

    selected: list[tuple[FieldMapping, list[_Moment | None]]] = []
    for mapping in config.fields:
        values = [radial.moments.get(mapping.source_code) for radial in radials]
        if any(moment is not None for moment in values):
            selected.append((mapping, values))
    if not selected:
        raise DecodeError(f"cut {builder.number} has no configured fields")

    geometry: set[tuple[int, int]] = set()
    fields: dict[str, np.ndarray] = {}
    raw_gate_codes: dict[str, np.ndarray] = {}
    metadata: dict[str, FieldMetadata] = {}
    for mapping, values in selected:
        present = [moment for moment in values if moment is not None]
        signatures = {
            (moment.raw.size, moment.bin_length, moment.scale, moment.offset, moment.flags)
            for moment in present
        }
        if len(signatures) != 1:
            raise DecodeError(
                f"cut {builder.number} field {mapping.source_name} changes geometry or scaling"
            )
        gate_count, bin_length, scale, offset, flags = signatures.pop()
        resolution = (
            cut.doppler_resolution_m
            if mapping.source_name in {"VEL", "SW"}
            else cut.log_resolution_m
        )
        geometry.add((gate_count, resolution))
        expected_scale = 1.0 / scale
        expected_offset = -offset / scale
        if not np.isclose(mapping.scale_factor, expected_scale, atol=1e-9) or not np.isclose(
            mapping.add_offset, expected_offset, atol=1e-9
        ):
            raise DecodeError(
                f"cut {builder.number} field {mapping.source_name} scale/offset differs from config"
            )
        decoded = np.full((len(radials), gate_count), np.nan, dtype="float32")
        raw_codes = np.full(
            (len(radials), gate_count),
            ABSENT_RAW_GATE_CODE,
            dtype="uint32",
        )
        for ray_index, moment in enumerate(values):
            if moment is None:
                continue
            raw_codes[ray_index] = moment.raw.astype("uint32", copy=False)
            valid = moment.raw >= 5
            decoded[ray_index, valid] = (
                moment.raw[valid].astype("float32") - float(offset)
            ) / float(scale)
        fields[mapping.canonical_name] = decoded
        raw_gate_codes[mapping.canonical_name] = raw_codes
        metadata[mapping.canonical_name] = FieldMetadata(
            mapping=mapping,
            source_code=mapping.source_code,
            raw_scale=scale,
            raw_offset=offset,
            raw_bin_length=bin_length,
            source_flags=flags,
        )
    if len(geometry) != 1:
        raise DecodeError(
            f"cut {builder.number} has incompatible configured field geometry {sorted(geometry)}"
        )
    gate_count, resolution = geometry.pop()
    range_m = cut.start_range_m + (np.arange(gate_count, dtype="float32") + 0.5) * resolution
    source_moments = tuple(
        SOURCE_MOMENT_NAMES.get(code, f"UNKNOWN_{code}") for code in sorted(builder.source_codes)
    )
    return DecodedSweep(
        source_sweep_number=builder.number,
        nominal_elevation_deg=cut.nominal_elevation_deg,
        azimuth_deg=np.asarray([radial.azimuth_deg for radial in radials], dtype="float32"),
        elevation_deg=np.asarray([radial.elevation_deg for radial in radials], dtype="float32"),
        ray_time=times.astype("datetime64[ns]"),
        horizontal_noise_dbm=np.asarray(
            [radial.horizontal_noise_dbm for radial in radials], dtype="float32"
        ),
        vertical_noise_dbm=np.asarray(
            [radial.vertical_noise_dbm for radial in radials], dtype="float32"
        ),
        range_m=range_m,
        fields=fields,
        raw_gate_codes=raw_gate_codes,
        field_metadata=metadata,
        source_moments=source_moments,
        radial_state_counts=dict(sorted(states.items())),
        nyquist_velocity_m_s=cut.nyquist_velocity_m_s,
    )


def _validate_header(
    config: RadarDecoderConfig,
    site: SourceSite,
    task: SourceTask,
    cuts: tuple[SourceCut, ...],
) -> None:
    if site.code.casefold() != config.radar_id.casefold():
        raise DecodeError(f"source site {site.code!r} does not match radar {config.radar_id!r}")
    checks = {
        "longitude": (site.longitude_deg, config.site.get("longitude_deg"), 1e-4),
        "latitude": (site.latitude_deg, config.site.get("latitude_deg"), 1e-4),
        "ground altitude": (site.ground_altitude_m, config.site.get("altitude_m"), 1.0),
        "antenna altitude": (
            site.antenna_altitude_m,
            config.site.get("antenna_altitude_m"),
            1.0,
        ),
        "frequency": (site.frequency_mhz, config.hardware.get("frequency_mhz"), 0.1),
        "horizontal beam width": (
            site.beam_width_horizontal_deg,
            config.hardware.get("beam_width_deg"),
            1e-3,
        ),
        "vertical beam width": (
            site.beam_width_vertical_deg,
            config.hardware.get("beam_width_vertical_deg"),
            1e-3,
        ),
    }
    for label, (actual, expected, tolerance) in checks.items():
        if expected is not None and abs(float(actual) - float(expected)) > tolerance:
            raise DecodeError(f"source {label} {actual} differs from config {expected}")
    if config.scan.get("strategy_name") != task.name:
        raise DecodeError(f"source task {task.name!r} differs from radar configuration")
    expected_cuts = config.scan.get("expected_cut_elevations_deg")
    actual_cuts = [cut.nominal_elevation_deg for cut in cuts]
    if expected_cuts is not None and (
        len(expected_cuts) != len(actual_cuts)
        or not np.allclose(expected_cuts, actual_cuts, atol=0.11)
    ):
        raise DecodeError(f"source cut elevations {actual_cuts!r} differ from config")
    expected_gate = config.scan.get("range_gate_m")
    resolutions = {cut.log_resolution_m for cut in cuts} | {
        cut.doppler_resolution_m for cut in cuts
    }
    if expected_gate is not None and any(
        abs(value - float(expected_gate)) > 1e-6 for value in resolutions
    ):
        raise DecodeError(f"source gate resolutions {sorted(resolutions)} differ from config")


def _parse_site(value: bytes) -> SourceSite:
    values = SITE_CONFIG.unpack(value)
    return SourceSite(
        code=_decode_text(values[0]),
        name=_decode_text(values[1]),
        latitude_deg=float(values[2]),
        longitude_deg=float(values[3]),
        antenna_altitude_m=int(values[4]),
        ground_altitude_m=int(values[5]),
        frequency_mhz=float(values[6]),
        beam_width_horizontal_deg=float(values[7]),
        beam_width_vertical_deg=float(values[8]),
        rda_version=int(values[9]),
        radar_type_code=int(values[10]),
    )


def _parse_task(value: bytes) -> SourceTask:
    values = TASK_CONFIG.unpack(value)
    try:
        scan_start = datetime.fromtimestamp(values[5], UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise DecodeError(f"invalid task scan time {values[5]}") from error
    return SourceTask(
        name=_decode_text(values[0]),
        description=_decode_text(values[1]),
        polarization_type=int(values[2]),
        scan_type=int(values[3]),
        pulse_width_ns=int(values[4]),
        scan_start_time=scan_start,
        cut_number=int(values[6]),
    )


def _parse_cut(number: int, value: bytes) -> SourceCut:
    values = CUT_CONFIG.unpack(value)
    return SourceCut(
        number=number,
        process_mode=int(values[0]),
        waveform=int(values[1]),
        prf1_hz=float(values[2]),
        prf2_hz=float(values[3]),
        nominal_elevation_deg=float(values[6]),
        angular_resolution_deg=float(values[9]),
        scan_speed_deg_s=float(values[10]),
        log_resolution_m=int(values[11]),
        doppler_resolution_m=int(values[12]),
        max_range1_m=int(values[13]),
        max_range2_m=int(values[14]),
        start_range_m=int(values[15]),
        nyquist_velocity_m_s=float(values[20]),
        moments_mask=int(values[21]),
    )


def _decode_text(value: bytes) -> str:
    raw = value.split(b"\0", 1)[0]
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _open_source(path: Path) -> BinaryIO:
    with path.open("rb") as stream:
        compressed = stream.read(3) == b"BZh"
    return bz2.open(path, "rb") if compressed else path.open("rb")


def _read_exact(stream: BinaryIO, size: int, label: str) -> bytes:
    value = stream.read(size)
    if len(value) != size:
        raise DecodeError(f"truncated {label}: expected {size} bytes, got {len(value)}")
    return value


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _filename_time(filename: str) -> datetime | None:
    match = re.search(r"_(\d{14})_", filename)
    if match is None:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _decode_noise(value: int) -> float:
    # RSTM radial headers encode the magnitude of negative dBm in centi-dBm.
    # Z9598 uses both zero and the minimum signed short for unavailable estimates.
    return np.nan if value in {0, -32768} else -abs(value) / 100.0
