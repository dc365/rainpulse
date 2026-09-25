from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import types
from dataclasses import asdict
from pathlib import Path

import numpy as np
from pyproj import Transformer

from rainpulse_algo.multiband.model import Grid, Network, Station, Sweep, Volume, XProfile, epoch

BASE = "9c6ea02dcef750c9499fac5763b5cc84793e1e61"
REFERENCE = (
    "model.py",
    "quality.py",
    "fusion.py",
    "adapters.py",
    "managed.py",
    "codec.py",
    "product.py",
)
_TEMP = None


def reference(module):
    global _TEMP
    if "rp_perf_reference" not in sys.modules:
        root = os.getenv("RAINPULSE_PERF_REFERENCE_ROOT")
        if root:
            path = Path(root) / "algorithms/rainpulse_algo/multiband"
        else:
            _TEMP = tempfile.TemporaryDirectory(prefix="rp-reference-")
            path = Path(_TEMP.name)
            for name in REFERENCE:
                proc = subprocess.run(
                    ["git", "show", f"{BASE}:algorithms/rainpulse_algo/multiband/{name}"],
                    capture_output=True,
                )
                if proc.returncode:
                    raise RuntimeError(
                        "reference commit unavailable; set RAINPULSE_PERF_REFERENCE_ROOT"
                    )
                (path / name).write_bytes(proc.stdout)
        package = types.ModuleType("rp_perf_reference")
        package.__path__ = [str(path)]
        sys.modules[package.__name__] = package
    return importlib.import_module("rp_perf_reference." + module)


def station(sid="x1", *, band="X", attenuation="upstream_verified", longitude=120.0):
    return Station(
        sid,
        band,
        9.4e9 if band == "X" else 2.9e9,
        longitude,
        26.0,
        120.0,
        2.0,
        1.0,
        "native_bundle",
        enabled=True,
        geometry_verified=True,
        calibration_verified=True,
        calibration_id="cal-v1",
        nominal_cadence_seconds=360,
        maximum_age_seconds=900,
        allowed_s_qc_versions=("test-s-v1",),
        x_qc=XProfile(
            attenuation=attenuation,
            alpha_db_per_degree=0.15 if attenuation == "phidp_linear" else None,
        ),
    )


def volume(st=None, *, cuts=3, rays=90, gates=96, seed=6, dtype="float32"):
    st = st or station()
    rng = np.random.default_rng(seed)
    sweeps = []
    start = "2026-08-28T00:00:00Z"
    end = "2026-08-28T00:01:00Z"
    t = epoch(start)
    for number in range(cuts):
        shape = (rays, gates)
        z = rng.uniform(1, 50, shape).astype(dtype)
        observed = np.ones(shape, "uint8")
        observed[rng.random(shape) < 0.04] = 0
        z[observed == 0] = np.nan
        noecho = ((rng.random(shape) < 0.05) & (observed == 1)).astype("uint8")
        z[noecho == 1] = np.nan
        snr = rng.uniform(8, 28, shape).astype(dtype)
        rho = rng.uniform(0.8, 1, shape).astype(dtype)
        phi = np.broadcast_to(np.arange(gates) * 0.9, shape).astype(dtype).copy()
        fields = {
            "DBZH": z,
            "OBSERVED_MASK": observed,
            "NO_ECHO_MASK": noecho,
            "SNRH": snr,
            "RHOHV": rho,
            "PHIDP": phi,
            "PHASE_VALID_MASK": np.ones(shape, bool),
            "LIQUID_MASK": np.ones(shape, "uint8"),
            "ATTENUATION_VALID_MASK": np.ones(shape, bool),
            "PIA_DB": np.ones(shape, "float32"),
            "WEATHER_PROTECTED_MASK": np.zeros(shape, "uint8"),
        }
        if st.band == "S":
            fields.update(
                DBZH_QC=z.copy(),
                REFLECTIVITY_ELIGIBLE_FOR_CR=observed.copy(),
                QUALITY_INDEX=rng.uniform(0.3, 1, shape).astype(dtype),
                CR_UNCERTAIN_MASK=(1 - observed).astype("uint8"),
            )
        sweeps.append(
            Sweep(
                number,
                np.arange(rays) * 360 / rays,
                np.arange(gates) * 500.0 + 250.0,
                np.full(rays, 0.5 + number * 0.8),
                np.linspace(t, t + 60, rays),
                fields,
            )
        )
    metadata = {
        "radar_id": st.radar_id,
        "scan_id": "scan-" + st.radar_id,
        "band": st.band,
        "frequency_hz": st.frequency_hz,
        "longitude_deg": st.longitude_deg,
        "latitude_deg": st.latitude_deg,
        "altitude_m_msl": st.altitude_m_msl,
        "height_datum": "MSL",
        "volume_start": start,
        "volume_end": end,
        "available_at": end,
        "asset_sha256": "a" * 64,
        "scan_type": "volume",
        "calibration_id": st.calibration_id,
        "attenuation_status": "raw" if st.x_qc.attenuation == "phidp_linear" else "corrected",
        "phase_anchor_verified": True,
        "pia_at_first_gate_db": 0.0,
        "qc_pipeline_version": "test-s-v1",
    }
    return Volume(metadata, sweeps)


def network(stations=None, *, tile_rows=4, width=22, height=19):
    stations = stations or [station()]
    t = Transformer.from_crs(4326, 32651, always_xy=True)
    x, y = t.transform(120.0, 26.0)
    grid = Grid(
        "grid",
        "EPSG:32651",
        x - width * 500.0,
        y - height * 500.0,
        1000.0,
        width,
        height,
        (250.0, 500.0, 1000.0, 2000.0, 4000.0),
        tile_rows,
        360,
    )
    return Network("release", {s.radar_id: s for s in stations}, {"demo": grid}, "b" * 64)


def assert_arrays(a, b):
    assert set(a) == set(b)
    for key in a:
        assert a[key].dtype == b[key].dtype, key
        assert np.array_equal(a[key], b[key], equal_nan=True), key


def clone(v):
    return copy.deepcopy(v)


def qc(v, st, net):
    from rainpulse_algo.multiband.quality import accept_s_qc, x_qc

    return x_qc(v, st, net.sha256) if st.band == "X" else accept_s_qc(v, st, net.sha256)


def cuts(volumes):
    for v in sorted(volumes, key=lambda v: (v.metadata["radar_id"], v.metadata["scan_id"])):
        for s in sorted(v.sweeps, key=lambda s: s.number):
            yield Volume(copy.deepcopy(v.metadata), [s])


def write_network(path, net):
    stations = {
        k: {n: v for n, v in asdict(s).items() if n != "radar_id"} for k, s in net.stations.items()
    }
    raw = json.dumps(
        {
            "schema_version": "1.0",
            "release_id": net.release_id,
            "stations": stations,
            "products": {k: asdict(v) for k, v in net.products.items()},
            "cache_max_bytes": net.cache_max_bytes,
            "maximum_input_bytes": net.maximum_input_bytes,
            "cache_ttl_seconds": net.cache_ttl_seconds,
        }
    ).encode()
    path.write_bytes(raw)
    return Network.load(path)


class Store:
    def __init__(self, objects, *, schema="2.0", pack_bytes=1024):
        from rainpulse_algo.worker.asset_access import artifact_digest

        self.logical = objects
        self.calls = []
        self.data = {}
        self.sha = artifact_digest(objects)
        if schema == "3.0":
            physical = {}
            entries = []
            current = bytearray()
            number = 0
            for key, value in sorted(objects.items()):
                if current and len(current) + len(value) > pack_bytes:
                    physical[f"pack{number}"] = bytes(current)
                    number += 1
                    current = bytearray()
                entries.append([key, f"pack{number}", len(current), len(value)])
                current.extend(value)
            physical[f"pack{number}"] = bytes(current)
        else:
            physical = objects
        self.marker = {
            "schema_version": schema,
            "sha256": self.sha,
            "size_bytes": sum(map(len, physical.values())),
            "objects": [
                {"key": k, "size_bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()}
                for k, v in physical.items()
            ],
        }
        if schema == "3.0":
            self.marker["packed_entries"] = entries
        self.data = {f"sample/{k}": v for k, v in physical.items()}
        self.refresh()

    def refresh(self):
        self.data["sample/_SUCCESS.json"] = json.dumps(self.marker).encode()

    def read(self, bucket, key, limit):
        self.calls.append(key)
        if key not in self.data:
            raise FileNotFoundError(key)
        value = self.data[key]
        if len(value) > limit:
            raise RuntimeError("transport cap")
        return value

    def reader(self, cache=None):
        from rainpulse_algo.worker.asset_access import VerifiedArtifactReader

        return VerifiedArtifactReader(
            self.read, namespace="test", maximum=2 * 1024**3, workers=1, cache=cache
        )


def source(v):
    m = v.metadata
    return {
        "radar_id": m["radar_id"],
        "scan_id": m["scan_id"],
        "input_uri": "s3://test/sample",
        **{k: m[k] for k in ("volume_start", "volume_end", "available_at")},
    }
