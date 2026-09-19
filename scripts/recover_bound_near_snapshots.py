"""Recover hash-bound NPZ snapshots from a sanitized concatenation-pack export.

This is NOT a general Zarr decoder or a reconstructed publication marker. The
supplied export lacks outer logical-key metadata. Only self-contained ZIP NPZs
matching the embedded P0 manifest's SHA256 and ZIP CRC are accepted.
"""
import argparse
from pathlib import Path,PurePosixPath
import hashlib,io,json,os,re,struct,tarfile,tempfile,zipfile


def recover(archive,output):
    archive,output=Path(archive),Path(output)
    if output.exists():raise ValueError('output already exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive,'r:*') as tar, tempfile.TemporaryDirectory(prefix='.near-recovery-',dir=output.parent) as tmp:
        members=tar.getmembers()
        for m in members:
            p=PurePosixPath(m.name)
            if p.is_absolute() or '..' in p.parts or m.issym() or m.islnk():raise ValueError('unsafe archive member')
        metadata=[m for m in members if m.name.endswith('/metadata/cases.jsonl') and m.isfile()]
        if len(metadata)!=1 or metadata[0].size>1000000:raise ValueError('ambiguous/missing cases metadata')
        content=tar.extractfile(metadata[0]).read();cases=[json.loads(x) for x in content.decode().splitlines() if x.strip()]
        if not 1<=len(cases)<=100:raise ValueError('case budget exceeded')
        reports=[];root=Path(tmp)
        for case in cases:
            identity=case['case_id']
            if not re.fullmatch(r'[A-Za-z0-9_-]+',identity):raise ValueError('unsafe case identity')
            pieces=[m for m in members if m.isfile() and f'/{identity}/qc/' in m.name and '/packs/' in m.name and m.name.endswith('.bin')]
            if not pieces or sum(m.size for m in pieces)>512*1024**2:raise ValueError('absent/oversized case pack')
            data=b''.join(tar.extractfile(m).read() for m in sorted(pieces,key=lambda m:m.name))
            start=0;manifest=None;decoder=json.JSONDecoder()
            while True:
                pos=data.find(b'{"algorithm_version":',start)
                if pos<0:break
                start=pos+1
                try:obj,_=decoder.raw_decode(data[pos:pos+2000000].decode('utf8',errors='replace'))
                except ValueError:continue
                if obj.get('schema')=='rainpulse.bound-volume-evidence-v1':manifest=obj;break
            if manifest is None:raise ValueError('bound numeric manifest not found')
            targets={r['sha256']:r for r in manifest['sweeps']};found={};cursor=0;d=root/identity;d.mkdir()
            while True:
                end=data.find(b'PK\x05\x06',cursor)
                if end<0:break
                cursor=end+4
                if end+22>len(data):continue
                disk,cdisk,n1,n2,size,offset,comment=struct.unpack_from('<4H2IH',data,end+4)
                if disk or cdisk or n1!=n2 or comment or not n1:continue
                begin=end-size-offset
                if begin<0 or data[begin:begin+4]!=b'PK\x03\x04':continue
                value=data[begin:end+22];sha=hashlib.sha256(value).hexdigest()
                if sha not in targets:continue
                record=targets[sha];name=record['sweep']
                if not re.fullmatch(r'sweep_\d{3}',name):raise ValueError('unsafe sweep identity')
                with zipfile.ZipFile(io.BytesIO(value)) as z:
                    if z.testzip() is not None:raise ValueError('snapshot ZIP CRC failed')
                (d/(name+'.npz')).write_bytes(value);found[name]=sha
            if len(found)!=len(targets):raise ValueError('not every bound snapshot was recovered')
            (d/'manifest.json').write_text(json.dumps(manifest,indent=2))
            reports.append({'case_id':identity,'snapshots':found,'checks':'embedded snapshot SHA256 + ZIP CRC; no outer publication validation'})
        (root/'cases.jsonl').write_bytes(content);(root/'recovery.json').write_text(json.dumps(reports,indent=2))
        if output.exists():raise ValueError('output appeared concurrently')
        os.rename(root,output)
    return reports

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--archive',required=True,type=Path);p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();print(json.dumps(recover(a.archive,a.output),indent=2))
