import subprocess
import sys
from pathlib import Path
import numpy as np
from rainpulse_algo.radar.qc_engine.adapters import NativeSweep
from rainpulse_algo.radar.qc_engine.measurement_v8.native import run_native
from rainpulse_algo.radar.qc_engine.measurement_v8.schema import NativeConfig
from rainpulse_algo.radar.qc_engine.measurement_v8.io import file_hash


def test_contrast_is_background_invariant_and_detects_known_line(tmp_path):
    root=Path(__file__).resolve().parents[2]
    binary=tmp_path/'core'
    subprocess.run([sys.executable,str(root/'tools/radar_native/build.py'),'--output',str(binary)],check=True,capture_output=True)
    sha=file_hash(binary);results=[]
    for background in (0.,20.):
        z=np.full((360,256),background);z[180,:]+=10
        n=NativeSweep('sweep_000',np.arange(360.),np.ones(360),np.arange(256)*250.,np.arange(360),
            {'DBZH':z},{'DBZH':np.ones(z.shape,bool)},np.arange(360),True,
            np.ones(360,bool),np.zeros(360,bool),{}, {'azimuth_spacing_deg':1.})
        cfg=NativeConfig(emitter1_minimum_contrast_db=4.)
        fixed=run_native(n,cfg,binary,sha)
        legacy=run_native(n,cfg.model_copy(update={'emitter1_minimum_contrast_db':None}),binary,sha)
        assert np.nanmax(fixed.scores['1'][180])>.5
        assert np.nanmax(legacy.scores['1'][180])<.1
        np.testing.assert_equal(fixed.scores['2'],legacy.scores['2'])
        results.append(fixed.scores['1'])
    np.testing.assert_equal(*results)
