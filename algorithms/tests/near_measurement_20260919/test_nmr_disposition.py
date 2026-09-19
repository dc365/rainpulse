import numpy as np
import pytest
from near_helpers import cfg,sample,same,result_fixture,group
from volume_review.near_measurement.core import evaluate
from volume_review.near_measurement.disposition import apply,CR
from volume_review.near_measurement.validation import validate_serialized,BeforeView
from volume_review.near_measurement.integration import review_result,attributes
from volume_review.disposition import DERIVED_FIELDS


def project(c=None,snr=16):
 c=c or cfg(mode='experiment');a,az,r=sample(snr=snr);e=evaluate(a,az,r,c).arrays
 out,s=apply(a,e,c,low_quality_flag=1024);return a,out,s,c


@pytest.mark.parametrize('nonmet',['diagnostic_only','cr_withhold','quarantine'])
def test_audit_preserves_every_existing_field(nonmet):
 a,out,s,c=project(cfg(nonmet_policy=nonmet))
 for k in a:assert np.array_equal(a[k],out[k],equal_nan=True),k
 assert s['cr_loss_gates']==s['qpe_loss_gates']==0


def test_cr_only_nonmet_does_not_change_qpe_or_qi():
 a,o,s,c=project()
 assert s['cr_loss_gates']>0 and s['qpe_loss_gates']==0
 for k in ('QPE_ELIGIBLE_MASK','QC_ACTION','QC_FLAGS','QUALITY_INDEX','REFLECTIVITY_TRUST_MASK','DBZH_RAW','DBZH_QC'):
  assert np.array_equal(a[k],o[k]),k
 assert np.all(o['CR_UNCERTAIN_MASK'][o['NMR_CR_WITHHELD_MASK']==1]==1)


def test_low_snr_uncertainty_never_changes_qpe_even_in_quarantine_mode():
 a,o,s,c=project(cfg(mode='experiment',nonmet_policy='quarantine'),snr=2)
 assert s['cr_uncertainty_only_loss_gates']>0 and s['qpe_loss_gates']==0
 assert not o['NMR_QUARANTINE_MASK'].any()
 assert np.array_equal(a['QPE_ELIGIBLE_MASK'],o['QPE_ELIGIBLE_MASK'])


def test_quarantine_updates_all_trust_but_not_old_rfi_np_reasons():
 a,o,s,c=project(cfg(mode='experiment',nonmet_policy='quarantine'))
 q=o['NMR_QUARANTINE_MASK']==1;assert q.any()
 assert not o['QPE_ELIGIBLE_MASK'][q].any();assert not o[CR][q].any()
 for k in a:
  if k.endswith('_TRUST_MASK'):assert not o[k][q].any()
 assert np.array_equal(a['RFI_QUARANTINE_MASK'],o['RFI_QUARANTINE_MASK'])
 assert np.array_equal(a['NP_QUARANTINE_MASK'],o['NP_QUARANTINE_MASK'])
 assert np.isnan(o['DBZH_USABLE'][q]).all()


def test_preexisting_ineligible_is_not_restored_and_idempotence_rejected():
 a,az,r=sample();a[CR][:,20:30]=0;c=cfg(mode='experiment');e=evaluate(a,az,r,c).arrays
 o,s=apply(a,e,c,low_quality_flag=1024);assert not o[CR][:,20:30].any()
 with pytest.raises(ValueError,match='already'):apply(o,e,c,low_quality_flag=1024)


def test_derived_trusted_segment_is_invalidated_not_only_removed_gate():
 a,az,r=sample();a['VOR_STATE']=np.full(a['VALID_MASK'].shape,3,'uint8');a['VOR_STATE'][6,50]=1
 for k in DERIVED_FIELDS:a[k]=np.ones_like(a['VALID_MASK']) if k.endswith('_MASK') else np.ones_like(a['DBZH_RAW'])
 c=cfg(mode='experiment',nonmet_policy='quarantine');e=evaluate(a,az,r,c).arrays
 o,s=apply(a,e,c,low_quality_flag=1024);assert s['added_quarantine_gates']==1
 assert o['NMR_DERIVED_INVALIDATION_MASK'][6].all();assert not o['KDP_OS_AVAILABLE_MASK'][6].any()
 assert np.isnan(o['KDP_OS'][6]).all()


@pytest.mark.parametrize('key',['DBZH_RAW','QC_ACTION','QC_FLAGS','QPE_ELIGIBLE_MASK',CR,'NMR_QUARANTINE_MASK','NMR_NONMET_CR_WITHHELD_MASK'])
def test_delta_validator_refuses_corruption(key):
 a,o,s,c=project(cfg(mode='experiment',nonmet_policy='quarantine'))
 def parent(v,attrs):same({k:v[k] for k in v},a)
 validate_serialized(o,attributes(c,1024),parent)
 o[key]=o[key].copy();o[key][6,50]=9
 with pytest.raises((ValueError,AssertionError)):validate_serialized(o,attributes(c,1024),parent)


def test_missing_baseline_and_forged_identity_rejected():
 a,o,s,c=project();at=attributes(c,1024);at['qc_near_measurement_sha256']='f'*64
 with pytest.raises(ValueError):validate_serialized(o,at,lambda *_:None)
 del o['NMR_BEFORE_QC_FLAGS']
 with pytest.raises(ValueError,match='pre-near'):validate_serialized(o,attributes(c,1024),lambda *_:None)


@pytest.mark.parametrize('permuted',[False,True])
def test_actual_post_volume_adapter_contract(permuted):
 c=cfg(mode='experiment',nonmet_policy='quarantine');result,native=result_fixture(c,permuted)
 out=review_result(result,native);g=group(out.sweeps[0]);a=group(result.sweeps[0])
 calls=[]
 def parent(v,attrs):
  same({k:v[k] for k in v},a);calls.append(1)
 validate_serialized(g,attributes(c,1024),parent)
 assert calls==[1];assert out.summary['near_measurement']['added_quarantine_gates']>0
 assert not g['QPE_ELIGIBLE_MASK'][g['NMR_QUARANTINE_MASK']==1].any()
 assert 'qc/volume_review/near_measurement.json' in out.volume_review_artifacts


def test_no_near_extension_is_identity():
 result,native=result_fixture(cfg());result.profile.volume_review=result.profile.volume_review.model_copy(update={'near_measurement':None})
 assert review_result(result,native) is result
