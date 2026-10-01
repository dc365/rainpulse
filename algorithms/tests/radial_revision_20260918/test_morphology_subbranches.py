"""Original-cell geometry distinguishes radial lines from tangential fragments."""
from pathlib import Path
import importlib.util
import numpy as np
import pytest

p=Path(__file__).resolve().parents[3]/'scripts/audit_s_morphology_subbranches.py'
spec=importlib.util.spec_from_file_location('subbranch_audit',p)
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def make():
    r=np.arange(50000.,250000.,500.);az=np.arange(41.,dtype=float)+330
    z=np.full((len(az),len(r)),np.nan,'float32');good=np.ones(len(az),bool)
    gap=np.zeros(len(az),bool);gap[-1]=True
    return z,az,r,good,gap


def test_radial_line_uses_gate_footprint_and_is_rotation_equivariant():
    z,az,r,good,gap=make();z[20,20:350]=20
    a=m.components(z,np.isfinite(z),az,r,good,gap,levels=(10.,))[0]
    b=m.components(z,np.isfinite(z),(az+123)%360,r,good,gap,levels=(10.,))[0]
    assert np.isfinite(a['physical_pca_aspect']) and a['physical_pca_aspect']>20
    assert a['radial_alignment_error_deg']<1
    assert b['physical_pca_aspect']==pytest.approx(a['physical_pca_aspect'])
    assert b['radial_alignment_error_deg']==pytest.approx(a['radial_alignment_error_deg'],abs=1e-5)


def test_far_tangential_blob_is_not_a_radial_streak():
    z,az,r,good,gap=make();z[10:30,280:284]=20
    a=m.components(z,np.isfinite(z),az,r,good,gap,levels=(10.,))[0]
    assert a['radial_alignment_error_deg']>80
    assert a['range_span_m']==2000


def test_native_gap_splits_component_and_missing_does_not_connect():
    z,az,r,good,gap=make();z[18:23,280:284]=20;gap[20]=True
    a=m.components(z,np.isfinite(z),az,r,good,gap,levels=(10.,))
    assert len(a)==2
    assert all(not (18 in c['ray_indices'] and 22 in c['ray_indices']) for c in a)
    gap[20]=False;z[20]=np.nan
    a=m.components(z,np.isfinite(z),az,r,good,gap,levels=(10.,))
    assert len(a)==2


def test_component_domain_not_cropped_to_selected_residual():
    z,az,r,good,gap=make();z[18:23,20:350]=20
    a=m.components(z,np.isfinite(z),az,r,good,gap,levels=(10.,))[0]
    assert a['gates']==5*330 and a['range_span_m']==165000


def test_large_connected_weather_domain_cannot_nominate_from_pca_alone():
    r=np.arange(2125.,460000.,500.)
    az=np.arange(0.,180.,5.)
    z=np.full((len(az),len(r)),np.nan)
    # A long branch dominates covariance, but is attached to a broad domain.
    z[3:33,:3]=20.
    z[18,:]=20.
    good=np.ones(len(az),bool);gap=np.zeros(len(az),bool);gap[-1]=True
    item=m.components(z,np.isfinite(z),az,r,good,gap,levels=(10.,))[0]
    assert item['physical_pca_aspect']>6
    assert item['radial_alignment_error_deg']<10
    assert item['angular_width_deg']>90
    assert not m.radial_geometry(item)
