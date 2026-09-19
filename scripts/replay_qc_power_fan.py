#!/usr/bin/env python3
"""Read-only native-gate fan replay and full-volume composite comparison.

This does not publish QC or diagnostic products. Inputs must be QC Zarr URIs
whose groups contain DBZH_RAW, geometry and v2 eligibility diagnostics.
"""
import argparse
import json
from pathlib import Path

import numpy as np
import yaml

from rainpulse_algo.diagnostics.composite import composite_reflectivity
from rainpulse_algo.diagnostics.renderer import _open_group, BUSINESS_HARD_REJECT_FLAG_NAMES
from rainpulse_algo.radar.qc_engine.adapters import adapt_sweep
from rainpulse_algo.radar.qc_engine.profile import load_open_source_profile
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.geometry import numeric_plateaus
from rainpulse_algo.radar.qc_engine.review_extension.radial_revision.power_fan import detect_power_fans
from rainpulse_algo.worker.object_store import ArtifactObjectReader, minio_client_from_environment


class Overlay:
    def __init__(self, source, updates):
        self.source, self.updates, self.attrs = source, updates, source.attrs

    def __getitem__(self, key):
        return self.updates[key] if key in self.updates else self.source[key]

    def __contains__(self, key):
        return key in self.updates or key in self.source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qc-uri', action='append', required=True)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--flag-definitions', default='configs/qc/flag-definitions-v2.yaml')
    parser.add_argument('--radar-config-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    profile = load_open_source_profile(args.profile, args.flag_definitions)
    flag_doc = yaml.safe_load(Path(args.flag_definitions).read_text())
    reject = sum(f['mask'] for f in flag_doc['flags'] if f['name'] in BUSINESS_HARD_REJECT_FLAG_NAMES)
    reader = ArtifactObjectReader(minio_client_from_environment())
    roots = [_open_group(reader.load(uri)) for uri in args.qc_uri]
    sites = {}
    for root in roots:
        site = root.attrs['radar_id']
        sites[site] = yaml.safe_load((Path(args.radar_config_dir)/(site+'.yaml')).read_text())['site']
    before, bounds = composite_reflectivity(roots, reject, sites=sites)
    updated, counts = [], {}
    for root in roots:
        updates = {}
        counts[root.attrs['radar_id']] = {}
        for n in root['sweep_number'][:]:
            name = f'sweep_{int(n):03d}'
            g = root[name]
            native = adapt_sweep(Overlay(root, {name: Overlay(g, {'DBZH': g['DBZH_RAW']})}), name, profile)
            blocked = np.zeros(native.shape, bool)
            for key in ('WEATHER_SUPPORTED_MASK', 'NP_WEATHER_PROTECTED_MASK',
                        'SRC_REVIEW_WEATHER_PROTECTED_MASK', 'SRC_REVIEW_CONFLICT_MASK'):
                if key in g:
                    blocked |= g[key][:][native.original_indices] == 1
            blocked |= numeric_plateaus(native.fields['DBZH'], native.field_available['DBZH'], native.ranges)
            evidence = detect_power_fans(native, blocked)
            hit = native.restore(evidence['RV2_POWER_FAN_MASK']) == 1
            eligible = g['QPE_ELIGIBLE_MASK'][:].copy()
            counts[root.attrs['radar_id']][str(int(n))] = int((hit & (eligible == 1)).sum())
            eligible[hit] = 0
            updates[name] = Overlay(g, {'QPE_ELIGIBLE_MASK': eligible})
        updated.append(Overlay(root, updates))
    after, after_bounds = composite_reflectivity(updated, reject, sites=sites)
    if bounds != after_bounds:
        raise ValueError('comparison grid changed')
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(dest, before=before, after=after, bounds=bounds)
    summary = {'profile_version': profile.profile_version, 'published': False,
               'validation': 'incremental native-mask diagnostic replay, not full QC worker',
               'newly_isolated_eligible_gates': counts}
    dest.with_suffix('.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
