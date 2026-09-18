"""Read-only minimal numeric Zarr-v2 reader for this frozen package (Blosc/no filters)."""
from pathlib import Path
import json, itertools, os, numpy as np, blosc2
class Group:
    def __init__(self,p): self.path=Path(p); self.attrs=json.loads((self.path/'.zattrs').read_text()) if (self.path/'.zattrs').exists() else {}
    def keys(self): return sorted(p.name for p in self.path.iterdir() if p.is_dir())
    def __getitem__(self,k):
        p=self.path/k
        return Array(p) if (p/'.zarray').exists() else Group(p)
    def __contains__(self,k): return (self.path/k).is_dir()
class Array:
    def __init__(self,p):
        self.path=p; self.meta=json.loads((p/'.zarray').read_text());self.shape=tuple(self.meta['shape']);self.dtype=np.dtype(self.meta['dtype']); self.attrs=json.loads((p/'.zattrs').read_text()) if (p/'.zattrs').exists() else {}
    def read(self):
        m=self.meta
        if m['zarr_format']!=2 or m.get('filters'): raise ValueError('unsupported Zarr metadata')
        if self.dtype.hasobject: raise ValueError('object dtype prohibited')
        chunks=tuple(m['chunks']); fill=m['fill_value']
        if fill=='NaN': fill=np.nan
        if fill is None: fill=0
        out=np.full(self.shape,fill,dtype=self.dtype)
        for idx in itertools.product(*(range((s+c-1)//c) for s,c in zip(self.shape,chunks))):
            p=self.path/m.get('dimension_separator','.').join(map(str,idx))
            if not p.exists():
                if m['fill_value'] is None: raise ValueError('unspecified fill in missing Zarr chunk')
                continue
            raw=p.read_bytes(); comp=m.get('compressor')
            if comp:
                if comp['id']!='blosc': raise ValueError('unsupported compressor')
                raw=blosc2.decompress(raw)
            data=np.frombuffer(raw,dtype=self.dtype).reshape(chunks,order=m['order'])
            sl=tuple(slice(i*c,min((i+1)*c,s)) for i,c,s in zip(idx,chunks,self.shape))
            cut=tuple(slice(0,x.stop-x.start) for x in sl)
            out[sl]=data[cut]
        return out
    def __getitem__(self,k): return self.read()[k]


def directory_digest(path):
    import hashlib
    path = Path(path)
    h = hashlib.sha256()
    for p in sorted((p for p in path.rglob("*") if p.is_file()), key=lambda x: x.relative_to(path).as_posix()):
        if p.is_symlink():
            raise ValueError("symlink in frozen input")
        name = p.relative_to(path).as_posix().encode()
        raw = p.read_bytes()
        h.update(len(name).to_bytes(4, "big")); h.update(name)
        h.update(len(raw).to_bytes(8, "big")); h.update(hashlib.sha256(raw).digest())
    return h.hexdigest()


def case_groups(root, case):
    root = Path(root).resolve()
    n = (root/case["normalized_path"]).resolve()
    q = (root/case["qc_path"]).resolve()
    if not n.is_relative_to(root) or not q.is_relative_to(root):
        raise ValueError("case path escapes frozen package")
    if not (q/".zgroup").exists():
        objects = sorted((q/"_objects").iterdir())
        if len(objects) != 1:
            raise ValueError("ambiguous QC object; select an exact artifact")
        q = objects[0]
    return Group(n), Group(q)

from types import SimpleNamespace
def native(g, rootattrs, cfg):
    az=g['azimuth'][:].astype(float);r=g['range'][:].astype(float)
    order=np.argsort(az,kind='stable');gaz=az[order];gaps=np.diff(np.r_[gaz,gaz[0]+360])
    spacing=float(np.median(gaps[gaps>.01]));full=bool(spacing<=3 and not (gaps>spacing*1.8).any() and not (gaps<=.01).any())
    if not full:order=np.roll(order,-((int(np.argmax(gaps))+1)%len(order)))
    azimuth=az[order];gaps=((np.roll(azimuth,-1)-azimuth)%360)>spacing*1.8
    dup=((np.roll(azimuth,-1)-azimuth)%360)<=.01;good=~(dup|np.roll(dup,1))
    f={k:g[k][:][order] for k in ('DBZH','SNR','RHOHV','ZDR','PHIDP','VR','SW') if k in g}
    a={k:np.isfinite(v)&good[:,None] for k,v in f.items()}
    lo,hi=cfg['echo'].get('dbzh_valid_range_dbz',(-32,80))
    if 'DBZH' in a:a['DBZH']&=(f['DBZH']>=lo)&(f['DBZH']<=hi)
    if 'RHOHV' in a:a['RHOHV']&=(f['RHOHV']>=0)&(f['RHOHV']<=1)
    if 'SW' in a:a['SW']&=f['SW']>=0
    if 'DBZH' not in f:
        f['DBZH']=np.full((len(az),len(r)),np.nan,'float32');a['DBZH']=np.zeros((len(az),len(r)),bool)
    n=SimpleNamespace(fields=f,field_available=a,shape=(len(az),len(r)),azimuth=azimuth,ranges=r,elevation=g['elevation'][:][order],ray_time=g['ray_time'][:][order],geometry_good=good,gap_after=gaps,full_ppi=full,original_indices=order,gate_spacing_m=float(np.median(np.diff(r))),name=g.path.name,attrs=rootattrs)
    def restore(x):
        out=np.empty_like(x);out[order]=x;return out
    n.restore=restore
    return n
