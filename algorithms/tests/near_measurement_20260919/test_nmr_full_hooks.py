from dataclasses import replace
import numpy as np
import pytest
from near_helpers import cfg,result_fixture,group,same
from volume_review.config import VolumeReviewConfig
from volume_review.integration import review_result,root_attributes
from volume_review.validation import validate_serialized
from volume_review.receipts import snapshot_group,npz_bytes,load_npz
from volume_review.composite import build_composite,trace_pixel


@pytest.mark.parametrize('near_mode',['audit','experiment'])
@pytest.mark.parametrize('permuted',[False,True])
def test_vor_to_near_to_recursive_contract_validation(near_mode,permuted):
 c=cfg(mode=near_mode,nonmet_policy='quarantine');initial,native=result_fixture(c,permuted)
 q=initial.sweeps[0];optional={k:v for k,v in q.optional_qc_fields.items() if not k.startswith('CR_') and k!='REFLECTIVITY_ELIGIBLE_FOR_CR'}
 q=replace(q,optional_qc_fields=optional);initial=replace(initial,sweeps=(q,))
 initial.profile.volume_review=VolumeReviewConfig(mode='experiment_quarantine',unknown_cr_policy='retain_with_risk',near_measurement=c)
 out=review_result(initial,native);calls=[]
 def legacy(view,attrs):
  assert 'qc_volume_review_version' not in attrs
  same({k:view[k] for k in view},group(initial.sweeps[0]));calls.append(1)
 validate_serialized(group(out.sweeps[0]),root_attributes(initial.profile),legacy)
 assert calls==[1];assert 'near_measurement' in out.summary
 assert bool(out.sweeps[0].optional_qc_fields['NMR_QUARANTINE_MASK'].any())==(near_mode=='experiment')
 snap=snapshot_group(group(out.sweeps[0]));roundtrip=load_npz(npz_bytes(snap));same(snap,roundtrip)
 assert 'NMR_NONMET_CANDIDATE_MASK' in snap and not any(k.startswith('NMR_BEFORE_') for k in snap)


class Root(dict):
 def __init__(self,attrs):super().__init__();self.attrs=attrs


def root_fixture():
 initial,native=result_fixture(cfg(mode='experiment',nonmet_policy='cr_withhold'))
 # Full parent hook creates the provenance used by the compositor.
 q=initial.sweeps[0];q=replace(q,optional_qc_fields={k:v for k,v in q.optional_qc_fields.items() if not k.startswith('CR_') and k!='REFLECTIVITY_ELIGIBLE_FOR_CR'})
 initial=replace(initial,sweeps=(q,));initial.profile.volume_review=VolumeReviewConfig(mode='experiment_quarantine',unknown_cr_policy='retain_with_risk',near_measurement=cfg(mode='experiment'))
 out=review_result(initial,native)
 r=Root({**root_attributes(initial.profile),'radar_id':'SITE_TEST','scan_id':'scan','asset_id':'asset',
         'site_longitude_deg':0.,'site_latitude_deg':0.,'qc_parameters_sha256':'a'*64})
 r['sweep_number']=np.array([0],dtype='int32')
 r['sweep_000']={**group(out.sweeps[0]),'azimuth':native[0].azimuth,'range':native[0].ranges,'elevation':native[0].elevation}
 return r


def test_real_compositor_uses_all_gate_qualification_and_exposes_risk():
 r=root_fixture();p=build_composite([r],0,maximum_size=128)
 assert 'CR_NEAR_WITHHELD' in p.arrays
 assert np.isfinite(p.arrays['CR_NEAR_WITHHELD']).any()
 assert not np.isfinite(p.arrays['CR_TRUSTED'])[np.isfinite(p.arrays['CR_NEAR_WITHHELD'])].any()
 for row,col in np.argwhere(np.isfinite(p.arrays['CR_TRUSTED']))[::10]:
  assert trace_pixel(p,[r],int(row),int(col))['status']=='RECONSTRUCTED'


def test_compositor_rejects_quarantine_leak():
 r=root_fixture();a=r['sweep_000'];m=a['NMR_CR_WITHHELD_MASK']==1;a['REFLECTIVITY_ELIGIBLE_FOR_CR'][m]=1
 with pytest.raises(ValueError,match='leaked'):build_composite([r],0,maximum_size=128)


def test_compositor_rejects_mixed_near_versions():
 r=root_fixture();other=Root(dict(r.attrs));other.update(r);other.attrs['qc_near_measurement_sha256']='b'*64
 with pytest.raises(ValueError,match='mixed'):build_composite([r,other],0,maximum_size=128)
