#!/usr/bin/env python3
"""Read-only background verification of an existing S refresh and public PNGs."""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import time
from urllib.parse import quote
from urllib.request import urlopen
import uuid
import zlib


MAX_PNG = 16 * 1024**2


def sha(data):
    return hashlib.sha256(data).hexdigest()


def png_receipt(data, maximum=MAX_PNG):
    if len(data) > maximum:
        raise ValueError('PNG byte limit exceeded')
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('PNG signature missing')
    offset, image, compressed, ended = 8, None, bytearray(), False
    while offset + 12 <= len(data):
        size, kind = struct.unpack('>I4s', data[offset:offset+8])
        end = offset + 12 + size
        if end > len(data):
            raise ValueError('PNG truncated chunk')
        body = data[offset+8:end-4]
        if zlib.crc32(kind+body) != struct.unpack('>I', data[end-4:end])[0]:
            raise ValueError('PNG chunk checksum mismatch')
        if image is None and kind != b'IHDR':
            raise ValueError('PNG missing initial header')
        if kind == b'IHDR':
            if image is not None or size != 13:
                raise ValueError('PNG invalid header')
            image = struct.unpack('>IIBBBBB', body)
        elif kind == b'IDAT':
            compressed.extend(body)
        elif kind == b'IEND':
            if size or end != len(data):
                raise ValueError('PNG invalid end or trailing bytes')
            ended = True
            break
        offset = end
    if not ended or not image or not compressed:
        raise ValueError('PNG incomplete image')
    width, height, depth, color, compression, filtering, interlace = image
    # Diagnostic renderer emits 8-bit, noninterlaced RGB/RGBA PNGs.
    if (not width or not height or width*height > 16*1024**2 or depth != 8 or
            color not in (2, 6) or compression or filtering or interlace):
        raise ValueError('PNG unsupported diagnostic dimensions/format')
    row_size = 1 + width * (3 if color == 2 else 4)
    expected = height * row_size
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, expected+1)
    except zlib.error as exc:
        raise ValueError('PNG invalid image compression') from exc
    if (len(raw) != expected or not decoder.eof or decoder.unused_data or
            decoder.unconsumed_tail or any(raw[i] > 4 for i in range(0, expected, row_size))):
        raise ValueError('PNG invalid or excessive decoded image')
    return dict(sha256=sha(data), bytes=len(data), width=width, height=height)


def require_members(inputs, expected, field):
    observed = {}
    for item in inputs:
        scan = item['scan_id']
        if scan in observed:
            raise ValueError('duplicate input membership')
        observed[scan] = (item['radar_id'], item[field])
    if observed != expected:
        raise ValueError('published input membership differs from refreshed scans')


def job_payload(row):
    envelope = row['request_payload']
    if row['status'] != 'SUCCEEDED' or envelope['job_id'] != row['job_id']:
        raise ValueError('job incomplete or envelope identity mismatch')
    return envelope['payload']


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def validate_station_supplement(original, replacement, allowed, native_rows, qc_jobs, profile):
    """Allow only an immutable supplement with unchanged contributing input lineage."""
    mutable = {'radar_inputs', 'output_prefix'}
    if (original.keys() != replacement.keys() or any(
            original[k] != replacement[k] for k in original.keys() - mutable)):
        raise ValueError('supplement changed analysis or rendering identity')
    prefix = ('s3://rainpulse/diagnostics/' + str(uuid.UUID(original['analysis_id'])) + '/' +
              quote(original['renderer_version'], safe='') + '/station-supplements/')
    output = replacement['output_prefix']
    if not output.startswith(prefix):
        raise ValueError('supplement output identity missing')
    revision = output[len(prefix):]
    if not revision.endswith('/') or str(uuid.UUID(revision[:-1])) != revision[:-1]:
        raise ValueError('supplement revision identity invalid')
    if uuid.UUID(revision[:-1]).int == 0:
        raise ValueError('supplement revision must be nonzero')
    expected = {r['scan_id']: (r['radar_id'], r['qc_uri']) for r in original['radar_inputs']}
    require_members([r for r in replacement['radar_inputs'] if r['scan_id'] in expected],
                    expected, 'qc_uri')
    seen_scans, seen_sites, extras = set(), set(), []
    for item in replacement['radar_inputs']:
        scan, site = item['scan_id'], item['radar_id']
        if scan in seen_scans or site in seen_sites:
            raise ValueError('supplement duplicate scan or station')
        seen_scans.add(scan)
        seen_sites.add(site)
        if scan in expected:
            continue
        if site not in allowed:
            raise ValueError('supplement station not explicitly allowed')
        row = native_rows[scan]
        age = (instant(original['analysis_time']) - instant(row['volume_end_time'])).total_seconds()
        if (row['scan_id'] != scan or row['radar_id'] != site or row['radar_band'] != 'S' or
                row['qc_uri'] != item['qc_uri'] or not 0 <= age <= 720):
            raise ValueError('supplement is not causal current native S QC')
        match = re.search(r'/inputs/([0-9a-f-]{36})/volume\.zarr$', item['qc_uri'])
        if not match:
            raise ValueError('supplement native QC job binding missing')
        job = str(uuid.UUID(match[1]))
        qc = job_payload(qc_jobs[job])
        if (qc['scan_id'] != scan or qc['radar_id'] != site or
                qc['qc_profile_sha256'] != profile or qc['input_uri'] != row['normalized_uri'] or
                qc['output_prefix'] + 'volume.zarr' != item['qc_uri']):
            raise ValueError('supplement QC source or profile differs')
        extras.append(item)
    if not extras:
        raise ValueError('replacement is not a station supplement')
    return extras


def public_generation(detail, group):
    frames = [f for p in detail['panels'] if p['panel_id'] == 'analysis:dbzh_qc'
              for f in p['frames'] if instant(f['valid_time']) == instant(group['issue_time'])
              and f.get('lead_time_minutes') == 0]
    if len(frames) != 1:
        raise ValueError('missing or ambiguous composite generation')
    match = re.fullmatch(r'/api/v1/diagnostics/([0-9a-f-]{36})/layers/[a-z0-9-]+',
                         frames[0]['image_url'])
    if not match:
        raise ValueError('public diagnostic job identity missing')
    return str(uuid.UUID(match[1]))


def load_station_supplement(original_row, current_job, allowed, profile):
    rows = query('SELECT coalesce(json_agg(t),\'[]\') FROM (SELECT job_id,status,job_type,'
                 'request_payload FROM jobs WHERE job_id=\''+str(uuid.UUID(current_job))+'\') t')
    if len(rows) != 1:
        raise ValueError('supplement diagnostic job missing or ambiguous')
    current = rows[0]
    original = job_payload(original_row)
    replacement = job_payload(current)
    if (current['job_type'] != 'analysis.diagnostics' or
            current['request_payload']['event_type'] != 'analysis.diagnostics.requested.v1' or
            current['request_payload']['run_id'] != original_row['request_payload']['run_id']):
        raise ValueError('supplement diagnostic workflow differs')
    base_scans = {r['scan_id'] for r in original['radar_inputs']}
    extras = [r for r in replacement['radar_inputs'] if r['scan_id'] not in base_scans]
    if not extras:
        raise ValueError('replacement is not a station supplement')
    ids = ','.join("'"+str(uuid.UUID(r['scan_id']))+"'" for r in extras)
    native = query('SELECT coalesce(json_agg(t),\'[]\') FROM (SELECT DISTINCT ON(rs.scan_id) '
                   'rs.scan_id,rs.radar_id,rs.qc_uri,rs.normalized_uri,s.volume_end_time,'
                   "c.config#>>'{hardware,radar_band}' AS radar_band FROM radar_scan_runs rs "
                   'JOIN radar_scans s USING(scan_id) JOIN radars d ON d.radar_id=rs.radar_id '
                   'JOIN radar_config_versions c ON c.radar_id=d.radar_id AND '
                   'c.radar_config_version=d.current_config_version WHERE rs.scan_id IN ('+ids+
                   ') ORDER BY rs.scan_id,rs.updated_at DESC) t')
    qc_ids = []
    for item in extras:
        match = re.search(r'/inputs/([0-9a-f-]{36})/volume\.zarr$', item['qc_uri'])
        if not match:
            raise ValueError('supplement native QC job binding missing')
        qc_ids.append("'"+str(uuid.UUID(match[1]))+"'")
    qc_rows = query('SELECT coalesce(json_agg(t),\'[]\') FROM (SELECT job_id,status,'
                    'request_payload FROM jobs WHERE job_id IN ('+','.join(qc_ids)+')) t')
    verified = validate_station_supplement(
        original, replacement, allowed, {r['scan_id']: r for r in native},
        {r['job_id']: r for r in qc_rows}, profile)
    return verified


def public_frames(detail, group, sites, analysis, diagnostic):
    if detail['cycle_id'] != group['cycle_id'] or detail['analysis_id'] != analysis:
        raise ValueError('public cycle analysis generation mismatch')
    selected = []
    panels = {}
    for panel in detail['panels']:
        key = panel['panel_id']
        if key in panels:
            raise ValueError('duplicate public panel')
        panels[key] = [f for f in panel['frames'] if
                       instant(f['valid_time']) == instant(group['issue_time']) and
                       f.get('lead_time_minutes') == 0]
    for site in group['missing_sites']:
        if panels.get('dbzh_raw:'+site) or panels.get('dbzh_qc:'+site):
            raise ValueError('planned missing station changed')
    for scan in group['scans']:
        site = sites[scan]
        pair = []
        for kind in ('raw', 'qc'):
            frames = panels.get('dbzh_'+kind+':'+site, [])
            by_sweep = {}
            for frame in frames:
                sweep = frame['sweep_number']
                if frame['scan_id'] != scan or sweep in by_sweep:
                    raise ValueError('public station/scan/sweep mismatch')
                by_sweep[sweep] = frame
            if not by_sweep or 0 not in by_sweep:
                raise ValueError('missing T0 raw/QC sweep')
            pair.append(by_sweep)
            selected.extend(frames)
        if pair[0].keys() != pair[1].keys():
            raise ValueError('raw/QC sweep membership differs')
    for panel in ('analysis:dbzh_qc', 'qpe'):
        frames = panels.get(panel, [])
        if len(frames) != 1:
            raise ValueError('missing or ambiguous composite/QPE T0')
        selected.extend(frames)
    for frame in selected:
        match = re.fullmatch(r'/api/v1/diagnostics/([0-9a-f-]{36})/layers/[a-z0-9-]+',
                             frame['image_url'])
        if not match or str(uuid.UUID(match[1])) != diagnostic:
            raise ValueError('public image diagnostics generation mismatch')
    return selected


def query(sql):
    raw = subprocess.check_output(
        ['docker', 'exec', 'rainpulse-postgres-1', 'psql', '-U', 'rainpulse', '-d',
         'rainpulse', '-At', '-c', sql], text=True, timeout=60)
    return json.loads(raw)


def fetch_json(url):
    with urlopen(url, timeout=30) as response:
        return json.loads(response.read(8*1024**2+1))


def audit(plan, state, previous, base, allowed_supplements=()):
    sites = {s['scan_id']: s['radar_id'] for s in plan['scans']}
    if not set(state['scans']) <= sites.keys():
        raise ValueError('refresh scan outside frozen plan')
    planned_slots = {g['issue_time'] for g in plan['groups']}
    if not set(state['completed']) <= planned_slots or len(set(state['completed'])) != len(
            state['completed']):
        raise ValueError('refresh completed slots outside unique frozen plan')
    handles = {r['key']: r for r in state['jobs']}
    ids = ','.join("'"+str(uuid.UUID(r['job_id']))+"'" for r in handles.values())
    jobs = query('SELECT coalesce(json_agg(t),\'[]\') FROM (SELECT job_id,status,request_payload '
                 'FROM jobs WHERE job_id IN ('+(ids or 'NULL')+')) t')
    jobs = {j['job_id']: j for j in jobs}
    def payload(key):
        return job_payload(jobs[handles[key]['job_id']])
    scans = state['scans']
    if scans:
        scan_ids = ','.join("'"+str(uuid.UUID(s))+"'" for s in scans)
        rows = query('SELECT coalesce(json_agg(t),\'[]\') FROM (SELECT DISTINCT ON(scan_id) '
                     'scan_id,normalized_uri,qc_uri,grid_uri FROM radar_scan_runs WHERE scan_id IN ('+
                     scan_ids+') ORDER BY scan_id,updated_at DESC) t')
        latest = {r['scan_id']: r for r in rows}
    else:
        latest = {}
    for scan, outputs in scans.items():
        qc, grid = payload('qc:'+scan), payload('grid:'+scan)
        if (qc['scan_id'] != scan or grid['scan_id'] != scan or
                qc['radar_id'] != sites[scan] or grid['radar_id'] != sites[scan] or
                qc['qc_profile_sha256'] != plan['identity']['config_sha256']['qc'] or
                qc['input_uri'] != latest[scan]['normalized_uri'] or
                qc['output_prefix']+'volume.zarr' != outputs['qc_uri'] or
                grid['input_uri'] != outputs['qc_uri'] or
                grid['output_prefix']+'grid.zarr' != outputs['grid_uri'] or
                any(latest[scan][k] != outputs[k] for k in ('qc_uri', 'grid_uri'))):
            raise ValueError('QC/grid frozen identity or latest lineage differs: '+scan)
    proofs = {}
    for group in plan['groups']:
        slot = group['issue_time']
        if slot not in state['completed']:
            continue
        mosaic, qpe, diag = (payload(k+':'+slot) for k in ('mosaic', 'qpe', 'diagnostics'))
        analysis = handles['mosaic:'+slot]['analysis_id']
        if any(p['analysis_id'] != analysis or instant(p['analysis_time']) != instant(slot)
               for p in (mosaic, qpe, diag)):
            raise ValueError('slot analysis generation mismatch')
        require_members(mosaic['inputs'],
                        {s: (sites[s], scans[s]['grid_uri']) for s in group['scans']}, 'grid_uri')
        require_members(diag['radar_inputs'],
                        {s: (sites[s], scans[s]['qc_uri']) for s in group['scans']}, 'qc_uri')
        if (qpe['input_uri'] != mosaic['output_prefix']+'mosaic.zarr' or
                diag['input_uri'] != qpe['output_prefix']+'analysis.zarr'):
            raise ValueError('mosaic/QPE/diagnostics lineage mismatch')
        diagnostic = baseline_job = handles['diagnostics:'+slot]['job_id']
        detail = fetch_json(base+'/api/v1/workspace/cycles/'+quote(group['cycle_id'], safe=''))
        extras = []
        try:
            frames = public_frames(detail, group, sites, analysis, diagnostic)
        except ValueError as exc:
            if not allowed_supplements or str(exc) != 'public image diagnostics generation mismatch':
                raise
            diagnostic = public_generation(detail, group)
            extras = load_station_supplement(jobs[baseline_job], diagnostic,
                                            set(allowed_supplements),
                                            plan['identity']['config_sha256']['qc'])
            extra_group = dict(group, scans=group['scans']+[r['scan_id'] for r in extras])
            extra_sites = dict(sites, **{r['scan_id']: r['radar_id'] for r in extras})
            frames = public_frames(detail, extra_group, extra_sites, analysis, diagnostic)
        old = previous.get(slot, {})
        images = {}
        baseline_images = (old.get('baseline_images', {}) if
                           old.get('baseline_diagnostic_job_id') == baseline_job else
                           old.get('images', {}) if old.get('diagnostic_job_id') == baseline_job else {})
        contributor_urls = {f['image_url'] for f in public_frames(
            detail, group, sites, analysis, diagnostic)}
        for frame in frames:
            url = frame['image_url']
            if url in images:
                continue
            if old.get('diagnostic_job_id') == diagnostic and url in old.get('images', {}):
                images[url] = old['images'][url]
            else:
                with urlopen(base+url, timeout=30) as response:
                    images[url] = png_receipt(response.read(MAX_PNG+1))
            if extras and url in contributor_urls:
                baseline_url = url.replace('/'+diagnostic+'/', '/'+baseline_job+'/', 1)
                if baseline_url not in baseline_images:
                    with urlopen(base+baseline_url, timeout=30) as response:
                        baseline_images[baseline_url] = png_receipt(response.read(MAX_PNG+1))
                if images[url] != baseline_images[baseline_url]:
                    raise ValueError('supplement changed an original contributor/composite/QPE image')
        proofs[slot] = dict(analysis_id=analysis, diagnostic_job_id=diagnostic,
                           public_frame_count=len(frames), images=images)
        if extras:
            proofs[slot].update(baseline_diagnostic_job_id=baseline_job,
                                baseline_images=baseline_images, station_supplements=extras)
    complete = (state['status'] == 'DONE' and len(scans) == len(plan['scans']) and
                len(proofs) == len(plan['groups']))
    if state['status'] == 'DONE' and not complete:
        raise ValueError('refresh DONE receipt has incomplete frozen inventory')
    return dict(status='VERIFIED' if complete else 'RUNNING', scans_verified=len(scans),
                scans_total=len(plan['scans']), slots_verified=len(proofs),
                slots_total=len(plan['groups']), slots=proofs,
                meteorological_effectiveness_verified=False, production_writes=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base', default='http://127.0.0.1:4173')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=int, default=60)
    parser.add_argument('--allow-station-supplement', action='append', default=[])
    args = parser.parse_args()
    if args.interval < 15 or args.output.resolve() == (args.refresh/'state.json').resolve():
        raise ValueError('invalid audit interval or output')
    lock = args.output.with_suffix('.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    plan_bytes = (args.refresh/'plan.json').read_bytes()
    plan = json.loads(plan_bytes)
    identity = dict(plan_sha256=sha(plan_bytes), auditor_sha256=sha(Path(__file__).read_bytes()))
    if args.allow_station_supplement:
        identity['allowed_station_supplements'] = sorted(set(args.allow_station_supplement))
    previous = json.loads(args.output.read_text()) if args.output.exists() else {'slots': {}}
    if args.output.exists() and any(previous.get(k) != v for k, v in identity.items()):
        raise ValueError('persisted auditor/plan changed')
    while True:
        terminal = False
        try:
            if (sha((args.refresh/'plan.json').read_bytes()) != identity['plan_sha256'] or
                    sha(Path(__file__).read_bytes()) != identity['auditor_sha256']):
                raise ValueError('frozen plan/auditor changed')
            state_bytes = (args.refresh/'state.json').read_bytes()
            state = json.loads(state_bytes)
            if state['plan_sha256'] != identity['plan_sha256']:
                raise ValueError('refresh receipt plan mismatch')
            result = audit(plan, state, previous['slots'], args.base, args.allow_station_supplement)
            result['observed_refresh_state_sha256'] = sha(state_bytes)
            result['refresh_status'] = state['status']
            if state['status'] == 'ERROR':
                result.update(status='REFRESH_ERROR', error=state.get('error'))
            terminal = result['status'] in ('VERIFIED', 'REFRESH_ERROR')
        except ValueError as exc:
            result = dict(previous, status='IDENTITY_OR_PUBLICATION_ERROR', error=str(exc))
            terminal = True
        except Exception as exc:
            result = dict(previous, status='OBSERVATION_RETRY', error=type(exc).__name__+': '+str(exc))
        result.update(identity, updated_at=datetime.now(timezone.utc).isoformat())
        temporary = args.output.with_suffix('.tmp')
        temporary.write_text(json.dumps(result, indent=2)+'\n')
        temporary.replace(args.output)
        print(json.dumps({k: v for k, v in result.items() if k != 'slots'}), flush=True)
        previous = result
        if args.once or terminal:
            if result['status'] not in ('RUNNING', 'VERIFIED'):
                raise SystemExit(1)
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
