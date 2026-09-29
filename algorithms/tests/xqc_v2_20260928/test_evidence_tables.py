import json
from rainpulse_algo.multiband.xqc_v2.evidence_tables import compact, expand


def test_lossless_record_compaction_and_legacy_reading():
    records = {'module_records': {'source': {'status': 'EVALUATED', 'models': [
        {'ray': i, 'target_block': i % 20, 'reference_blocks': [1, 3, 9],
         'response_bounds_db': [-21.123456789, 3.5], 'available': True}
        for i in range(1000)]}}}
    encoded = compact(records)
    assert expand(encoded) == records
    assert expand(records) == records
    assert records['module_records']['source']['models'][0]['ray'] == 0
    assert len(json.dumps(encoded)) < .6 * len(json.dumps(records))
    assert encoded['module_records']['source']['status'] == 'EVALUATED'


def test_heterogeneous_and_empty_records_are_preserved():
    value = {'models': [{'a': 1}, {'a': 2, 'b': None}], 'empty': [], 'tuple': [1, 2]}
    assert expand(compact(value)) == value


def test_record_budget_keeps_actions_when_lossless_table_fits(monkeypatch):
    import numpy as np
    from .helpers import fixture, config
    from rainpulse_algo.multiband.xqc_v2 import radial_source, evidence_tables
    from rainpulse_algo.multiband.xqc_v2.core import evaluate_cut
    volume, row = fixture()
    cfg = config(receiver_enabled=False, radial_objects_enabled=False,
                 clutter_enabled=False, isolation_enabled=False,
                 radial_source_enabled=True, noise_censor_snr_db=0.,
                 maximum_evidence_bytes=4096)
    def source(s, cfg, **kwargs):
        mask=np.zeros(s.shape,bool);mask[row,100]=True
        return mask, {'status':'EVALUATED','models':[
            {'reference_gate_observation_is_available': True,
             'source_response_upper_bound_in_decibels': 23.5,
             'source_reference_block_identifier': i} for i in range(70)]}
    monkeypatch.setattr(radial_source, 'detect', source)
    real=evidence_tables.compact
    monkeypatch.setattr(evidence_tables, 'compact', lambda x:x)
    old=evaluate_cut(volume.sweeps[0], volume.metadata, cfg)
    assert old.record['status']=='RESOURCE_OR_GEOMETRY_ABSTAINED'
    assert not old.arrays['XQC_QUARANTINE_MASK'].any()
    monkeypatch.setattr(evidence_tables, 'compact', real)
    fixed=evaluate_cut(volume.sweeps[0], volume.metadata, cfg)
    assert fixed.record['status']=='EVALUATED'
    assert fixed.arrays['XQC_QUARANTINE_MASK'][row,100]==1
    assert len(json.dumps(fixed.record,separators=(',',':')).encode()) <= 4096
