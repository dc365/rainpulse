#!/usr/bin/env python3
"""Generate a versioned child of the deployed radial step3 profile."""
import argparse
from pathlib import Path
import yaml


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--direct-morphology', action='store_true')
    p.add_argument('--isolated', action='store_true')
    a = p.parse_args()
    data = yaml.safe_load(a.base.read_text())
    rv = data['generalization']['broad_source']['source_review']['radial_revision']
    if rv['step'] != 3 or rv.get('fragment_line') is not None or data.get('operational_eligible') is not False:
        raise ValueError('requires an experimental step3 parent without fragment-line')
    version = 'fragment-line-v2' if a.direct_morphology else 'fragment-line-v1'
    if a.isolated:
        version = 'fragment-line-v3'
    data['profile_version'] += '-'+version
    rv['fragment_line'] = {'version': version, 'coherent_source_enabled': True,
                           'morphology_quarantine_enabled': a.direct_morphology or a.isolated,
                           'isolated_quarantine_enabled': a.isolated}
    with a.output.open('x') as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


if __name__ == '__main__':
    main()
