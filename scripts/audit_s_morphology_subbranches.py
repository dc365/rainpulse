#!/usr/bin/env python3
"""Measure original polar components and radial alignment; never propose actions."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
from scipy import ndimage
sys.path.insert(0,str(Path(__file__).resolve().parent))
from audit_s_source_footprint import select_roi


def radial_geometry(item):
    """Nominate a bounded component, never authorize QC from global PCA alone."""
    return (item['angular_width_deg'] <= 90.
            and item['physical_pca_aspect'] >= 6.
            and item['radial_alignment_error_deg'] <= 10.
            and item['range_span_m'] >= 20000.)


def components(raw, available, azimuth, ranges, good, gaps, levels=(0.,10.,20.,35.)):
    """Native connectivity breaks at true scan gaps; no interpolation or linkage."""
    z=np.asarray(raw); available=np.asarray(available,dtype=bool)
    az=np.asarray(azimuth,dtype=float); r=np.asarray(ranges,dtype=float)
    good=np.asarray(good,dtype=bool); gaps=np.asarray(gaps,dtype=bool)
    if z.shape!=(len(az),len(r)) or available.shape!=z.shape or good.shape!=az.shape or gaps.shape!=az.shape:
        raise ValueError('native geometry/availability shape mismatch')
    dr=float(np.median(np.diff(r)))
    if not np.isfinite(r).all() or dr<=0 or not np.allclose(np.diff(r),dr):
        raise ValueError('uniform positive original range geometry required')
    if not np.isfinite(az).all():raise ValueError('invalid original azimuth')
    result=[]
    for rows in np.split(np.arange(len(az)),np.flatnonzero(gaps[:-1])+1):
        if not len(rows):continue
        angles=np.rad2deg(np.unwrap(np.deg2rad(az[rows])))
        spacing=float(np.median(np.diff(angles))) if len(rows)>1 else None
        if spacing is None or spacing<=0 or np.any(np.diff(angles)<=0):continue
        for level in levels:
            signal=available[rows]&np.isfinite(z[rows])&good[rows,None]&(z[rows]>=level)
            labels,count=ndimage.label(signal,structure=np.ones((3,3),dtype='uint8'))
            for identity,box in enumerate(ndimage.find_objects(labels),1):
                if box is None:continue
                rr,cc=np.where(labels[box]==identity)
                rr=rr+box[0].start;cc=cc+box[1].start
                if len(rr)<3:continue
                ray=rows[rr]; theta=np.deg2rad(az[ray]); distance=r[cc]
                x=distance*np.sin(theta); y=distance*np.cos(theta)
                points=np.column_stack((x,y)); centre=points.mean(axis=0)
                delta=points-centre
                # Native gate footprint prevents one-ray samples from having
                # an infinite physical aspect ratio solely from quantization.
                radial=np.column_stack((np.sin(theta),np.cos(theta)))
                tangent=np.column_stack((np.cos(theta),-np.sin(theta)))
                angular_width=distance*np.deg2rad(spacing)
                covariance=delta.T@delta/len(delta)
                covariance+=np.einsum('ni,nj->ij',radial,radial)*dr**2/(12*len(delta))
                covariance+=np.einsum('ni,nj,n->ij',tangent,tangent,angular_width**2)/(12*len(delta))
                eigenvalues,eigenvectors=np.linalg.eigh(covariance)
                radial_direction=centre/np.linalg.norm(centre) if np.linalg.norm(centre)>0 else np.array([np.nan,np.nan])
                alignment=float(np.degrees(np.arccos(np.clip(abs(eigenvectors[:,-1]@radial_direction),0,1))))
                result.append(dict(level_dbz=float(level),ray_indices=ray,gate_indices=cc,
                    gates=len(ray),range_start_m=float(distance.min()),range_end_m=float(distance.max()+dr),
                    range_span_m=float(np.ptp(distance)+dr),range_support_m=float(len(np.unique(cc))*dr),
                    angular_width_deg=float(angles[rr].max()-angles[rr].min()+spacing),
                    physical_pca_aspect=float(np.sqrt(eigenvalues[-1]/max(eigenvalues[0],1e-12))),
                    radial_alignment_error_deg=alignment))
    return result


def audit(path, *, azimuth=None, range_min=0.):
    with np.load(path,allow_pickle=False) as data:a={k:data[k] for k in data.files}
    meta=json.loads(str(a['METADATA']))
    if not meta.get('web_frame_identity'):raise ValueError('requires complete Web-paired original snapshot')
    roi=select_roi(a['AZIMUTH'],a['RANGE'],range_min=range_min,
                   azimuth_start=azimuth[0] if azimuth else None,azimuth_end=azimuth[1] if azimuth else None)
    remaining=a['BEFORE'].astype(bool)&~a['ADDED'].astype(bool)&roi
    rows=[];cover=np.zeros(a['RAW'].shape,bool);thin=np.zeros_like(cover)
    for item in components(a['RAW'],a['AVAILABLE_DBZH'],a['AZIMUTH'],a['RANGE'],a['GEOMETRY_GOOD'],a['GAP_AFTER']):
        rr=item.pop('ray_indices');cc=item.pop('gate_indices');selected=remaining[rr,cc]
        if not selected.any():continue
        cover[rr[selected],cc[selected]]=True
        nominated=radial_geometry(item)
        if nominated:thin[rr[selected],cc[selected]]=True
        item['remaining_selected_gates']=int(selected.sum());item['diagnostic_radial_geometry']=bool(nominated)
        for name in ('SNR','RHOHV'):
            key='MOMENT_'+name;valid_key='AVAILABLE_'+name
            use=selected&a.get(valid_key,np.zeros_like(remaining))[rr,cc].astype(bool)
            item[name.lower()+'_valid_selected_gates']=int(use.sum())
            item[name.lower()+'_median_selected']=float(np.median(a[key][rr[use],cc[use]])) if use.any() and key in a else None
        rows.append(item)
    return dict(scope='original_component_subbranch_geometry_not_QC',action_gates=0,product_writes=False,
        independent_weather_truth=False,snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        scan_id=meta['scan_id'],sweep=meta['sweep'],selection={'azimuth':azimuth,'range_min':range_min},
        remaining_selected_gates=int(remaining.sum()),covered_selected_gates=int(cover.sum()),
        diagnostic_radial_geometry_selected_gates=int(thin.sum()),components=rows)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('snapshot',type=Path)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--azimuth',type=float,nargs=2)
    p.add_argument('--range-min',type=float,default=0.);a=p.parse_args()
    report=audit(a.snapshot,azimuth=a.azimuth,range_min=a.range_min)
    report['script_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with a.output.open('x') as stream:json.dump(report,stream,indent=2,allow_nan=False);stream.write('\n')
    print(json.dumps({k:v for k,v in report.items() if k!='components'}))


if __name__=='__main__':main()
