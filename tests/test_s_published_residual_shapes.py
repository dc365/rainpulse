"""Reject residual selections that do not match immutable published native data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'algorithms'))
spec=importlib.util.spec_from_file_location('residual_shapes',ROOT/'scripts/audit_s_published_residual_shapes.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def inputs(tmp_path):
    snapshot=tmp_path/'raw.npz';receipt=tmp_path/'published.json'
    np.savez(snapshot,METADATA=np.array(json.dumps({'scan_id':'original'})),RAW=np.array([[25.]]),
        AZIMUTH=np.array([30.]),RANGE=np.array([100000.]))
    report=dict(scope='exact_Web_consumed_stored_QC_not_replay',
        snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(),
        web_frame_identity={'web_scan_id':'original'},target_gates=1,renderer_eligible_visible_gates=1,
        target_records=[dict(row=0,column=0,raw_dbzh=25.,azimuth_deg=30.,range_m=100000.,renderer_visible=True)])
    return snapshot,receipt,report


@pytest.mark.parametrize('change,match',[
    ('scope','actual published'),('digest','snapshot mismatch'),('scan','scan mismatch'),
    ('duplicate','unique integer'),('coordinate','coordinate/measurement'),('count','count mismatch')])
def test_audit_rejects_unbound_or_forged_native_targets(tmp_path,change,match):
    snapshot,receipt,data=inputs(tmp_path)
    if change=='scope':data['scope']='prototype'
    if change=='digest':data['snapshot_sha256']='different'
    if change=='scan':data['web_frame_identity']['web_scan_id']='different'
    if change=='duplicate':data['target_records']*=2
    if change=='coordinate':data['target_records'][0]['range_m']=100001.
    if change=='count':data['renderer_eligible_visible_gates']=0
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError,match=match):m.audit(snapshot,receipt)


def test_bounded_exterior_band_requires_measured_windows(tmp_path):
    with pytest.raises(ValueError,match='requires measured windows'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',shoulder_band=True)


def test_short_subset_cannot_run_without_measured_window_evidence(tmp_path):
    with pytest.raises(ValueError,match='short subset requires measured windows'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',short_subset=True)


def test_parent_footprint_cannot_run_without_short_original_evidence(tmp_path):
    with pytest.raises(ValueError,match='parent footprint requires short subset evidence'):
        m.audit(tmp_path/'unused.npz',tmp_path/'unused.json',parent_footprint=True)
