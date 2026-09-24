# ruff: noqa: E501, E701, E702, I001, E402
import json
from datetime import UTC, datetime

import numpy as np
import pytest
from pyproj import Geod, Transformer

from rainpulse_algo.multiband.model import Network, Sweep, Volume

TARGET = "2026-09-23T00:10:00Z"


def network_document():
    lon, lat, _ = Geod(ellps="WGS84").fwd(119.3, 26.1, 90., 20000.)
    x, y = Transformer.from_crs("EPSG:4326", "EPSG:32651", always_xy=True).transform(lon, lat)
    common = dict(longitude_deg=119.3, latitude_deg=26.1, altitude_m_msl=100., beam_width_h_deg=1., beam_width_v_deg=1., enabled=True, geometry_verified=True, calibration_verified=True, calibration_id="fixture-cal", quality_scale=1.)
    return {"schema_version": "1.0", "release_id": "fixture-sx-v1",
            "stations": {"s1": {**common, "band": "S", "frequency_hz": 2.8e9, "source": "native_bundle", "nominal_cadence_seconds": 360, "maximum_age_seconds": 600, "allowed_s_qc_versions": ["s-fixture-v1"]},
                         "x1": {**common, "band": "X", "frequency_hz": 9.4e9, "source": "native_bundle", "maximum_age_seconds": 120, "x_qc": {"attenuation": "upstream_verified"}}},
            "products": {"local": {"grid_id": "local-500m", "crs": "EPSG:32651", "west_m": x-250, "south_m": y-250, "spacing_m": 500., "width": 1, "height": 1, "levels_m_msl": [475., 1870.]}}}


@pytest.fixture
def net():
    return Network.from_bytes(json.dumps(network_document()).encode())


def volume(station, *, age=0, values=(35.,), elevations=(1.,), noecho=False, missing=False):
    target = datetime.fromisoformat(TARGET.replace("Z", "+00:00")).timestamp()
    end = target-age
    iso = lambda e: datetime.fromtimestamp(e, UTC).isoformat()
    meta = dict(radar_id=station.radar_id, scan_id=station.radar_id+"-scan", band=station.band, frequency_hz=station.frequency_hz,
                longitude_deg=station.longitude_deg, latitude_deg=station.latitude_deg, altitude_m_msl=station.altitude_m_msl, height_datum="MSL", volume_start=iso(end-10), volume_end=iso(end), available_at=iso(end+1),
                asset_sha256=("a" if station.band == "S" else "b")*64, scan_type="volume", qc_pipeline_version="s-fixture-v1",
                calibration_id=station.calibration_id, attenuation_status="corrected")
    sweeps = []
    for n, (value, el) in enumerate(zip(values, elevations, strict=True)):
        az = np.arange(360, dtype=float)
        ranges = np.arange(250., 40250., 500.)
        shape = (len(az), len(ranges))
        dbz = np.full(shape, value, np.float32)
        if missing or noecho:
            dbz[:] = np.nan
        fields = dict(DBZH=dbz, OBSERVED_MASK=np.full(shape, not missing, np.uint8), NO_ECHO_MASK=np.full(shape, noecho and not missing, np.uint8),
                      DBZH_QC=dbz.copy(), REFLECTIVITY_ELIGIBLE_FOR_CR=np.full(shape, not missing, np.uint8), QUALITY_INDEX=np.ones(shape, np.float32),
                      SNRH=np.full(shape, 20., np.float32), RHOHV=np.full(shape, .99, np.float32), ATTENUATION_VALID_MASK=np.ones(shape, np.uint8))
        sweeps.append(Sweep(n, az, ranges, np.full(360, el), np.full(360, end), fields))
    return Volume(meta, sweeps)
