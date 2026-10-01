"""Bind diagnostics to consumed QC; latest scan state is not render lineage."""
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
spec=importlib.util.spec_from_file_location('published_qc',ROOT/'scripts/audit_s_published_qc.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def fixture():
    job='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    frame={'image_url':f'/api/v1/diagnostics/{job}/layers/raw'}
    web=dict(raw_frame=frame,qc_frame=frame,web_scan_id='scan',site='site')
    receipt=dict(job_id=job,status='SUCCEEDED',request_payload={'payload':{
        'radar_inputs':[dict(scan_id='scan',radar_id='site',qc_uri='consumed-old-qc')]}})
    return web,receipt


def test_bind_to_rendered_input_instead_of_latest_scan_qc():
    web,receipt=fixture()
    assert m.published_input(web,receipt)=='consumed-old-qc'


def test_mismatched_raw_qc_generation_is_rejected():
    web,receipt=fixture();web['raw_frame']={'image_url':'/api/v1/diagnostics/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb/layers/raw'}
    with pytest.raises(ValueError,match='generation'):m.published_input(web,receipt)


def test_duplicate_or_wrong_scan_and_incomplete_generation_are_rejected():
    web,receipt=fixture();inputs=receipt['request_payload']['payload']['radar_inputs']
    inputs.append(inputs[0].copy())
    with pytest.raises(ValueError,match='unique'):m.published_input(web,receipt)
    inputs.pop();inputs[0]['scan_id']='different'
    with pytest.raises(ValueError,match='unique'):m.published_input(web,receipt)
    receipt['status']='RUNNING'
    with pytest.raises(ValueError,match='incomplete'):m.published_input(web,receipt)


def test_evidence_selection_requires_exact_snapshot_and_binary_shape(tmp_path):
    import json,hashlib
    import numpy as np
    snapshot=tmp_path/'snapshot.npz';snapshot.write_bytes(b'original-snapshot')
    proof={'input_snapshot_sha256':hashlib.sha256(snapshot.read_bytes()).hexdigest()}
    evidence=tmp_path/'evidence.npz'
    def save(mask):np.savez(evidence,METADATA=np.array(json.dumps(proof)),RV2_BACKBONE_STRONG_MASK=mask)
    save(np.array([[0,1]],dtype='uint8'))
    mask,receipt=m.evidence_selection(evidence,snapshot,(1,2))
    assert mask.tolist()==[[False,True]] and receipt['diagnostic_selection_only']
    snapshot.write_bytes(b'different-snapshot')
    with pytest.raises(ValueError,match='snapshot mismatch'):m.evidence_selection(evidence,snapshot,(1,2))
    snapshot.write_bytes(b'original-snapshot');save(np.array([[0,2]],dtype='uint8'))
    with pytest.raises(ValueError,match='invalid'):m.evidence_selection(evidence,snapshot,(1,2))
