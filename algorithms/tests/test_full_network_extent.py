import json
from pathlib import Path
import numpy as np
import pytest
from pyproj import Geod, Transformer
from rainpulse_algo.multiband.model import Grid

CONFIG = Path(__file__).resolve().parents[2] / 'configs/multiband/fujian-full-experimental-20260828.json'

def test_full_product_contains_native_s_range_in_every_direction():
    config=json.loads(CONFIG.read_text());grid=config['products']['sx-fujian-full-test']
    project=Transformer.from_crs(4326,grid['crs'],always_xy=True);geod=Geod(ellps='WGS84')
    bearings=np.arange(0,360,.25)
    for id,s in config['stations'].items():
        if s['band']!='S':continue
        lon,lat,_=geod.fwd(np.full(bearings.shape,s['longitude_deg']),np.full(bearings.shape,s['latitude_deg']),bearings,np.full(bearings.shape,460000.))
        x,y=project.transform(lon,lat)
        assert min(x)>=grid['west_m'],id
        assert max(x)<=grid['west_m']+grid['width']*grid['spacing_m'],id
        assert min(y)>=grid['south_m'],id
        assert max(y)<=grid['south_m']+grid['height']*grid['spacing_m'],id
    Grid(**{k:v for k,v in grid.items() if k!='unused'})

def test_larger_horizontal_domain_keeps_height_fusion_budget():
    args=dict(grid_id='full',crs='EPSG:32651',west_m=-553000,south_m=2417000,spacing_m=1000,width=1238,height=1077,levels_m_msl=(0,))
    Grid(**args,method='experimental_horizontal_max')
    with pytest.raises(ValueError):Grid(**args)
    with pytest.raises(ValueError):Grid(**{**args,'width':2048,'height':2048},method='experimental_horizontal_max')
