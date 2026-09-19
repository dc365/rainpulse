"""Experimental interpretable evidence score; not wired into production.

Weights are hypotheses, not calibrated probabilities. Background and temporal
persistence share a family, as do SNR and polarimetric reliability.
"""
import numpy as np


def score_candidate(*, z, eligible, protected, background, low_snr,
                    polarimetric, temporal, doppler, texture, omit=None):
    shape=np.shape(z)
    def checked(x):
        a=np.asarray(x)
        if a.shape!=shape or not np.isin(a,(0,1)).all():
            raise ValueError('evidence must be a shape-matched binary mask')
        return a.astype(bool)
    evidence={k:checked(v) for k,v in dict(background=background,low_snr=low_snr,
        polarimetric=polarimetric,temporal=temporal,doppler=doppler,texture=texture).items()}
    evidence['polarimetric'] &= ~evidence['low_snr']
    if omit is not None:
        if omit not in evidence:raise ValueError('unknown ablation')
        evidence[omit]=np.zeros(shape,bool)
    e=evidence
    # Do not count low-SNR polarization as additional evidence.
    e['polarimetric'] &= ~e['low_snr']
    score=(2*e['background'].astype('uint8')+e['low_snr']+
        2*e['polarimetric'].astype('uint8')+e['temporal']+e['doppler']+e['texture'])
    families=((e['background']|e['temporal']).astype('uint8')+
        (e['low_snr']|e['polarimetric'])+e['doppler']+e['texture'])
    candidate=checked(eligible)&~checked(protected)&np.isfinite(z)&(z<=30)&e['background']&(score>=4)&(families>=3)
    return candidate,score,families
