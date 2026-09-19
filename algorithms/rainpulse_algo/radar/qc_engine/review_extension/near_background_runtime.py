"""Pinned, explicitly retrospective near-station background admission."""
from functools import lru_cache
from pathlib import Path
import hashlib
import io
import numpy as np
from .near_background import candidate
from .nonprecip import EchoClass, Family


@lru_cache(maxsize=8)
def load_asset(path, digest):
    payload=Path(path).read_bytes()
    if hashlib.sha256(payload).hexdigest()!=digest:
        raise ValueError('near background content hash mismatch')
    with np.load(io.BytesIO(payload),allow_pickle=False) as archive:
        result={k:archive[k] for k in archive.files}
    if not np.array_equal(result['azimuth'],np.arange(360)) or not np.array_equal(result['range_m'],np.arange(500,75000,1000)):
        raise ValueError('near background geometry differs from pinned research grid')
    for a in result.values():a.flags.writeable=False
    return result


def augment(native,baseline,result,cfg,no_rain_below_dbz):
    policy=cfg.near_background
    if policy is None or "near_nonmet" not in cfg.quarantine_classes:return
    station=str(native.attrs['radar_id']).lower()
    asset=policy.assets.get(station)
    if asset is None:return
    target=str(native.attrs['volume_end_time_utc'])[:10]
    if target!=policy.target_date:
        result.summary['near_background_status']='outside_explicit_retrospective_date'
        return
    bg=load_asset(asset.path,asset.sha256)
    key=f'{int(native.name[-3:])+1:03d}'
    if key+'_p90' not in bg:return
    if abs(float(np.median(native.elevation))-float(bg[key+'_elevation']))>.2:
        raise ValueError('near background elevation mismatch')
    az=np.rint(native.azimuth).astype(int)%360
    rg=np.clip((native.ranges//1000).astype(int),0,74)
    pick=lambda name:bg[key+'_'+name][az[:,None],rg[None,:]]
    z=native.fields['DBZH'];shape=z.shape
    rho=native.fields.get('RHOHV',np.full(shape,np.nan));snr=native.fields.get('SNR',np.full(shape,np.nan))
    p10,p90=pick('p10'),pick('p90')
    background=(pick('observed_fraction')>=.8)&(pick('observed_count')>=20)&np.isfinite(p10)&np.isfinite(p90)&(z>=p10-3)&(z<=p90+3)
    measured=native.field_available['DBZH']&native.geometry_good[:,None]&(z>=no_rain_below_dbz)
    # Respect each moment's availability, not just the numeric storage value.
    rho=np.where(native.field_available.get('RHOHV',np.zeros(shape,bool)),rho,np.nan)
    snr=np.where(native.field_available.get('SNR',np.zeros(shape,bool)),snr,np.nan)
    a=result.arrays
    eligible=measured&(baseline.arrays['REFLECTIVITY_TRUST_MASK']==1)
    protected=(a['NP_WEATHER_PROTECTED_MASK']==1)|(a['NP_MIXED_MASK']==1)
    remove,coverage,fraction=candidate(z,rho,snr,background,eligible,protected,native.azimuth,native.ranges)
    remove &= ~a['NP_PROPOSAL_MASK'].astype(bool)
    a['NP_NEAR_BACKGROUND_MASK']=remove.astype('uint8')
    a['NP_NEAR_BACKGROUND_COVERAGE']=coverage.astype('float32')
    a['NP_NEAR_BACKGROUND_ABNORMAL_FRACTION']=fraction.astype('float32')
    a['NP_NEAR_CANDIDATE_MASK'][remove]=1;a['NP_NEAR_AVAILABLE_MASK'][remove]=1
    a['NP_CANDIDATE_MASK'][remove]=1;a['NP_PROPOSAL_MASK'][remove]=1
    a['NP_CLASS'][remove]=int(EchoClass.NEAR_NONMET)
    a['NP_CLASS_BITS'][remove]=1<<int(EchoClass.NEAR_NONMET)
    a['NP_EVIDENCE_BITS'][remove] |= int(Family.HISTORY)|int(Family.POLARIZATION)
    a['NP_EVIDENCE_FAMILY_COUNT'][remove]=np.maximum(a['NP_EVIDENCE_FAMILY_COUNT'][remove],2)
    result.summary.update(near_background_version=policy.version,near_background_sha256=asset.sha256,
        near_background_status='retrospective_single_day',near_background_added_gates=int(remove.sum()),
        proposed_gates=int(a['NP_PROPOSAL_MASK'].sum()),
        class_counts={c.name.lower():int((a['NP_CLASS']==c).sum()) for c in EchoClass})
