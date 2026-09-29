# ruff: noqa: E501, I001
import io
import json
from copy import deepcopy
import numpy as np
import pytest
from rainpulse_algo.multiband.quality import x_qc
from rainpulse_algo.multiband.xqc_v2.export import export_sweep, encode_arrays
from rainpulse_algo.multiband.attenuation import copy_path_provenance, PATH_PROVENANCE_KEYS
from .helpers import station, volume, SHA

def test_path_export_without_optional_nonmet_classifier_is_explicit():
    s = station()
    v = x_qc(volume(s), s, SHA)
    objects = {}
    report = export_sweep(v.sweeps[0], v.metadata, objects)
    assert report['mode'] == 'path_quality_only' and len(objects) == 2
    detail = json.loads(objects[report['evidence_path']])
    assert detail['path_quality']['s_radar_input_required'] is False
    assert detail['path_parameters']['alpha_db_per_degree'] == 0.2
    with np.load(io.BytesIO(objects[report['native']['object_path']]), allow_pickle=False) as numeric:
        for key in ('PATH_REASON', 'PATH_STATE', 'PATH_VALID_MASK', 'PIA_DB', 'AH_DB_PER_KM', 'RADOME_QUALIFIED_MASK', 'DBZH_ATTENUATION_CORRECTED'):
            np.testing.assert_array_equal(numeric[key], v.sweeps[0].fields[key])

def test_existing_evidence_honors_export_native_disabled():
    s = station()
    v = x_qc(volume(s), s, SHA)
    cut = v.sweeps[0]
    cut.xqc_diagnostics = {'version': 'test-existing-xqc', 'mode': 'audit', 'status': 'EVALUATED', 'parameter_sha256': 'e' * 64, 'rejected_gates': 0, 'withheld_gates': 0, 'export_native': False}
    before = deepcopy(cut.xqc_diagnostics)
    objects = {}
    report = export_sweep(cut, v.metadata, objects)
    assert report['mode'] == 'audit' and report['native'] is None and (len(objects) == 1)
    assert cut.xqc_diagnostics == before

def test_numeric_export_budget_cannot_be_bypassed():
    with pytest.raises(ValueError, match='decoded budget'):
        encode_arrays({'large': np.zeros((20, 20), 'float32')}, maximum_decoded_bytes=16)

def test_adapter_provenance_copies_only_present_declarations():
    target = {}
    copy_path_provenance({'radome_status': 'unknown', 'S_reference': 'not-permitted'}, target)
    assert target == {'radome_status': 'unknown'}
    assert 'radome_evidence_sha256' not in target
    copy_path_provenance({'radome_evidence_sha256': 'e' * 64}, target)
    assert target['radome_evidence_sha256'] == 'e' * 64
