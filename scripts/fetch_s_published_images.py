#!/usr/bin/env python3
"""Save the actual Web RAW/QC PNG pair bound by a published-QC audit receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen
import uuid


def bound_urls(receipt):
    if receipt.get('scope') != 'exact_Web_consumed_stored_QC_not_replay':
        raise ValueError('published QC audit receipt required')
    job = str(uuid.UUID(receipt['diagnostic_job_id']))
    web = receipt['web_frame_identity']; result = {}
    site = web['site']; sweep = web['sweep']
    if not re.fullmatch(r'z[0-9]+', site) or type(sweep) is not int or not 0 <= sweep <= 999:
        raise ValueError('invalid native station/sweep identity')
    for side in ('raw', 'qc'):
        frame = web[side+'_frame']; path = frame['image_url']
        asset = f'radar-{site}-dbzh-{side}-sweep-{sweep:03d}'
        if (frame['scan_id'] != web['web_scan_id'] or
            frame['sweep_number'] != sweep or frame['asset_id'] != asset or
            path != f'/api/v1/diagnostics/{job}/layers/{asset}'):
            raise ValueError('PNG must match the audited scan and diagnostic generation')
        result[side] = path
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('receipt',type=Path); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--web-base',default='http://192.168.28.105:4173')
    args = p.parse_args(); data = args.receipt.read_bytes()
    urls = bound_urls(json.loads(data))
    args.output.mkdir(parents=True,exist_ok=False); files = []
    for side,path in urls.items():
        with urlopen(args.web_base.rstrip('/')+path,timeout=30) as response:
            if response.headers.get_content_type() != 'image/png': raise ValueError('PNG response required')
            pixels = response.read(16*1024*1024+1)
        if len(pixels)>16*1024*1024 or not pixels.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('invalid or excessive PNG response')
        name = side+'.png'
        with (args.output/name).open('xb') as f: f.write(pixels)
        files.append(dict(file=name,url=path,bytes=len(pixels),sha256=hashlib.sha256(pixels).hexdigest()))
    manifest = dict(audit_sha256=hashlib.sha256(data).hexdigest(),files=files)
    with (args.output/'manifest.json').open('x') as f: json.dump(manifest,f,indent=2); f.write('\n')
    print(json.dumps(manifest,indent=2))


if __name__=='__main__': main()
