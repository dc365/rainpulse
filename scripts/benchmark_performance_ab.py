#!/usr/bin/env python3
"""Reproducible synthetic A/B kernel timings; NOT a full Worker/site benchmark."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import platform
import statistics
import time
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy.ndimage import median_filter, minimum_filter1d

from rainpulse_algo.multiband.candidate_kernel import nonmet_candidate
from rainpulse_algo.multiband.preview_sampling import prepare_polar_sampling, render_polar_sampling
from rainpulse_algo.radar.qc_engine.group_validation import validate_seed_objects

BASE = 'c5c7b47b205e6a487d9e181c896dde59d937184e'


def reference():
    path = Path(__file__).resolve().parents[1] / 'algorithms/tests/performance_ab_20260926/references/legacy.py'
    spec = importlib.util.spec_from_file_location('performance_ab_reference', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def paired(before, after, count):
    before(); after()
    times = {'before': [], 'after': []}
    for index in range(count):
        order = [('before', before), ('after', after)]
        if index % 2:
            order.reverse()
        for key, fn in order:
            gc.collect()
            started = time.perf_counter()
            fn()
            times[key].append((time.perf_counter() - started) * 1000)
    return {'milliseconds': times,
            'median_ms': {k: statistics.median(v) for k, v in times.items()}}


def old_candidate(f, echo, snr, cfg, ranges):
    dr = float(np.median(np.diff(ranges)))
    n = min(501, max(3, int(round(cfg.phase_window_m/dr))))
    n += n % 2 == 0
    measured = np.where(echo, f['DBZH'], np.nan)
    med = median_filter(np.where(echo, measured, 0.), size=(1, n), mode='nearest')
    supported = minimum_filter1d(echo.astype(np.uint8), size=n, axis=1, mode='nearest') == 1
    return (echo & supported & snr & np.isfinite(f['RHOHV'])
            & (f['RHOHV'] < cfg.rho_candidate_max)
            & (np.abs(measured - med) > cfg.texture_candidate_db)
            & ~f['WEATHER_PROTECTED_MASK'].astype(bool))


def run(repeats):
    legacy = reference()
    rng = np.random.default_rng(926271)
    labels = rng.integers(1, 1501, (360, 2048), dtype=np.int32)
    core = rng.random(labels.shape) < .5
    prop = ~core
    counts = np.bincount(labels.ravel())
    seeds = np.bincount(labels[core], minlength=len(counts))
    sizes = counts[labels].astype('u4')
    fractions = np.divide(seeds, counts, out=np.zeros_like(counts, dtype=float), where=counts > 0)[labels].astype('f4')
    a = lambda: legacy.loop_validate(labels, sizes, fractions, core, prop)
    b = lambda: validate_seed_objects(labels, sizes, fractions, core, prop,
          maximum_object_gates=5000, minimum_seed_gates=3, minimum_seed_fraction=.3)
    a(); b()
    grouped = {**paired(a, b, repeats), 'gates': labels.size, 'objects': 1500,
               'checks_passed': True, 'scope': 'count/fraction checks only; not full validator'}

    shape = (360, 2000)
    z = rng.uniform(-15, 50, shape).astype('f4')
    echo = rng.random(shape) > .001
    z[~echo] = np.nan
    snr = np.ones(shape, bool)
    cfg = SimpleNamespace(phase_window_m=975., rho_candidate_max=.75, texture_candidate_db=8.)
    ranges = np.arange(shape[1])*75. + 37.5
    candidates = []
    for active in (0, 36, 360):
        rho = np.full(shape, .99, 'f4'); rho[:active] = .7
        f = {'DBZH': z, 'RHOHV': rho, 'WEATHER_PROTECTED_MASK': np.zeros(shape, 'u1')}
        old = lambda: old_candidate(f, echo, snr, cfg, ranges)
        new = lambda: nonmet_candidate(f, echo, snr, cfg, ranges)
        np.testing.assert_array_equal(old(), new())
        candidates.append({**paired(old, new, repeats), 'active_rays': active,
                           'total_rays': 360, 'equal_mask': True,
                           'scope': 'X candidate only; no phase, decoding, QC output or I/O'})

    az = np.arange(360.)
    el = np.full(360, .5)
    ranges = np.arange(2000.)*75. + 37.5
    action = rng.integers(0, 4, shape, dtype=np.uint8)
    qc = z.copy(); qc[action == 2] = np.nan
    sampling = legacy.map_sampling(az, el, ranges, 720)
    def old_images():
        result = []
        for mapping in (None, sampling):
            for field, actions in ((z, None), (qc, None), (z, action)):
                result.append(legacy.polar_quicklook(az, ranges, field, size=720,
                       map_sampling=mapping, elevation_deg=el, actions=actions))
        return result
    def new_images():
        result = []
        for mapping in (None, sampling):
            plan = prepare_polar_sampling(az, ranges, size=720,
                                         map_sampling=mapping, elevation_deg=el)
            for field, actions in ((z, None), (qc, None), (z, action)):
                result.append(render_polar_sampling(plan, field, legacy.LEVELS,
                                  legacy.COLORS, legacy.png, actions=actions))
        return result
    before, after = old_images(), new_images()
    assert before == after
    images = {**paired(old_images, new_images, repeats), 'images': 6, 'size': [720,720],
              'png_bytes_equal': True, 'png_sha256': [hashlib.sha256(x).hexdigest() for x in after],
              'scope': 'two sampling plans + six colors/PNG encodes; excludes GEOD grid, QC and I/O'}
    return {'base_commit': BASE, 'synthetic': True, 'real_site_test': False,
            'repetitions': repeats, 'warmup_runs': 1, 'group_validation': grouped,
            'x_candidate': candidates, 'six_png': images}


def installed(name):
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return 'not-installed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10 or args.output.exists():
        parser.error('repeats must be 1..10 and output must not already exist')
    old_affinity = os.sched_getaffinity(0) if hasattr(os, 'sched_getaffinity') else None
    try:
        if old_affinity:
            os.sched_setaffinity(0, {min(old_affinity)})
        result = run(args.repeats)
        result['runtime'] = {'python': platform.python_version(),
            'libraries': {k: installed(k) for k in ('numpy','scipy','numba','pyproj')},
            'cpu_affinity_count': len(os.sched_getaffinity(0)) if old_affinity else None,
            'telemetry': 'no trace root active in timed sections'}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as f:
            json.dump(result, f, indent=2, allow_nan=False)
        print(json.dumps(result, indent=2))
    finally:
        if old_affinity:
            os.sched_setaffinity(0, old_affinity)


if __name__ == '__main__':
    main()
