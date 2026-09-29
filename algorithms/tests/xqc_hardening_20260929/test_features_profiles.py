import copy, hashlib, json
import numpy as np
import pytest
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.features import extract, PreparedFeatures
from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.engine import evaluate_volume
from rainpulse_algo.multiband.xqc_v2.geometry import adapt
from rainpulse_algo.multiband.xqc_v2.release_profiles import generate, parse_json
from .helpers import fixture,config


def test_shared_features_produce_identical_cf_output_without_recompute(monkeypatch):
    v,_=fixture('weather',rays=120,gates=80);c=config();s=adapt(v.sweeps[0],c).sweep
    old=evaluate_volume([s],c.clutter)[0]
    features=PreparedFeatures.bind(s,c.clutter,extract(s,c.clutter))
    def forbidden(*a,**k):raise AssertionError('recomputed immutable features')
    monkeypatch.setattr('rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.engine.extract',forbidden)
    new=evaluate_volume([s],c.clutter,prepared_features=[features])[0]
    for k,a in old.arrays.items():np.testing.assert_array_equal(new.arrays[k],a)
    assert old.summary==new.summary
    with pytest.raises(ValueError):features.for_sweep(adapt(v.sweeps[0],c).sweep,c.clutter)
    with pytest.raises(ValueError):features.for_sweep(s,c.clutter.model_copy(update={'minimum_snr_db':19.}))


def parent():
    return {'schema_version':'1.0','release_id':'parent','products':{},'stations':{
      'x01':{'band':'X','source':'normalized_zarr','x_qc_enabled':True,'x_qc':{
        'enhancement':config(noise_censor_snr_db=4.,receiver_enabled=False).model_dump(mode='json')}},
      's01':{'band':'S','source':'s_qc_zarr'}}}


def upgrade(p=None,**kw):
    p=parent() if p is None else p;raw=json.dumps(p,sort_keys=True).encode()
    return generate(raw,parent_sha256=hashlib.sha256(raw).hexdigest(),radar_ids=['x01'],release_id='child',**kw)


def test_config_upgrade_preserves_unselected_and_nested_parameters():
    p=parent();before=copy.deepcopy(p)
    a,receipt=upgrade(p,updates={'x01':{'receiver':{'minimum_snr_db':21.}}})
    b,r2=upgrade(p,updates={'x01':{'receiver':{'minimum_snr_db':21.}}})
    out=json.loads(a)
    assert p==before and a==b and receipt==r2
    assert out['stations']['s01']==p['stations']['s01']
    ev=out['stations']['x01']['x_qc']['enhancement']
    assert ev['noise_censor_snr_db']==4. and ev['receiver']['minimum_snr_db']==21.
    assert ev['receiver']['block_m']==p['stations']['x01']['x_qc']['enhancement']['receiver']['block_m']
    assert ev['context']['mode']=='disabled' and not out['stations']['x01'].get('calibration_verified',False)
    assert receipt['deployment_performed'] is False


def test_fan_upgrade_explicit_floor_and_no_hidden_case_parameters():
    p=parent();p['stations']['x01']['x_qc']['enhancement']['noise_censor_snr_db']=None
    with pytest.raises(ValueError):upgrade(p,preset='fan')
    a,_=upgrade(p,preset='fan',updates={'x01':{'noise_censor_snr_db':4.}})
    c=json.loads(a)['stations']['x01']['x_qc']['enhancement']
    assert c['radial_source_fan_model_enabled'] and c['radial_source_block_model_enabled']
    assert c['radial_source_maximum_width_deg']==3. and c['maximum_new_exclusion_fraction']==1.


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}'])
def test_release_json_is_strict(raw):
    with pytest.raises(ValueError):parse_json(raw)


def test_changed_parent_and_unselected_updates_are_rejected():
    p=parent();raw=json.dumps(p).encode()
    with pytest.raises(ValueError):generate(raw,parent_sha256='0'*64,radar_ids=['x01'],release_id='child')
    with pytest.raises(ValueError):upgrade(p,updates={'s01':{}})


def test_x_coordinator_extracts_current_features_only_once(monkeypatch):
    from rainpulse_algo.multiband.xqc_v2 import core
    v,_=fixture('clutter',rays=120,gates=80)
    c=config(receiver_enabled=False,radial_objects_enabled=False,isolation_enabled=False)
    calls=[];original=core.extract_features
    def counted(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(core,'extract_features',counted)
    def forbidden(*args,**kwargs):raise AssertionError('CF duplicated current feature computation')
    monkeypatch.setattr('rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.engine.extract',forbidden)
    ev=core.evaluate_cut(v.sweeps[0],v.metadata,c)
    assert ev.record['status']=='EVALUATED' and len(calls)==1


@pytest.mark.parametrize('patch',[{'source_maximum_trials':True},{'source_maximum_models':1.5},
    {'source_maximum_summary_bytes':False},{'context':{'maximum_donors':True}}])
def test_new_resource_contracts_reject_noninteger_types(patch):
    with pytest.raises(ValueError):config(**patch)
