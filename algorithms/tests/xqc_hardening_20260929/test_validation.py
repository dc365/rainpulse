import copy
import numpy as np
import pytest
from rainpulse_algo.multiband.quality import x_qc, Flag
from rainpulse_algo.multiband.xqc_v2.pipeline import validate_output
from .helpers import fixture, config, station


def result():
    v,_=fixture('weather',rays=24,gates=40)
    c=config(mode='quarantine',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    out=x_qc(v,station(c),'a'*64).sweeps[0]
    # Inject an internally valid rejected gate, then test independent corruptions.
    f=out.fields;g=(0,0)
    for k in ('XQC_REJECTED_MASK','XQC_WITHHELD_MASK'):f[k][g]=1
    f['QC_ACTION'][g]=2;f['MB_QC_FLAGS'][g]|=int(Flag.NONMET_CONFIRMED)
    f['DBZH_QC'][g]=np.nan;f['DBZH_QC_DISPLAY'][g]=np.nan;f['XQC_REASON'][g]=32768
    validate_output(v.sweeps[0],out,c)
    return v.sweeps[0],out,c


@pytest.mark.parametrize('case',['numeric','display','flag','action','reason','eligibility','qpe','raw','availability','coordinate'])
def test_exit_validation_rejects_corrupted_product(case):
    raw,qc,c=result();f=qc.fields;g=(0,0)
    if case=='numeric':f['DBZH_QC'][g]=20
    elif case=='display':f['DBZH_QC_DISPLAY'][g]=20
    elif case=='flag':f['MB_QC_FLAGS'][g]&=np.uint16(65535-int(Flag.NONMET_CONFIRMED))
    elif case=='action':f['QC_ACTION'][g]=3
    elif case=='reason':f['XQC_REASON'][g]=0
    elif case=='eligibility':f['REFLECTIVITY_ELIGIBLE_FOR_CR'][g]=1
    elif case=='qpe':f['QPE_ELIGIBLE_MASK'][g]=1
    elif case=='raw':f['DBZH_RAW']=f['DBZH_RAW'].copy();f['DBZH_RAW'][g]+=1
    elif case=='availability':f['OBSERVED_MASK']=f['OBSERVED_MASK'].copy();f['OBSERVED_MASK'][g]=2
    else:qc.azimuth_deg=qc.azimuth_deg.copy();qc.azimuth_deg[0]+=.1
    with pytest.raises(ValueError):validate_output(raw,qc,c)


def test_audit_does_not_change_baseline_numeric_actions_or_qualification():
    v,_=fixture('weather',rays=24,gates=40)
    c=config(mode='audit',receiver_enabled=False,radial_objects_enabled=False,clutter_enabled=False,isolation_enabled=False)
    base=x_qc(v,station(),'a'*64).sweeps[0].fields
    audit=x_qc(v,station(c),'a'*64).sweeps[0].fields
    for k,a in base.items():np.testing.assert_array_equal(audit[k],a)
