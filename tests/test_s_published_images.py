"""An image pair must belong to the exact audited station/cut/generation."""
import copy
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('published_images',ROOT/'scripts/fetch_s_published_images.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def receipt():
    job='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
    web=dict(site='z9591',sweep=2,web_scan_id='original')
    for side in ('raw','qc'):
        asset=f'radar-z9591-dbzh-{side}-sweep-002'
        web[side+'_frame']=dict(scan_id='original',sweep_number=2,asset_id=asset,
            image_url=f'/api/v1/diagnostics/{job}/layers/{asset}')
    return dict(scope='exact_Web_consumed_stored_QC_not_replay',diagnostic_job_id=job,web_frame_identity=web)


def test_exact_pair_is_bound():
    data=receipt()
    assert m.bound_urls(data)=={side:data['web_frame_identity'][side+'_frame']['image_url'] for side in ('raw','qc')}


@pytest.mark.parametrize('field,value',[
    ('scan_id','different'),('sweep_number',3),('asset_id','radar-z9598-dbzh-qc-sweep-002'),
    ('image_url','/api/v1/diagnostics/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/layers/radar-z9598-dbzh-qc-sweep-002'),
    ('image_url','/api/v1/diagnostics/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/layers/radar-z9591-dbzh-qc-sweep-003'),
    ('image_url','https://elsewhere.invalid/qc.png')])
def test_wrong_station_cut_scan_or_path_rejected(field,value):
    data=copy.deepcopy(receipt());data['web_frame_identity']['qc_frame'][field]=value
    with pytest.raises(ValueError,match='match the audited'):m.bound_urls(data)
