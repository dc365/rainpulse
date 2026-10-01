"""Measured signal-space radial tracks; experimental diagnostics, never sources.

A weak RAW fragment cannot enlarge the initial angular stencil. Actual signal
and both flank measurements are required; gaps/missing are not quiet evidence.
Target/adjacent range blocks do not train the receiver-power consistency test.
"""
from types import SimpleNamespace
import numpy as np
from ..arrays import moment, mask, native_geometry
from .raw_fans import detect

PREFIX='RV2_SIGNAL_TRACK_'


def diagnose(native,blocked):
    r,az,dr,good,gaps=native_geometry(native)
    z,obs=moment(native,'DBZH');snr,snr_ok=moment(native,'SNR')
    barriers=mask(blocked,native.shape,'signal track barriers')|~good[:,None]
    measured=obs&snr_ok&(snr>=5.)&~barriers
    # A read-only signal selection view; no RAW values/availability are mutated.
    view=SimpleNamespace(shape=native.shape,fields={'DBZH':z},field_available={'DBZH':measured},
        ranges=r,azimuth=az,geometry_good=good,gap_after=gaps)
    fans,detail=detect(view,barriers,np.zeros(native.shape,'uint32'))
    parent=fans['RV2_RAW_FAN_ID'];nominated=parent>0
    matched=np.zeros(native.shape,bool);hold=np.zeros(native.shape,'uint8');hold[nominated]=1
    proofs={k:np.full(native.shape,np.nan,'float32') for k in
        ('REFERENCE_SNR_DB','REFERENCE_BLOCKS','REFERENCE_SPAN_M','LEFT_QUIET_FRACTION','RIGHT_QUIET_FRACTION')}
    blocks=(r//20000.).astype(int)
    for record in detail['records']:
        identity=record['id'];members=parent==identity
        rows,cols=np.where(members)
        if record['end_m']-record['start_m']<60000.:continue
        left,right=int(rows.min())-1,int(rows.max())+1
        if left<0 or right>=len(az) or gaps[left:right].any():continue
        width=float(np.max(fans['RV2_RAW_FAN_RIGHT_DEG'][members]-fans['RV2_RAW_FAN_LEFT_DEG'][members]))
        if width>8.:continue
        # Both ORIGINAL outer rays must remain measured and unprotected across
        # the full identity. Never find a quieter farther flank adaptively.
        interval=(r>=record['start_m'])&(r<record['end_m'])
        if barriers[left:right+1,interval].any():continue
        for target_block in np.unique(blocks[cols]):
            t=blocks[cols]==target_block;tr,tc=rows[t],cols[t]
            ref=abs(blocks[cols]-target_block)>1
            rr,cc=rows[ref],cols[ref];unique=np.unique(blocks[cc])
            if len(unique)<3 or len(cc)*dr<20000. or np.ptp(r[cc])<60000.-dr:continue
            hold[tr,tc]=2
            # Evaluate several independently observed range windows. Every
            # reference window needs bilateral quiet signal measurements.
            centres=[];quiet=[];accepted_blocks=[]
            for block in unique:
                window=blocks==block
                pair=[];valid=True
                for flank in (left,right):
                    available=snr_ok[flank,window]&~barriers[flank,window]
                    q=available&(snr[flank,window]<=3.)
                    if available.mean()<.9 or q.mean()<.9:valid=False;break
                    pair.append(float(q.mean()))
                if not valid:continue
                accepted_blocks.append(block)
                cells=ref&(blocks[cols]==block)
                centres.append(float(np.median(snr[rows[cells],cols[cells]])))
                quiet.append(pair)
            if len(accepted_blocks)<3 or len(accepted_blocks)<.8*len(unique):continue
            reference=ref&np.isin(blocks[cols],accepted_blocks)
            reference_cols=cols[reference]
            if len(reference_cols)*dr<20000. or np.ptp(r[reference_cols])<60000.-dr:continue
            centre=float(np.median(centres))
            if (centre<7. or np.percentile(abs(np.asarray(centres)-centre),90)>2.5 or
                abs(np.median(centres[::2])-np.median(centres[1::2]))>1.5):continue
            hold[tr,tc]=3
            window=blocks==target_block
            target_quiet=[];valid=True
            for flank in (left,right):
                available=snr_ok[flank,window]&~barriers[flank,window]
                q=available&(snr[flank,window]<=3.)
                if available.mean()<.9 or q.mean()<.9:valid=False;break
                target_quiet.append(float(q.mean()))
            if not valid:continue
            use=abs(snr[tr,tc]-centre)<=2.5
            tr,tc=tr[use],tc[use]
            matched[tr,tc]=True;hold[tr,tc]=0
            for key,value in dict(REFERENCE_SNR_DB=centre,REFERENCE_BLOCKS=len(accepted_blocks),
                REFERENCE_SPAN_M=float(np.ptp(r[reference_cols])),LEFT_QUIET_FRACTION=target_quiet[0],
                RIGHT_QUIET_FRACTION=target_quiet[1]).items():proofs[key][tr,tc]=value
    rho,rho_ok=moment(native,'RHOHV')
    weather_like=matched&rho_ok&(rho>=.95)&(snr>=10.)
    matched[weather_like]=False;hold[weather_like]=4
    for values in proofs.values():values[weather_like]=np.nan
    return {PREFIX+'NOMINATED_MASK':nominated.astype('uint8'),PREFIX+'MODEL_MATCH_MASK':matched.astype('uint8'),
        PREFIX+'RAW_PARENT_ID':parent,PREFIX+'HOLD_REASON':hold,
        **{PREFIX+k:v for k,v in proofs.items()}},dict(version='measured-signal-tracks-v1',
        nominated_gates=int(nominated.sum()),model_match_gates=int(matched.sum()),
        weather_like_retained_gates=int(weather_like.sum()),actions=0,source_claim=False,
        recursive_growth=False,filled_gates=0)
