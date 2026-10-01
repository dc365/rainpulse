"""Generate a versioned S-QC child from the exact active parent; no deployment."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))
from rainpulse_algo.radar.qc_engine.profile import OpenSourceQCProfile


def generate(parent):
    OpenSourceQCProfile.model_validate(parent)
    if parent.get('operational_eligible') is not False:
        raise ValueError('experimental parent required; no automatic operational promotion')
    child = copy.deepcopy(parent)
    revision = child['generalization']['broad_source']['source_review']['radial_revision']
    if revision['mode'] != 'experiment_quarantine':
        raise ValueError('use the active quarantine parent, not an audit/legacy profile')
    line = revision['fragment_line']
    if not line.get('residual_objects_enabled'):
        raise ValueError('active parent must already enable residual objects')
    for key in ('discontinuous_tracks_enabled', 'source_envelope_enabled',
                'raw_fragment_families_enabled', 'source_ledger_enabled',
                'raw_fan_families_enabled', 'source_footprint_enabled'):
        line[key] = True
    suffix = '-s-bounded-radial-20261001-v3'
    if child['profile_version'].endswith(suffix):
        raise ValueError('parent is already the discontinuous child')
    child['profile_version'] += suffix
    OpenSourceQCProfile.model_validate(child)
    return child


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('parent', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    raw = args.parent.read_bytes()
    child = generate(yaml.safe_load(raw))
    payload = yaml.safe_dump(child, sort_keys=False).encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as f:
        f.write(payload)
    print(json.dumps({'parent_sha256': hashlib.sha256(raw).hexdigest(),
                      'child_sha256': hashlib.sha256(payload).hexdigest(),
                      'profile_version': child['profile_version'],
                      'output': str(args.output), 'deployed': False}))


if __name__ == '__main__':
    main()
