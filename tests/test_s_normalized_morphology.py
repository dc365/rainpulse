import importlib.util
from pathlib import Path
import pytest

p=Path(__file__).resolve().parents[1]/'scripts/audit_s_normalized_morphology.py'
spec=importlib.util.spec_from_file_location('normalized_audit',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture():
    scan='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    return dict(radar_id='z9591',scan_id=scan,normalized_uri=f's3://rainpulse/radar/normalized/z9591/{scan}/volume.zarr')


def test_frozen_plan_rejects_duplicate_scan_and_wrong_uri():
    row=fixture()
    assert m.validate_plan([row])==[row]
    with pytest.raises(ValueError,match='ambiguous'):m.validate_plan([row,row])
    row['normalized_uri']=row['normalized_uri'].replace('z9591','z9598')
    with pytest.raises(ValueError,match='ambiguous'):m.validate_plan([row])


def test_frozen_plan_rejects_unbounded_or_non_s_station_inputs():
    with pytest.raises(ValueError,match='1–16'):m.validate_plan([])
    with pytest.raises(ValueError,match='1–16'):m.validate_plan([fixture()]*17)
    row=fixture();row['radar_id']='zf501'
    with pytest.raises(ValueError,match='S station'):m.validate_plan([row])


def test_detector_routes_keep_fragment_options_out_of_complete_fan_replays():
    assert m.detector_spec('constellation')[1]=={'segment_evidence':True}
    for name,module in [('whole-object','morphology_objects'),('variable-width','variable_morphology')]:
        actual,options=m.detector_spec(name)
        assert actual.endswith('.'+module) and options=={}
    with pytest.raises(ValueError,match='read-only'):m.detector_spec('engine-quarantine')
    assert m.detector_spec('physical-windows')[1]=={'physical_windows':True}
