# ruff: noqa: E501, I001
"""Real Zarr adapter integration; never replace the SDK with a numeric mock."""
import pytest
zarr = pytest.importorskip('zarr', reason='real Zarr 2 SDK required for adapter roundtrip')
import numpy as np
from zarr.storage import MemoryStore
from rainpulse_algo.multiband.adapters import from_group, read_x_qc_sweep
from rainpulse_algo.multiband.attenuation import PATH_INPUT_FIELDS, PATH_PROVENANCE_KEYS
from rainpulse_algo.multiband.quality import x_qc
from .helpers import volume, station, SHA

def fixture():
    s = station(source='normalized_zarr')
    v = volume(s)
    store = MemoryStore()
    root = zarr.group(store=store)
    attrs = {**v.metadata, 'contract_name': 'rainpulse.normalized-radar-volume', 'radar_band': 'X', 'site_longitude_deg': s.longitude_deg, 'site_latitude_deg': s.latitude_deg, 'volume_start_time_utc': v.metadata['volume_start'], 'volume_end_time_utc': v.metadata['volume_end']}
    attrs.update(clear_path_evidence_sha256='c' * 64, path_anchor_evidence_sha256='d' * 64, negligible_attenuation_evidence_sha256='e' * 64)
    root.attrs.update(attrs)
    root.create_dataset('sweep_number', data=np.array([0], 'int32'))
    g = root.create_group('sweep_000')
    cut = v.sweeps[0]
    for key, values in cut.fields.items():
        g.create_dataset(key, data=values)
    for key in PATH_INPUT_FIELDS:
        if key not in g:
            g.create_dataset(key, data=np.zeros(cut.fields['DBZH'].shape, 'float32' if key.endswith('DB') else 'uint8'))
    for key, values in [('azimuth', cut.azimuth_deg), ('range', cut.range_m), ('elevation', cut.elevation_deg), ('ray_time', cut.ray_time_epoch)]:
        g.create_dataset(key, data=values)
    source = {k: v.metadata[k] for k in ('scan_id', 'volume_start', 'volume_end', 'available_at')}
    return (s, v, root, store, source)

@pytest.mark.parametrize('streaming', [False, True])
def test_actual_sdk_roundtrip_preserves_path_inputs(streaming):
    s, v, root, store, source = fixture()
    if streaming:
        got, _ = read_x_qc_sweep(dict(store), s, source, 0, asset_sha256=SHA, maximum_bytes=10 ** 7)
    else:
        got = from_group(root, s, source, asset_sha256=SHA, maximum_bytes=10 ** 7)
    for key in PATH_INPUT_FIELDS:
        assert key in got.sweeps[0].fields
    for key in PATH_PROVENANCE_KEYS:
        assert got.metadata[key] == root.attrs[key]
    out = x_qc(got, s, SHA)
    assert out.sweeps[0].fields['PATH_VALID_MASK'].any()
    np.testing.assert_array_equal(got.sweeps[0].fields['DBZH'], v.sweeps[0].fields['DBZH'])


@pytest.mark.parametrize('streaming', [False,True])
def test_native_sampling_roundtrip_retains_cut_identity_without_promotion(streaming):
    s,v,root,store,source=fixture()
    root.attrs.update(input_sha256='a'*64,radar_config_version='native-test')
    sampling={'version':'native-cut-sampling-v1','input_sha256':'a'*64,'radar_config_version':'native-test','source_sweep_number':10,'process_mode_code':1,'waveform_code':8,'prf1_hz':1200.,'prf2_hz':800.,'log_resolution_m':75.,'doppler_resolution_m':75.,'nyquist_velocity_m_s':19.3,'semantic_verification':False}
    root['sweep_000'].attrs.update(source_sweep_number=10,native_cut_sampling=sampling)
    if streaming:
        got,_=read_x_qc_sweep(dict(store),s,source,0,asset_sha256=SHA,maximum_bytes=10**7)
    else:
        got=from_group(root,s,source,asset_sha256=SHA,maximum_bytes=10**7)
    assert got.metadata['native_cut_sampling']=={'0':sampling}
    assert got.metadata.get('doppler_verification_id') is None
    assert got.metadata.get('doppler_waveform') is None
    np.testing.assert_array_equal(got.sweeps[0].fields['DBZH'],v.sweeps[0].fields['DBZH'])


@pytest.mark.parametrize('mismatch', ['input_sha256','radar_config_version','source_sweep_number'])
def test_native_sampling_rejects_mismatched_identity(mismatch):
    s,v,root,store,source=fixture()
    root.attrs.update(input_sha256='a'*64,radar_config_version='native-test')
    sampling={'version':'native-cut-sampling-v1','input_sha256':'a'*64,'radar_config_version':'native-test','source_sweep_number':10,'process_mode_code':1,'waveform_code':8,'prf1_hz':1200.,'prf2_hz':800.,'log_resolution_m':75.,'doppler_resolution_m':75.,'nyquist_velocity_m_s':19.3,'semantic_verification':False}
    sampling[mismatch]=11 if mismatch=='source_sweep_number' else 'wrong'
    root['sweep_000'].attrs.update(source_sweep_number=10,native_cut_sampling=sampling)
    with pytest.raises(ValueError,match='native cut sampling'):
        read_x_qc_sweep(dict(store),s,source,0,asset_sha256=SHA,maximum_bytes=10**7)


@pytest.mark.parametrize('streaming', [False,True])
def test_legacy_asset_without_sampling_does_not_invent_per_cut_metadata(streaming):
    s,v,root,store,source=fixture()
    if streaming:
        got,_=read_x_qc_sweep(dict(store),s,source,0,asset_sha256=SHA,maximum_bytes=10**7)
    else:
        got=from_group(root,s,source,asset_sha256=SHA,maximum_bytes=10**7)
    assert 'native_cut_sampling' not in got.metadata


@pytest.mark.parametrize('invalid', ['boolean_waveform','nonfinite_prf','promoted','different_nyquist'])
def test_native_sampling_rejects_invalid_measurements_and_promotion(invalid):
    s,v,root,store,source=fixture()
    root.attrs.update(input_sha256='a'*64,radar_config_version='native-test')
    sampling={'version':'native-cut-sampling-v1','input_sha256':'a'*64,'radar_config_version':'native-test','source_sweep_number':10,'process_mode_code':1,'waveform_code':8,'prf1_hz':1200.,'prf2_hz':800.,'log_resolution_m':75.,'doppler_resolution_m':75.,'nyquist_velocity_m_s':19.3,'semantic_verification':False}
    if invalid=='boolean_waveform':sampling['waveform_code']=True
    elif invalid=='nonfinite_prf':sampling['prf1_hz']=float('nan')
    elif invalid=='promoted':sampling['semantic_verification']=True
    else:root['sweep_000'].attrs['nyquist_velocity_m_s']=10.
    root['sweep_000'].attrs.update(source_sweep_number=10,native_cut_sampling=sampling)
    with pytest.raises(ValueError,match='native cut sampling'):
        read_x_qc_sweep(dict(store),s,source,0,asset_sha256=SHA,maximum_bytes=10**7)


@pytest.mark.parametrize('streaming', [False, True])
@pytest.mark.parametrize('field,value', [('dealiasing_mode_code', True), ('sample_count1', 42.5),
                                        ('sample_count2', 'missing'), ('phase_mode_code', None),
                                        ('atmospheric_loss_db_per_km', float('inf'))])
def test_native_processing_extension_rejects_malformed_present_values(streaming, field, value):
    s,v,root,store,source=fixture()
    root.attrs.update(input_sha256='a'*64,radar_config_version='native-test')
    sampling={'version':'native-cut-sampling-v1','input_sha256':'a'*64,'radar_config_version':'native-test','source_sweep_number':10,'process_mode_code':1,'waveform_code':8,'prf1_hz':1200.,'prf2_hz':800.,'log_resolution_m':75.,'doppler_resolution_m':75.,'nyquist_velocity_m_s':19.3,'semantic_verification':False,field:value}
    root['sweep_000'].attrs.update(source_sweep_number=10,native_cut_sampling=sampling)
    with pytest.raises(ValueError, match='native cut sampling'):
        if streaming: read_x_qc_sweep(dict(store),s,source,0,asset_sha256=SHA,maximum_bytes=10**7)
        else: from_group(root,s,source,asset_sha256=SHA,maximum_bytes=10**7)


@pytest.mark.parametrize('streaming', [False, True])
def test_native_processing_extension_preserves_unknown_codes_and_sentinels(streaming):
    s,v,root,store,source=fixture()
    root.attrs.update(input_sha256='a'*64,radar_config_version='native-test')
    sampling={'version':'native-cut-sampling-v1','input_sha256':'a'*64,'radar_config_version':'native-test','source_sweep_number':10,'process_mode_code':1,'waveform_code':8,'prf1_hz':1200.,'prf2_hz':800.,'log_resolution_m':75.,'doppler_resolution_m':75.,'nyquist_velocity_m_s':19.3,'semantic_verification':False,'dealiasing_mode_code':2,'sample_count1':42,'sample_count2':-2147483648,'phase_mode_code':0,'atmospheric_loss_db_per_km':.025}
    root['sweep_000'].attrs.update(source_sweep_number=10,native_cut_sampling=sampling)
    if streaming: got,_=read_x_qc_sweep(dict(store),s,source,0,asset_sha256=SHA,maximum_bytes=10**7)
    else: got=from_group(root,s,source,asset_sha256=SHA,maximum_bytes=10**7)
    assert got.metadata['native_cut_sampling']['0']==sampling
    assert got.metadata.get('doppler_verification_id') is None
    np.testing.assert_array_equal(got.sweeps[0].fields['DBZH'],v.sweeps[0].fields['DBZH'])
