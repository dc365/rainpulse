import numpy as np
from volume_review.data import Sweep


def sweep(name="sweep_000",el=.5,offset=0.,with_polar=True,missing_sides=True):
    nr,ng=24,160
    az=(np.arange(nr)+offset)%360
    r=500.+np.arange(ng)*500.
    snr=np.full((nr,ng),np.nan,"float32")
    snr[8:11]=30.
    z=snr+20*np.log10(r[None,:]/1000)+.012*r[None,:]/1000+3
    if not missing_sides:
        z[np.isnan(z)]=-5
    fields={"DBZH":z.astype("float32")}
    if with_polar:
        for k,val in (("SNR",snr),("RHOHV",np.where(np.isfinite(snr),.7,np.nan)),
                      ("ZDR",np.where(np.isfinite(snr),.2,np.nan)),
                      ("PHIDP",np.where(np.isfinite(snr),2.,np.nan))):
            fields[k]=val.astype("float32")
    gaps=np.zeros(nr,bool); gaps[-1]=True
    return Sweep(name,az,np.full(nr,el),r,fields,{k:np.isfinite(v) for k,v in fields.items()},
                 np.ones(nr,bool),gaps,np.full(nr,10. if name.endswith("000") else 50.))


def change(s,field,mutator):
    f={k:v.copy() for k,v in s.fields.items()}
    mutator(f[field])
    return Sweep(s.name,s.azimuth,s.elevation,s.ranges,f,{k:np.isfinite(v) for k,v in f.items()},s.good,s.gap_after,s.ray_time_s)


def baseline(s):
    obs=s.observed
    a={"QC_ACTION":np.where(obs,0,3).astype("uint8"),"REFLECTIVITY_TRUST_MASK":obs.astype("uint8"),
       "QPE_ELIGIBLE_MASK":obs.astype("uint8"),"DBZH_USABLE":np.where(obs,s.fields["DBZH"],np.nan).astype("float32"),
       "P2_ADMIN_PENALTY_REMOVED_MASK":np.zeros(s.shape,"uint8")}
    for k in ("SNR","RHOHV","ZDR","PHIDP","VR","SW"):
        a[k+"_TRUST_MASK"]=s.moment(k)[1].astype("uint8")
    return a,np.zeros(s.shape,"uint32"),np.where(obs,.9,np.nan).astype("float32")


class Root(dict):
    def __init__(self,*a,attrs=None,**kw):
        super().__init__(*a,**kw); self.attrs={} if attrs is None else attrs


def root_from_sweeps(sweeps,cr_masks=None):
    root=Root(attrs={"radar_id":"anonymous","scan_id":"volume-0","asset_id":"asset-0",
        "site_longitude_deg":0.,"site_latitude_deg":0.,"qc_volume_review_phase":3,
        "qc_volume_review_sha256":"a"*64,"qc_parameters_sha256":"b"*64,
        "qc_volume_review_mode":"audit"})
    root['sweep_number']=np.arange(len(sweeps),dtype="int32")
    for i,s in enumerate(sweeps):
        root[f"sweep_{i:03d}"]={"DBZH_RAW":s.fields["DBZH"].copy(),"DBZH_QC":s.fields["DBZH"].copy(),
          "VALID_MASK":s.observed.astype("uint8"),"QC_FLAGS":np.zeros(s.shape,"uint32"),
          "REFLECTIVITY_ELIGIBLE_FOR_CR":(s.observed if cr_masks is None else cr_masks[i]).astype("uint8"),
          "CR_UNCERTAIN_MASK":np.zeros(s.shape,"uint8"),"CR_QUALIFICATION_REASON":np.ones(s.shape,"uint16"),
          "azimuth":s.azimuth.copy(),"range":s.ranges.copy(),"elevation":s.elevation.copy()}
    return root
