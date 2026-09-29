from dataclasses import replace
import numpy as np
from rainpulse_algo.multiband.model import Sweep, Volume, Station, XProfile
from rainpulse_algo.multiband.xqc_v2.config import XQCConfig

def config(**changes):
    data={"clutter":{"depolarization_backend":"numpy_reference", "minimum_range_m":750.,
                     "minimum_phase_spacing_m":50., "neighbourhood_m":750., "mode":"quarantine",
                     "isolated_objects":{"mode":"audit"}},
          "maximum_new_exclusion_fraction":1., **changes}
    return XQCConfig.model_validate(data)

def fixture(kind="coherent", rays=120, gates=700, dr=75., phase_contract=False):
    shape=(rays,gates);r=np.arange(1,gates+1,dtype=float)*dr
    z=np.full(shape,0.,"float32");sn=np.full(shape,2.,"float32")
    rho=np.full(shape,.99,"float32");zdr=np.zeros(shape,"float32");phi=np.zeros(shape,"float32")
    row=rays//3
    if kind=="coherent":
        sn[row]=30.;z[row]=30+20*np.log10(r/1000.)+.01*r/1000.-25
        zdr[row]=.2;phi[row]=44.;rho[row]=.99
    elif kind=="flat":
        z[row]=28.;sn[row]=25.;rho[row]=.6;zdr[row]=5.;phi[row]=44.
    elif kind=="weather":
        z[:]=15.;sn[:]=25.;rho[:]=.999;zdr[:]=.3
        phi[:]=np.linspace(20.,22.,gates)
    elif kind=="clutter":
        rng=np.random.default_rng(55);z[:]=rng.uniform(5,24,shape)
        sn[:]=25.;rho[:]=.5;zdr[:]=4.8;phi[:]=rng.uniform(0,360,shape)
    elif kind!="empty":raise ValueError(kind)
    f=dict(DBZH=z,SNRH=sn,RHOHV=rho,ZDR=zdr,PHIDP=phi,
           OBSERVED_MASK=np.ones(shape,"uint8"),NO_ECHO_MASK=np.zeros(shape,"uint8"))
    if phase_contract:
        f.update(PHASE_VALID_MASK=np.ones(shape,"uint8"),LIQUID_MASK=np.ones(shape,"uint8"))
    start=1787875200.
    s=Sweep(0,np.arange(rays)*360/rays,r,np.full(rays,.5),start+np.linspace(0,60,rays),f)
    m=dict(radar_id="x01",scan_id="test",band="X",volume_start="2026-08-28T00:00:00Z",
        volume_end="2026-08-28T00:01:00Z",available_at="2026-08-28T00:01:01Z",
        asset_sha256="a"*64,scan_type="volume",attenuation_status="raw",radar_config_version="test-75m",
        calibration_id="test",frequency_hz=9.45e9,longitude_deg=118.,latitude_deg=26.,altitude_m_msl=100.,height_datum="MSL")
    return Volume(m,[s]),row

def station(cfg=None, *, calibrated=False, phase=False):
    x=XProfile(enhancement=None if cfg is None else cfg.model_dump(mode="json"),
               attenuation="phidp_linear" if phase else "none",alpha_db_per_degree=.1 if phase else None)
    return Station("x01","X","normalized_zarr",frequency_hz=9.45e9,
        longitude_deg=118.,latitude_deg=26.,altitude_m_msl=100.,beam_width_h_deg=1.,beam_width_v_deg=1.,
        geometry_verified=calibrated,calibration_verified=calibrated,calibration_id="test",x_qc_enabled=True,x_qc=x)
