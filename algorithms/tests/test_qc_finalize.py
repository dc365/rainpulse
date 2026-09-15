from types import SimpleNamespace as NS
import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.finalize import finalize_decision


@pytest.mark.parametrize('health',['HEALTHY','DEGRADED'])
def test_finalization_preserves_actions_reasons_and_excludes_unusable(health):
    action=np.array([[0,1,2,3]],dtype='uint8')
    arrays={'QC_ACTION':action.copy(),'QPE_ELIGIBLE_MASK':np.array([[1,0,0,0]],dtype='uint8'),
            'REASON':np.array([[0,7,9,11]])}
    d=NS(arrays=arrays,quality=np.array([[.8,.5,.2,np.nan]],dtype='float32'),flags=np.zeros((1,4),dtype='uint32'))
    s=NS(field_available={'DBZH':np.array([[True,True,True,False]])},fields={'DBZH':np.array([[30.,40.,50.,np.nan]])})
    p=NS(health_gate=NS(degraded_quality_multiplier=.5),quality_index=NS(quantitative_minimum=.6,low_quality_threshold=.3),flag_masks={'LOW_QUALITY':4})
    quality,observed,low,flags=finalize_decision(s,d,p,{'health':health})
    np.testing.assert_equal(d.arrays['QC_ACTION'],action)
    np.testing.assert_equal(d.arrays['REASON'],[[0,7,9,11]])
    assert np.isnan(d.arrays['DBZH_USABLE'][0,1:]).all()
    assert d.arrays['QPE_ELIGIBLE_MASK'][0,0]==(health=='HEALTHY')
    assert flags[0,3]==0 and not observed[0,3]
    np.testing.assert_allclose(d.quality,[[.8,.5,.2,np.nan]],rtol=1e-6)
