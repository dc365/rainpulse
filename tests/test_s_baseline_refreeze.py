import importlib.util
from pathlib import Path
import numpy as np
import pytest

spec=importlib.util.spec_from_file_location('refreeze',Path(__file__).resolve().parents[1]/'scripts/refreeze_s_source_baseline.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def test_only_valid_typed_diagnostic_reason_drift_is_recorded_without_mutation():
    old={m.TRACE:np.array([[2]],'uint8'),'RV2_ACTION_PROPOSAL_MASK':np.array([[0]],'uint8')}
    fresh={key:value.copy() for key,value in old.items()};fresh[m.TRACE][0,0]=4
    changes=m.diagnostic_changes(old,fresh)
    assert changes[m.TRACE]['old_new_counts']==[[2,4,1]]
    assert old[m.TRACE][0,0]==2
    fresh['RV2_ACTION_PROPOSAL_MASK'][0,0]=1
    with pytest.raises(ValueError,match='non-diagnostic'):m.diagnostic_changes(old,fresh)


def test_missing_new_field_dtype_and_unknown_reason_are_rejected():
    old={m.TRACE:np.zeros((1,1),'uint8')}
    with pytest.raises(ValueError,match='uncaptured'):m.diagnostic_changes(old,{**old,'new':np.zeros((1,1))})
    with pytest.raises(ValueError,match='dtype'):m.diagnostic_changes(old,{m.TRACE:np.zeros((1,1),'float32')})
    with pytest.raises(ValueError,match='non-diagnostic'):m.diagnostic_changes(old,{m.TRACE:np.full((1,1),255,'uint8')})
