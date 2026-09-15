from types import SimpleNamespace
import numpy as np
from rainpulse_algo.radar.qc_engine.measurement_v8.workbench import native_coverage
from rainpulse_algo.diagnostics.renderer import BUSINESS_HARD_REJECT_FLAG_NAMES


def test_executed_core_outside_business_gates_is_not_business_coverage():
    n=SimpleNamespace(field_available={'DBZH':np.ones((1,3),bool)},fields={'DBZH':np.array([[20.,20.,20.]])})
    baseline={'QPE_ELIGIBLE_MASK':np.array([[0,1,1]]),'QC_FLAGS':np.zeros((1,3),np.uint32)}
    p=SimpleNamespace(flag_masks={k:1<<i for i,k in enumerate(BUSINESS_HARD_REJECT_FLAG_NAMES)})
    r=SimpleNamespace(scores={'1':np.array([[1.,np.nan,np.nan]]),'2':np.array([[1.,np.nan,np.nan]])})
    audit=native_coverage(n,baseline,r,p)
    assert audit['evaluated_observed_gates']==1
    assert audit['evaluated_business_ge10_gates']==0
    assert audit['unevaluated_business_ge10_gates']==2
    r.scores['1'][0,1]=0.;r.scores['2'][0,1]=0.
    assert native_coverage(n,baseline,r,p)['evaluated_business_ge10_gates']==1
