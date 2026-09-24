# ruff: noqa: E501, E701, E702, I001, E402
import copy
import json
from pathlib import Path
import sys

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from configure_multiband import MIB, multiband_override


def write_network(tmp_path):
    value=json.loads((ROOT/'configs/multiband/network.example.json').read_text())
    for s in value['stations'].values():
        s['enabled']=True;s['geometry_verified']=True
    path=tmp_path/'network.json';path.write_text(json.dumps(value));return path

def effective():
    return {'services':{'radar-qc-worker':{'image':'qc:current','environment':{
        'RAINPULSE_QC_FLAG_DEFINITIONS':'/opt/flags.yaml','RAINPULSE_RADAR_QC_CONFIG':'/opt/current-qc.yaml',
        'RAINPULSE_OBJECT_STORE_ENDPOINT':'http://minio:9000'},
        'volumes':[{'type':'bind','source':'/actual/configs','target':'/opt','read_only':True}]}}}


def test_dedicated_kind_reuses_flags_but_not_s_thresholds(tmp_path):
    model=effective();before=copy.deepcopy(model)
    out=multiband_override(model,write_network(tmp_path),'rainpulse:multiband-1',1,2*1024*MIB,2,3*1024*MIB)
    worker=out['services']['ops-multiband-worker']
    assert worker['entrypoint'][-1]=='multiband'
    assert 'RAINPULSE_RADAR_QC_CONFIG' not in worker['environment']
    assert worker['environment']['RAINPULSE_QC_FLAG_DEFINITIONS']=='/opt/flags.yaml'
    assert worker['volumes'][-1]['read_only'] is True
    assert worker['mem_limit']==worker['memswap_limit']==2*1024*MIB
    assert model==before


def test_refuses_unverified_example_and_oversubscription(tmp_path):
    with pytest.raises(ValueError): multiband_override(effective(),ROOT/'configs/multiband/network.example.json','worker:v1',1,2*1024*MIB,2,3*1024*MIB)
    network=write_network(tmp_path)
    with pytest.raises(ValueError): multiband_override(effective(),network,'worker:v1',4,2*1024*MIB,1,3*1024*MIB)
    with pytest.raises(ValueError): multiband_override(effective(),network,'worker:latest',1,2*1024*MIB,2,3*1024*MIB)
    with pytest.raises(ValueError): multiband_override(effective(),network,'worker:v1',1,256*MIB,2,3*1024*MIB)
