"""Read a frozen qc-case normalized volume; no inference from PNG/QC labels.

Default backend is zarr. --portable-reader supports only these explicit Zarr-v2
unfiltered, C-order, raw/Blosc arrays using blosc2; it rejects other encodings.
It is an offline reader, never a replacement for the production decoder.
"""
from pathlib import Path
import hashlib
import itertools
import json
import numpy as np
from volume_review.data import Sweep


def read_array(path, portable=False):
    path=Path(path)
    if not portable:
        import zarr
        return np.asarray(zarr.open_array(str(path),mode='r')[:])
    meta=json.loads((path/'.zarray').read_text())
    if meta['zarr_format']!=2 or meta['order']!='C' or meta.get('filters'):
        raise ValueError('unsupported portable Zarr encoding')
    dtype=np.dtype(meta['dtype']); shape=tuple(meta['shape']); chunks=tuple(meta['chunks'])
    if (dtype.hasobject or len(shape)!=len(chunks) or len(shape) not in (1,2) or
            any(x<=0 for x in shape+chunks) or int(np.prod(shape))>10000000 or
            meta.get('dimension_separator','.') not in ('.','/')):
        raise ValueError('unsupported array geometry/dtype')
    fill=meta.get('fill_value',0)
    if fill=='NaN':fill=np.nan
    result=np.full(shape,fill,dtype=dtype)
    for idx in itertools.product(*[range((s+c-1)//c) for s,c in zip(shape,chunks)]):
        file=path/meta.get('dimension_separator','.').join(map(str,idx))
        if not file.exists():continue
        data=file.read_bytes(); compressor=meta.get('compressor')
        if compressor:
            if compressor.get('id')!='blosc':raise ValueError('portable reader supports Blosc only')
            import blosc2
            data=blosc2.decompress(data)
        a=np.frombuffer(data,dtype=dtype)
        if a.size!=int(np.prod(chunks)):raise ValueError('chunk byte shape mismatch')
        a=a.reshape(chunks)
        slices=tuple(slice(i*c,min((i+1)*c,s)) for i,c,s in zip(idx,chunks,shape))
        take=tuple(slice(0,x.stop-x.start) for x in slices)
        result[slices]=a[take]
    return result


def load_sweep(path, portable=False):
    p=Path(path); read=lambda k:read_array(p/k,portable)
    az=read('azimuth');r=read('range');el=read('elevation');times=read('ray_time')
    shape=(len(az),len(r));fields={};available={}
    for key in ['DBZH','SNR','RHOHV','ZDR','PHIDP']:
        if not (p/key/'.zarray').exists():continue
        value=read(key); valid=np.isfinite(value)
        codes=p/(key+'_RAW_CODE')
        if codes.is_dir():
            attrs=json.loads((codes/'.zattrs').read_text());code=read(key+'_RAW_CODE')
            forbidden=attrs.get('reserved_codes',[])+[attrs['absent_moment_code']]
            valid &= ~np.isin(code,forbidden)
        if key=='DBZH':valid &= (value>=-32)&(value<=80)
        if key=='RHOHV':valid &= (value>=0)&(value<=1)
        fields[key]=value;available[key]=valid
    if 'DBZH' not in fields:
        fields['DBZH']=np.full(shape,np.nan,'float32');available['DBZH']=np.zeros(shape,bool)
    order=np.argsort(az%360,kind='stable');az=az[order]%360
    gap=(np.roll(az,-1)-az)%360;pos=gap[gap>.01];spacing=float(np.median(pos)) if len(pos) else 360.
    duplicate=gap<=.01;good=~(duplicate|np.roll(duplicate,1));gaps=gap>1.8*spacing
    if times.dtype.kind=='M': times=times.astype('datetime64[ns]').astype('int64')/1e9
    sweep=Sweep(p.name,az,el[order],r,{k:v[order] for k,v in fields.items()},
        {k:v[order] for k,v in available.items()},good,gaps,times[order])
    return sweep,order


def path_under(root, relative):
    root=Path(root).resolve();p=(root/relative).resolve()
    if not p.is_relative_to(root):raise ValueError('case path escapes root')
    return p


def content_digest(path):
    # Same length-delimited path/content identity used by RainPulse artifacts.
    h=hashlib.sha256()
    for p in sorted(Path(path).rglob('*')):
        if not p.is_file():continue
        key=p.relative_to(path).as_posix().encode();data=p.read_bytes()
        h.update(len(key).to_bytes(4,'big'));h.update(key)
        h.update(len(data).to_bytes(8,'big'));h.update(hashlib.sha256(data).digest())
    return h.hexdigest()
