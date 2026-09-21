from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import numpy as np
from volume_review.episode_background.data import Sample
from volume_review.episode_background.builder import build_episode
from volume_review.episode_background.config import BuildConfig, EpisodeConfig

RECEIPT = hashlib.sha256(b'synthetic-review-only').hexdigest()
T0 = datetime(2026, 9, 18, tzinfo=timezone.utc)


def sample(i=99, *, z=5., rho=.7, zdr=.2, snr=12., nr=8, ng=40, when=None, az=None, missing=(), sweep='sweep_000'):
    shape=(nr,ng)
    values={'DBZH':z,'SNR':snr,'RHOHV':rho,'ZDR':zdr,'PHIDP':2.,'VR':.1,'SW':.4}
    fields={k:np.full(shape,v,'float32') for k,v in values.items() if k not in missing}
    available={k:np.isfinite(v) for k,v in fields.items()}
    return Sample('site_a',f'scan-{i}',sweep,'radar-processor-v1',(when or (T0+timedelta(seconds=360*i))).isoformat(),
        hashlib.sha256(f'content-{i}-{sweep}'.encode()).hexdigest(),
        np.arange(nr,dtype=float) if az is None else np.asarray(az,float),
        np.full(nr,.5),2000+np.arange(ng)*250.,fields,available,np.ones(nr,bool))


def episode(**kw):
    return [sample(i, z=5+.2*np.sin(i), snr=12+.2*np.cos(i), **kw) for i in range(20)]


def model(samples=None, **kw):
    return build_episode(episode() if samples is None else samples,BuildConfig(**kw),
        reviewed_no_precipitation=True,review_receipt=RECEIPT)


def target(**kw):
    return sample(99, when=T0+timedelta(hours=3), **kw)


def baseline(s):
    z,obs=s.moment('DBZH')
    return {'VALID_MASK':obs.astype('uint8'),'DBZH_RAW':s.fields['DBZH'].copy(),
        'DBZH_QC':s.fields['DBZH'].copy(),'REFLECTIVITY_ELIGIBLE_FOR_CR':obs.astype('uint8'),
        'CR_UNCERTAIN_MASK':np.zeros(s.shape,'uint8'), 'CR_QUALIFICATION_REASON':np.ones(s.shape,'uint16'),
        'QC_FLAGS':np.zeros(s.shape,'uint32'), 'QC_ACTION':np.where(obs,0,3).astype('uint8'),
        'QPE_ELIGIBLE_MASK':obs.astype('uint8'),'REFLECTIVITY_TRUST_MASK':obs.astype('uint8'),
        'QUALITY_INDEX':np.full(s.shape,.8,'float32')}
