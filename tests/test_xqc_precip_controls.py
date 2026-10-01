import base64
import importlib.util
import io
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location('precip_controls', Path(__file__).parents[1] / 'scripts/inspect_xqc_precip_controls.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def packed(text):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('0', text)
    return base64.b64encode(stream.getvalue())


def test_surface_values_preserve_zero_and_missing_without_truth_promotion():
    assert module.decode(packed('A,0,,0.1\nB,1,2,3\n')) == {'A':[0.,None,.1], 'B':[1.,2.,3.]}


@pytest.mark.parametrize('text', ['A,0\nA,1\n', 'A,nan\n', 'A,0\nB,0,1\n'])
def test_ambiguous_surface_data_rejected(text):
    with pytest.raises(ValueError):
        module.decode(packed(text))


def test_incomplete_zip_is_not_accepted_as_a_station_source():
    raw = base64.b64decode(packed('A,0\n'))
    with pytest.raises(zipfile.BadZipFile):
        module.decode(base64.b64encode(raw[:-22]))


def test_missing_hour_remains_unverified(tmp_path):
    from datetime import datetime
    result = module.inspect(tmp_path, datetime(2026,8,28))
    assert result['arithmetic_probe']['complete_minute_files'] == 0
    assert not result['may_be_used_as_weather_truth']
    assert not result['fujian_station_dictionary_present']
