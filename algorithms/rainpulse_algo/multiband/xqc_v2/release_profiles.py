"""Reproducible upgrades from an exact frozen network, never a live config edit.

Unlike the historical generator, existing enhancements are preserved/upgraded.
There are no ZF701 noise/width/action-budget values silently applied to others.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from .config import XQCConfig

REVISION = 'xqc-hardening-20260929-r1'


def parse_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('duplicate JSON key: ' + key)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda s: (_ for _ in ()).throw(ValueError('nonfinite JSON: '+s)))


def diff(old, new, prefix=''):
    if isinstance(old, dict) and isinstance(new, dict):
        out = []
        for k in sorted(old.keys() | new.keys()):
            path = prefix+'/'+str(k)
            if k not in old:
                out.append({'path':path, 'operation':'add', 'after':new[k]})
            elif k not in new:
                out.append({'path':path, 'operation':'remove', 'before':old[k]})
            else:
                out.extend(diff(old[k], new[k], path))
        return out
    return [] if old == new else [{'path':prefix, 'operation':'replace', 'before':old, 'after':new}]


def merge(old, patch):
    """Nested updates preserve untouched thresholds and asset bindings."""
    result = copy.deepcopy(old)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def generate(raw, *, parent_sha256, radar_ids, release_id, preset='preserve', updates=None):
    actual = hashlib.sha256(raw).hexdigest()
    if actual != parent_sha256:
        raise ValueError('parent network SHA256 changed')
    if len(raw) > 1024**2:
        raise ValueError('parent network exceeds 1 MiB')
    parent = parse_json(raw)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', release_id) or release_id == parent.get('release_id'):
        raise ValueError('new valid release_id required')
    if not radar_ids or len(set(radar_ids)) != len(radar_ids):
        raise ValueError('unique selected X stations required')
    if preset not in ('preserve','source','fan','context-audit'):
        raise ValueError('unknown X preset')
    updates = updates or {}
    if set(updates)-set(radar_ids):
        raise ValueError('updates include an unselected station')
    child = copy.deepcopy(parent)
    child['release_id'] = release_id
    for sid in radar_ids:
        station = child.get('stations',{}).get(sid)
        if station is None or station.get('band') != 'X' or not station.get('x_qc_enabled'):
            raise ValueError('selected station is not enabled for candidate X QC: '+sid)
        profile = station.setdefault('x_qc',{})
        values = copy.deepcopy(profile.get('enhancement') or {})
        if not isinstance(values,dict):
            raise ValueError('enhancement must be an object')
        if preset in ('source','fan'):
            values['radial_source_enabled'] = True
        if preset == 'fan':
            values.update(radial_source_block_model_enabled=True, radial_source_fan_model_enabled=True)
        if preset == 'context-audit':
            values['context'] = {**values.get('context',{}), 'mode':'audit'}
        patch = updates.get(sid,{})
        if not isinstance(patch,dict):
            raise ValueError('station updates must be an object')
        values = merge(values, patch)
        # No implicit 3 dB, 7 degree or 65% transfer from the tuned ZF701 case.
        cfg = XQCConfig.model_validate(values)
        profile['enhancement'] = cfg.model_dump(mode='json')
    encoded = (json.dumps(child,indent=2,ensure_ascii=False,allow_nan=False)+'\n').encode()
    if len(encoded)>1024**2:
        raise ValueError('child network exceeds 1 MiB')
    # Validate the existing network/geometry contract as well as new parameters.
    from ..model import Network
    Network.from_bytes(encoded)
    receipt = {'implementation_revision':REVISION, 'preset':preset,
               'parent_sha256':actual, 'child_sha256':hashlib.sha256(encoded).hexdigest(),
               'selected_radars':list(radar_ids), 'changes':diff(parent,child),
               'deployment_performed':False, 'operational_eligible':False}
    return encoded, receipt


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--parent',type=Path,required=True)
    p.add_argument('--parent-sha256',required=True)
    p.add_argument('--radars',nargs='+',required=True)
    p.add_argument('--release-id',required=True)
    p.add_argument('--preset',choices=('preserve','source','fan','context-audit'),default='preserve')
    p.add_argument('--updates',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    encoded,receipt=generate(args.parent.read_bytes(),parent_sha256=args.parent_sha256,
        radar_ids=args.radars,release_id=args.release_id,preset=args.preset,
        updates=parse_json(args.updates.read_bytes()) if args.updates else None)
    args.output.mkdir(parents=False,exist_ok=False)
    try:
        (args.output/'network.json').write_bytes(encoded)
        (args.output/'upgrade-receipt.json').write_text(json.dumps(receipt,indent=2,ensure_ascii=False)+'\n')
    except BaseException:
        for name in ('network.json','upgrade-receipt.json'):
            (args.output/name).unlink(missing_ok=True)
        args.output.rmdir()
        raise


if __name__ == '__main__':
    main()
