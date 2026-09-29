# ruff: noqa: E501, I001
from dataclasses import asdict
from datetime import datetime, UTC, timedelta
import numpy as np
from rainpulse_algo.multiband.model import XProfile, Station, Sweep, Volume, Grid, Network
TIME = datetime(2026, 9, 29, 8, 0, tzinfo=UTC)
STAMP = TIME.isoformat()
SHA = 'b' * 64

def options(**kw):
    return {'beta': 0.62, 'coefficient_id': 'synthetic-not-operational', 'coefficient_source_sha256': 'a' * 64, 'frequency_min_hz': 9000000000.0, 'frequency_max_hz': 10000000000.0, 'minimum_segment_m': 500.0, **kw}

def station(band='X', **kw):
    p = XProfile(attenuation='zphi', alpha_db_per_degree=0.2, phase_window_m=100.0, zphi=options())
    fields = dict(radar_id='x1' if band == 'X' else 's1', band=band, source='native_bundle', frequency_hz=9400000000.0 if band == 'X' else 2800000000.0, longitude_deg=121.0, latitude_deg=26.0, altitude_m_msl=100.0, beam_width_h_deg=2.0, beam_width_v_deg=2.0, enabled=True, geometry_verified=True, calibration_verified=True, calibration_id='synthetic-cal', maximum_age_seconds=300, allowed_s_qc_versions=('s-test',), x_qc=p if band == 'X' else XProfile())
    fields.update(kw)
    return Station(**fields)

def volume(st=None, *, gates=101, value=40.0, age=0):
    st = st or station()
    shape = (2, gates)
    phase = np.broadcast_to(np.linspace(0, 40, gates), shape).astype('float32').copy()
    ranges = 500.0 + np.arange(gates) * 200
    data = np.full(shape, value, 'float32')
    fields = dict(DBZH=data, RHOHV=np.full(shape, 0.99, 'float32'), SNRH=np.full(shape, 30.0, 'float32'), PHIDP=phase, OBSERVED_MASK=np.ones(shape, 'uint8'), NO_ECHO_MASK=np.zeros(shape, 'uint8'), PHASE_VALID_MASK=np.ones(shape, 'uint8'), LIQUID_MASK=np.ones(shape, 'uint8'))
    if st.band == 'S':
        fields.update(DBZH_QC=data.copy(), REFLECTIVITY_ELIGIBLE_FOR_CR=np.ones(shape, 'uint8'), QUALITY_INDEX=np.full(shape, 0.9, 'float32'))
    t = TIME - timedelta(seconds=age)
    meta = dict(radar_id=st.radar_id, scan_id='scan-' + st.radar_id, band=st.band, frequency_hz=st.frequency_hz, longitude_deg=st.longitude_deg, latitude_deg=st.latitude_deg, altitude_m_msl=st.altitude_m_msl, height_datum='MSL', scan_type='volume', volume_start=(t - timedelta(seconds=30)).isoformat(), volume_end=t.isoformat(), available_at=t.isoformat(), asset_sha256=SHA, calibration_id=st.calibration_id, qc_pipeline_version='s-test', attenuation_status='raw', phase_anchor_verified=True, pia_at_first_gate_db=0.0, radome_status='verified_negligible', radome_evidence_sha256='c' * 64)
    cut = Sweep(0, np.array([90.0, 270.0]), ranges, np.ones(2), np.full(2, t.timestamp()), fields)
    return Volume(meta, [cut])

def network(*stations, tile_rows=1, height=1, levels=(150.0,)):
    grid = Grid(grid_id='local-100m', crs='+proj=aeqd +lat_0=26 +lon_0=121 +datum=WGS84 +units=m', west_m=2450.0, south_m=-50.0, spacing_m=100.0, width=1, height=height, levels_m_msl=levels, tile_rows=tile_rows, method='quality_height_v2')
    return Network('test-v2', {s.radar_id: s for s in stations}, {'sx': grid}, SHA)
