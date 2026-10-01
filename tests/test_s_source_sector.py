"""Review regions must follow native geometry, not station or case identities."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
SPEC=importlib.util.spec_from_file_location('source_audit',Path(__file__).parents[1]/'scripts/audit_s_source_footprint.py')
mod=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(mod)

def test_native_sector_crosses_north_and_preserves_input_order():
    az=np.array([355.,5.,180.,np.nan]); r=np.array([50000.,150000.,250000.])
    before=az.copy()
    actual=mod.select_roi(az,r,azimuth_start=350,azimuth_end=10,range_min=100000,range_max=200000)
    assert actual.tolist()==[[False,True,False],[False,True,False],[False,False,False],[False,False,False]]
    np.testing.assert_equal(az,before)

def test_default_selects_entire_observed_geometry_and_rejects_half_sector():
    assert mod.select_roi([300.,40.],[0.,100.]).all()
    with pytest.raises(ValueError):mod.select_roi([40.],[0.],azimuth_start=20.)
