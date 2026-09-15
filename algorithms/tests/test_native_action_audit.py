import importlib.util
from pathlib import Path
import numpy as np

spec=importlib.util.spec_from_file_location('audit',Path(__file__).resolve().parents[2]/'scripts/qc_native_action_audit.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)


def test_unknown_and_existing_quarantine_are_not_new_review():
    q={'QC_ACTION':np.array([[0,0,1,0]]),'DBZH_RAW':np.full((1,4),30.),
       'VALID_MASK':np.ones((1,4)),'RFI_QUARANTINE_MASK':np.array([[0,0,1,0]]),
       'RHOHV_RAW':np.full((1,4),.85),'SNR_RAW':np.full((1,4),10.),
       'RHOHV_TRUST_MASK':np.array([[1,1,1,0]])}
    before=q['QC_ACTION'].copy()
    report,_,review=audit.analyse(q,np.array([[.8,np.nan,.8,.8]]),np.full((1,4),np.nan))
    np.testing.assert_equal(review,[[True,False,False,False]])
    np.testing.assert_equal(q['QC_ACTION'],before)
    assert report['scored']==3 and report['review_only_gates']==1
    assert not report['operational_eligible']
