"""Resolve the exact S raw/QC pair selected by the Web, without time guessing."""
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
import json
from urllib.request import urlopen


def resolve_frames(base_url,day,cases,fetch_json=None):
    if fetch_json is None:
        def fetch_json(url):
            with urlopen(url,timeout=30) as response:return json.load(response)
    base=base_url.rstrip('/')
    catalog=[];cursor='';seen=set()
    while True:
        query={'limit':200}
        if cursor:query['cursor']=cursor
        page=fetch_json(base+'/api/v1/workspace/cycles?'+urlencode(query))
        catalog.extend(page['items'])
        cursor=page.get('next_cursor')
        if not cursor:break
        if cursor in seen:raise ValueError('Web cycle catalog repeated cursor')
        seen.add(cursor)
    details={};result=[]
    for site,time,sweep in cases:
        target=datetime.fromisoformat(day+'T'+time+':00+08:00').astimezone(timezone.utc)
        cycles=[c for c in catalog if datetime.fromisoformat(c['issue_time'].replace('Z','+00:00'))==target]
        identities={c['cycle_id'] for c in cycles}
        if len(identities)!=1:raise ValueError(f'Web cycle missing or ambiguous: {site} {time}')
        cycle=cycles[0];identity=cycle['cycle_id']
        if identity not in details:
            details[identity]=fetch_json(base+'/api/v1/workspace/cycles/'+quote(identity,safe=''))
        detail=details[identity]
        if detail['cycle_id']!=identity:raise ValueError('Web cycle response identity mismatch')
        def frames(panel_id):
            panels=[p for p in detail['panels'] if p['panel_id']==panel_id]
            if len(panels)!=1:raise ValueError('Web raw/QC panel missing or ambiguous')
            return [f for f in panels[0]['frames'] if f.get('sweep_number')==int(sweep) and
                datetime.fromisoformat(f['valid_time'].replace('Z','+00:00'))==target]
        raw=frames('dbzh_raw:'+site.lower())
        if len(raw)!=1 or not raw[0].get('scan_id'):raise ValueError('Web raw frame missing or ambiguous')
        qc=[f for f in frames('dbzh_qc:'+site.lower()) if f.get('scan_id')==raw[0]['scan_id']]
        if len(qc)!=1:raise ValueError('Web raw/QC frame scan pair mismatch')
        result.append(dict(site=site.lower(),local_time=time,sweep=int(sweep),cycle_id=identity,
            issue_time=cycle['issue_time'],web_scan_id=raw[0]['scan_id'],raw_frame=raw[0],qc_frame=qc[0]))
    return result
