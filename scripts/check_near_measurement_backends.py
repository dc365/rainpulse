"""Run real installed-library parity checks; failure is not reported as success."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"algorithms/rainpulse_algo/radar/qc_engine"))
import numpy as np
from volume_review.near_measurement.config import NearMeasurementConfig
from volume_review.near_measurement.backends import depolarization,require,reference_depolarization


def main():
 c=NearMeasurementConfig();z=np.linspace(-7.,7.,101)[:,None];r=np.linspace(0.,1.,100)[None,:]
 actual,record=depolarization(z,r,c);expected=np.maximum(reference_depolarization(z,r),-100.)
 if not np.allclose(actual,expected,atol=2e-5,rtol=1e-6,equal_nan=True):raise RuntimeError('wradlib DR parity failed')
 pyart=require('arm_pyart','pyart',c.pyart_version)
 radar=pyart.testing.make_empty_ppi_radar(100,101,1)
 radar.add_field('reflectivity',{'data':np.broadcast_to(z,(101,100)).copy()})
 gf=pyart.filters.GateFilter(radar);mask=np.indices((101,100)).sum(axis=0)%3==0
 gf.exclude_gates(mask,op='or');gf.exclude_gates(mask,op='or')
 if not np.array_equal(gf.gate_excluded,mask):raise RuntimeError('GateFilter parity failed')
 print(json.dumps({'wradlib':record,'DR_max_abs_error_db':float(np.max(abs(actual-expected))),
                  'pyart_version':c.pyart_version,'gatefilter_mask_parity':True}))

if __name__=='__main__':main()
