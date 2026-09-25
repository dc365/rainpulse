"""Per-cut adaptation using the existing S/X input and QC contracts.

Zarr is opened over a read-only verified mapping, never MemoryStore.update(all).
Legacy NPZ is staged to a seekable file and decoded one cut at a time. The new
native-stream-v1 output is deliberately distinct from the <=32-cut eager format.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import numpy as np

from .adapters import FIELDS, X_QC_FIELDS, from_group
from .model import MAX_FUSION_SWEEPS, NAME, Sweep, Volume, epoch, json_bytes

COORDINATES = ("azimuth_deg", "range_m", "elevation_deg", "ray_time_epoch")
STREAM_CONTRACT = "rainpulse.multiband.native-stream-v1"


def _json(raw):
    if len(raw) > 1024**2:
        raise ValueError("native metadata exceeds 1 MiB")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate native metadata key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


class CutRoot:
    """Expose exactly one declared cut without altering underlying metadata."""

    def __init__(self, root, number):
        self.root, self.number, self.attrs = root, number, root.attrs

    def __getitem__(self, name):
        if name == "sweep_number":
            return np.asarray([self.number], np.int32)
        if name != f"sweep_{self.number:03d}":
            raise KeyError(name)
        return self.root[name]


class GroupCuts:
    def __init__(
        self,
        root,
        station,
        source,
        *,
        sha256,
        options,
        maximum_bytes,
        reject_mask=0,
        flag_version="",
        sweep_limit=MAX_FUSION_SWEEPS,
    ):
        self.root, self.station, self.source = root, station, source
        self.sha, self.options = sha256, options
        self.maximum = min(maximum_bytes, options.maximum_cut_bytes)
        self.reject_mask, self.flag_version = reject_mask, flag_version
        numbers = root["sweep_number"]
        sweep_limit = min(options.maximum_sweeps, sweep_limit)
        if (
            len(numbers.shape) != 1
            or not 1 <= math.prod(numbers.shape) <= sweep_limit
            or np.dtype(numbers.dtype).kind not in "iu"
        ):
            raise ValueError("invalid or oversized streamed cut index")
        values = list(map(int, numbers[:]))
        if len(set(values)) != len(values) or any(n < 0 or n > 4096 for n in values):
            raise ValueError("duplicate/invalid streamed cut numbers")
        self.numbers, self.gates = [], {}
        total = 0
        for n in sorted(values):
            group = root[f"sweep_{n:03d}"]
            field = "DBZH_RAW" if station.source == "s_qc_zarr" else "DBZH"
            if field not in group:
                continue
            array = group[field]
            if (
                len(array.shape) != 2
                or not 1 <= array.shape[0] <= 4096
                or not 1 <= array.shape[1] <= 16384
            ):
                raise ValueError("invalid streamed reflectivity dimensions")
            expected_shapes = {
                **{
                    key: array.shape
                    for key in (
                        X_QC_FIELDS if station.band == "X" else FIELDS
                    )
                    if key in group
                },
                "azimuth": (array.shape[0],),
                "elevation": (array.shape[0],),
                "ray_time": (array.shape[0],),
                "range": (array.shape[1],),
            }
            for key, expected in expected_shapes.items():
                if tuple(group[key].shape) != tuple(expected):
                    raise ValueError("streamed field shape differs from DBZH coordinates")
            count = math.prod(array.shape)
            total += count
            if total > options.maximum_volume_gates or count > 8_000_000:
                raise ValueError("streamed native gate budget exceeded")
            allowed_fields = X_QC_FIELDS if station.band == "X" else FIELDS
            selected = allowed_fields & set(group.array_keys())
            if station.source == "s_qc_zarr":
                selected |= {
                    k for k in group.array_keys() if k.endswith("CR_WITHHELD_MASK")
                }
            required = selected | {"azimuth", "range", "elevation", "ray_time"}
            size = 0
            for key in required:
                a = group[key]
                if np.dtype(a.dtype).kind not in "buifM" or len(a.shape) > 2:
                    raise ValueError("invalid streamed array type")
                size += math.prod(a.shape) * np.dtype(a.dtype).itemsize
                if size > self.maximum:
                    raise ValueError("streamed cut exceeds decoded input budget")
            self.numbers.append(n)
            self.gates[n] = count
        if not self.numbers:
            raise ValueError("no reflectivity cuts")

    def read(self, number):
        if number not in self.gates:
            raise KeyError(number)
        return from_group(
            CutRoot(self.root, number),
            self.station,
            self.source,
            asset_sha256=self.sha,
            maximum_bytes=self.maximum,
            s_reject_mask=self.reject_mask,
            expected_flag_version=self.flag_version,
        )


class NPZCuts:
    def __init__(
        self,
        metadata,
        path,
        station,
        source,
        *,
        sha256,
        options,
        maximum_bytes,
        sweep_limit=MAX_FUSION_SWEEPS,
    ):
        self.path, self.options = Path(path), options
        self.maximum = min(maximum_bytes, options.maximum_cut_bytes)
        self.manifest = _json(metadata)
        contract = self.manifest.get("contract")
        if contract not in {"rainpulse.multiband.native-v1", STREAM_CONTRACT}:
            raise ValueError("unsupported native streaming contract")
        if file_sha256(path) != self.manifest.get("arrays_sha256"):
            raise ValueError("native array container hash differs")
        self.metadata = copy.deepcopy(self.manifest["metadata"])
        if (
            self.metadata["scan_id"] != source["scan_id"]
            or epoch(self.metadata["volume_start"]) != epoch(source["volume_start"])
            or epoch(self.metadata["volume_end"]) != epoch(source["volume_end"])
        ):
            raise ValueError("native bundle identity differs from selected scan")
        self.metadata["asset_sha256"] = sha256
        if epoch(source["available_at"]) > epoch(self.metadata["available_at"]):
            self.metadata["available_at"] = source["available_at"]
        items = self.manifest.get("sweeps")
        cap = min(options.maximum_sweeps, sweep_limit)
        if contract.endswith("native-v1"):
            cap = min(cap, MAX_FUSION_SWEEPS)
        if not isinstance(items, list) or not 1 <= len(items) <= cap:
            raise ValueError("native sweep count exceeds its contract")
        self.fields, expected = {}, set()
        for item in items:
            n, fields = item["number"], item["fields"]
            if type(n) is not int or n < 0 or n > 4096 or n in self.fields:
                raise ValueError("duplicate/invalid native cut")
            if (
                not isinstance(fields, list)
                or not fields
                or len(set(fields)) != len(fields)
                or any(
                    not isinstance(k, str) or not NAME.fullmatch(k) or k in COORDINATES
                    for k in fields
                )
            ):
                raise ValueError("invalid native field names")
            self.fields[n] = fields
            expected.update(f"s{n}_{k}.npy" for k in (*COORDINATES, *fields))
        if len(expected) > 4096:
            raise ValueError("native array count exceeds budget")
        self.archive = ZipFile(path)
        try:
            entries = self.archive.infolist()
            if len(entries) != len(expected) or {e.filename for e in entries} != expected:
                raise ValueError("unexpected, duplicated or missing native arrays")
            self.headers = {}
            for entry in entries:
                if (
                    PurePosixPath(entry.filename).name != entry.filename
                    or entry.flag_bits & 1
                    or entry.file_size > self.maximum + 16384
                ):
                    raise ValueError("invalid or oversized native NPY member")
                with self.archive.open(entry) as stream:
                    version = np.lib.format.read_magic(stream)
                    if version == (1, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_1_0(stream)
                    elif version == (2, 0):
                        shape, fortran, dtype = np.lib.format.read_array_header_2_0(stream)
                    else:
                        raise ValueError("unsupported NPY version")
                    if (
                        dtype.kind not in "buif"
                        or not 1 <= len(shape) <= 2
                        or any(type(x) is not int or x < 1 for x in shape)
                    ):
                        raise ValueError("non-numeric or invalid native dimensions")
                    size = math.prod(shape) * dtype.itemsize
                    if size > self.maximum or size + stream.tell() != entry.file_size:
                        raise ValueError("native NPY size exceeds or differs from declared payload")
                    self.headers[entry.filename] = (shape, size)
            total = 0
            self.gates = {}
            for n, fields in self.fields.items():
                if not {"DBZH", "OBSERVED_MASK", "NO_ECHO_MASK"}.issubset(fields):
                    raise ValueError("native observation fields missing")
                shape = self.headers[f"s{n}_DBZH.npy"][0]
                if len(shape) != 2:
                    raise ValueError("reflectivity must be 2D")
                size = sum(self.headers[f"s{n}_{k}.npy"][1] for k in (*COORDINATES, *fields))
                count = math.prod(shape)
                if (
                    shape[0] > 4096
                    or shape[1] > 16384
                ):
                    raise ValueError("native reflectivity dimensions exceed contract")
                expected_shapes = {
                    **{f"s{n}_{key}.npy": shape for key in fields},
                    f"s{n}_azimuth_deg.npy": (shape[0],),
                    f"s{n}_elevation_deg.npy": (shape[0],),
                    f"s{n}_ray_time_epoch.npy": (shape[0],),
                    f"s{n}_range_m.npy": (shape[1],),
                }
                if any(
                    self.headers[key][0] != expected
                    for key, expected in expected_shapes.items()
                ):
                    raise ValueError("native array shape differs from DBZH coordinates")
                total += count
                if size > self.maximum or count > 8_000_000 or total > options.maximum_volume_gates:
                    raise ValueError("native decoded cut/volume exceeds streaming budget")
                self.gates[n] = count
            self.numbers = sorted(self.fields)
        except BaseException:
            self.archive.close()
            raise

    def read(self, number):
        if number not in self.gates:
            raise KeyError(number)
        arrays = {}
        for name in (*COORDINATES, *self.fields[number]):
            with self.archive.open(f"s{number}_{name}.npy") as stream:
                arrays[name] = np.lib.format.read_array(stream, allow_pickle=False)
                if stream.read(1):
                    raise ValueError("trailing native NPY bytes")
        cut = Sweep(
            number, *(arrays[k] for k in COORDINATES), {k: arrays[k] for k in self.fields[number]}
        )
        return Volume(copy.deepcopy(self.metadata), [cut])

    def close(self):
        self.archive.close()


class LimitedFile:
    def __init__(self, stream, maximum):
        self.stream, self.maximum = stream, maximum

    def write(self, data):
        if self.stream.tell() + len(data) > self.maximum:
            raise ValueError("encoded native output exceeds configured byte budget")
        return self.stream.write(data)

    def __getattr__(self, name):
        return getattr(self.stream, name)


class NativeStreamWriter:
    """One deterministic bounded NPZ written incrementally to private scratch.

    Publisher still needs bytes. At finish exactly one bounded encoded copy is
    returned, never the complete decoded volume. No claim of streaming upload.
    """

    def __init__(self, path, maximum_bytes):
        self.path, self.maximum = Path(path), maximum_bytes
        self.stream = self.path.open("x+b")
        self.archive = ZipFile(
            LimitedFile(self.stream, maximum_bytes), "w", compression=ZIP_DEFLATED, compresslevel=3
        )
        self.metadata, self.sweeps = None, []
        self.array_count = 0
        self.closed = False

    def add(self, volume):
        if len(volume.sweeps) != 1:
            raise ValueError("one QC cut per write required")
        if self.metadata is None:
            self.metadata = copy.deepcopy(volume.metadata)
        elif json_bytes(self.metadata) != json_bytes(volume.metadata):
            raise ValueError("native stream metadata changed")
        s = volume.sweeps[0]
        if self.sweeps and s.number <= self.sweeps[-1]["number"]:
            raise ValueError("native stream order/identity differs")
        if len(self.sweeps) >= 64:
            raise ValueError("native stream exceeds 64 cuts")
        arrays = dict(
            zip(
                COORDINATES,
                (s.azimuth_deg, s.range_m, s.elevation_deg, s.ray_time_epoch),
                strict=True,
            )
        )
        if set(arrays) & set(s.fields):
            raise ValueError("native coordinate/field collision")
        arrays.update(s.fields)
        for name, array in sorted(arrays.items()):
            if not NAME.fullmatch(name) or np.asarray(array).dtype.kind not in "buif":
                raise ValueError("invalid native output array")
            self.array_count += 1
            if self.array_count > 4096:
                raise ValueError("native stream array count exceeds budget")
            info = ZipInfo(f"s{s.number}_{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info._compresslevel = 3
            with self.archive.open(info, "w", force_zip64=True) as member:
                np.lib.format.write_array(member, np.asarray(array), allow_pickle=False)
        self.sweeps.append({"number": s.number, "fields": sorted(s.fields)})

    def finish(self):
        if self.metadata is None:
            raise ValueError("cannot publish an empty native stream")
        self.close()
        if self.path.stat().st_size > self.maximum:
            raise ValueError("native output budget exceeded")
        raw = self.path.read_bytes()
        meta = {
            "contract": STREAM_CONTRACT,
            "metadata": self.metadata,
            "sweeps": self.sweeps,
            "arrays_sha256": hashlib.sha256(raw).hexdigest(),
        }
        encoded = json_bytes(meta)
        if len(encoded) > 1024**2:
            raise ValueError("native metadata too large")
        return {"native_arrays.npz": raw, "native_volume.json": encoded}

    def close(self):
        if not self.closed:
            self.closed = True
            try:
                self.archive.close()
            finally:
                self.stream.close()
