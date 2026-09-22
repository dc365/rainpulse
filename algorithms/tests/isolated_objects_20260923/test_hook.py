from pathlib import Path
import sys,json
import numpy as np
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'clutter_fusion_20260921'))
from fusion_helpers import cfg,fixture,group
from volume_review.integration import review_result,root_attributes
from volume_review.validation import validate_serialized
from volume_review.clutter_fusion.isolation_config import IsolationConfig


@pytest.mark.parametrize('permuted',[False,True])
@pytest.mark.parametrize('mode',['audit','cr_withhold','quarantine'])
def test_real_result_hook_serialization_and_bounded_summary(permuted,mode):
    c=cfg(mode='quarantine',isolated_objects=IsolationConfig(mode=mode))
    before,native=fixture(c,permuted=permuted,near=True,receiver=True)
    out=review_result(before,native)
    a=group(out.sweeps[0]);assert 'CF_ISO_STATE' in a
    def parent(view,attrs):
        expected=group(before.sweeps[0])
        assert set(view)==set(expected)
        for key,v in expected.items():assert np.array_equal(view[key],v,equal_nan=True),key
    validate_serialized(a,root_attributes(before.profile),parent)
    record=out.summary['sweeps'][native[0].name]['clutter_fusion']
    assert 'object_records' not in record['evidence']['isolated_objects']
    detailed=json.loads(out.volume_review_artifacts['qc/volume_review/clutter_fusion.json'])
    assert 'object_records' in detailed['sweeps'][0]['evidence']['isolated_objects']


def test_audit_retains_actual_parent_quarantine():
    c=cfg(mode='quarantine');before,native=fixture(c)
    parent=review_result(before,native)
    before.profile.volume_review=before.profile.volume_review.model_copy(update={
        'clutter_fusion':c.model_copy(update={'isolated_objects':IsolationConfig(mode='audit')})})
    out=review_result(before,native);a,b=group(parent.sweeps[0]),group(out.sweeps[0])
    assert a['CF_QUARANTINE_MASK'].any()
    for key,v in a.items():assert np.array_equal(v,b[key],equal_nan=True),key
