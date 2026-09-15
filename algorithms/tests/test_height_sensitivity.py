from dataclasses import replace
import numpy as np
from .test_radar_qc_geometry import FlatTerrain, _reference_root
from rainpulse_algo.radar.qc_geometry import RadarBeamContext, CrossRadarSupportReference, build_trusted_cross_radar_support


def test_experiment_preserves_unverified_datum_and_default_remains_blocked():
    beam = RadarBeamContext('r',117.,27.,100.,1.,'incompatible_with_epsg_3855')
    az=np.array([0.,180.]); ranges=np.array([5000.,10000.,15000.,20000.])
    root=_reference_root(np.full((2,4),20.),azimuth_deg=az,range_m=ranges,elevation_deg=.5,radar_id='other')
    ref=CrossRadarSupportReference('other',root,replace(beam,radar_id='other'),True,True,{})
    current={'dbzh':np.full((2,4),20.),'azimuth':az,'range':ranges,'elevation':np.array([.5,.5])}
    for enabled in (False,True):
        d=build_trusted_cross_radar_support(current,beam,(ref,),terrain=FlatTerrain(),echo_threshold_dbzh=10.,minimum_overlap_gates=1,experimental_datum_assumption=enabled)
        assert bool(d.available_mask.any()) == enabled
        assert d.metrics['verified_current_vertical_datum'] == 0
        assert d.availability_audit['experimental_datum_assumption'] == enabled
        if enabled:
            assert not d.availability_audit['operational_eligible']
    assert beam.altitude_datum_status == 'incompatible_with_epsg_3855'
