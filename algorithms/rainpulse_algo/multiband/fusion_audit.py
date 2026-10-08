# ruff: noqa: E501, I001
"""Bounded, task-local read-only attribution, separate from meteorological QC.

Native gate counts, projected opportunities, source events and sampled pairs
have distinct units. This module never supplies an admission mask or a value.
"""
from __future__ import annotations
from collections import Counter
from contextvars import ContextVar
from functools import wraps
import hashlib
import json
import numpy as np
CONTRACT = 'rainpulse.sx-audit-v1'
MAX_SOURCES = 4096
MAX_PAIRS = 4096
_CURRENT = ContextVar('rainpulse_sx_fusion_audit', default=None)
_REQUEST = ContextVar('rainpulse_sx_requested_scope', default=None)
NATIVE_REASONS = ('missing', 'confirmed_rejected', 'withheld', 'calibration_unqualified', 'path_unqualified', 'radome_unqualified', 'invalid_measurement', 'other_inadmissible', 'qualified')
PROJECTED_REASONS = ('geometry_or_age_unsupported', 'height_unrepresented', 'unobserved', 'inadmissible', 'nonpositive_score', 'qualified')

def audited(function):

    @wraps(function)
    def call(*args, **kwargs):
        token = _CURRENT.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _CURRENT.reset(token)
    return call

def request_context(function):

    @wraps(function)
    def call(executor, request, *args, **kwargs):
        payload = request.get('payload', {})
        requested = payload.get('requested_radars')
        scope = None
        if requested is not None:
            if not isinstance(requested, list) or len(requested) > 32 or any((r not in executor.network.stations for r in requested)):
                raise ValueError('invalid requested source scope')
            scope = {'requested_radars': list(requested), 'requested_radars_by_band': {band: [r for r in requested if executor.network.stations[r].band == band] for band in ('S', 'X')}}
        token = _REQUEST.set(scope)
        try:
            return function(executor, request, *args, **kwargs)
        finally:
            _REQUEST.reset(token)
    return call

def _mask(fields, key, shape):
    a = fields.get(key)
    if a is None:
        return np.zeros(shape, bool)
    a = np.asarray(a)
    if a.shape != shape or not np.isin(a, (0, 1)).all():
        raise ValueError('audit received malformed existing mask: ' + key)
    return a == 1

def _partition(total, candidates):
    remaining = np.array(total, dtype=bool, copy=True)
    counts = {}
    for name, condition in candidates:
        chosen = remaining & condition
        counts[name] = int(np.count_nonzero(chosen))
        remaining &= ~chosen
    if np.any(remaining):
        raise ValueError('incomplete audit partition')
    return counts

class Collector:

    def __init__(self, out, grid):
        self.out = out
        self.grid = grid
        self.records = {}
        self.seen = set()
        self.layer_wins = Counter()
        self.layer_echo_wins = Counter()
        self.mode = grid.method
        self.grid_key = hashlib.sha256(json.dumps([grid.grid_id, grid.crs, grid.west_m, grid.south_m, grid.spacing_m, grid.width, grid.height, list(grid.levels_m_msl)], separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        total = grid.width * grid.height * len(grid.levels_m_msl)
        self.slots = np.unique(np.linspace(0, total - 1, min(MAX_PAIRS, total), dtype=np.int64))
        self.samples = {'grid_key': self.grid_key, 'slots': self.slots, 'mode': self.mode}
        for name in ('source', 'ray', 'gate'):
            self.samples[name] = np.full(len(self.slots), -1, np.int32)
        for name in ('value', 'height', 'age', 'resolution', 'score'):
            self.samples[name] = np.full(len(self.slots), np.nan, np.float64)
        self.samples['levels_m_msl'] = list(grid.levels_m_msl)
        self.samples['width'] = grid.width
        self.samples['height_cells'] = grid.height

    def record(self, index):
        if index not in self.records:
            if len(self.records) >= MAX_SOURCES:
                raise ValueError('fusion audit source budget exceeded')
            self.records[index] = dict(index=int(index), sweeps=[], native={n: 0 for n in NATIVE_REASONS}, native_total=0, multi_reason_counts={}, projected={n: 0 for n in PROJECTED_REASONS}, projected_total=0, admitted_no_echo_samples=0)
        return self.records[index]

    def native(self, sweep, station, index, admitted=None):
        key = (int(index), int(sweep.number))
        if key in self.seen:
            return
        self.seen.add(key)
        f = sweep.fields
        shape = f['DBZH'].shape
        observed = _mask(f, 'OBSERVED_MASK', shape)
        noecho = _mask(f, 'NO_ECHO_MASK', shape)
        if admitted is None:
            field = 'FUSION_ELIGIBLE_MASK' if self.mode == 'quality_height_v2' else 'REFLECTIVITY_ELIGIBLE_FOR_CR'
            q = np.asarray(f['QUALITY_SCORE'])
            value = np.asarray(f['DBZH_QC'])
            admitted = observed & _mask(f, field, shape) & (noecho | np.isfinite(value)) & np.isfinite(q) & (q > 0)
        else:
            admitted = np.asarray(admitted, dtype=bool)
            if admitted.shape != shape:
                raise ValueError('native audit/admission shape differs')
        reason = np.asarray(f.get('FUSION_INPUT_REASON', np.zeros(shape, np.uint16)))
        action = np.asarray(f.get('QC_ACTION', np.zeros(shape, np.uint8)))
        if reason.shape != shape or action.shape != shape:
            raise ValueError('native audit cause shape differs')
        hard = _mask(f, 'CONFIRMED_NONMET_MASK', shape) | (action == 2)
        withheld = action == 3
        for n in ('CR_WITHHELD_MASK', 'XQC_WITHHELD_MASK', 'XQC_BUDGET_WITHHELD_MASK'):
            withheld |= _mask(f, n, shape)
        not_admitted = ~admitted
        candidates = [('missing', ~observed), ('confirmed_rejected', not_admitted & hard), ('withheld', not_admitted & withheld), ('calibration_unqualified', not_admitted & (reason & 4 != 0)), ('path_unqualified', not_admitted & (reason & (1 | 32) != 0)), ('radome_unqualified', not_admitted & (reason & 2 != 0)), ('invalid_measurement', not_admitted & (reason & 16 != 0)), ('other_inadmissible', not_admitted), ('qualified', admitted)]
        counts = _partition(np.ones(shape, bool), candidates)
        row = self.record(index)
        row.update(radar_id=station.radar_id, band=station.band)
        row['sweeps'].append(int(sweep.number))
        if np.any(admitted & ~observed):
            raise ValueError('purpose admission includes an unobserved gate')
        row['admitted_with_uncertainty_diagnostics'] = row.get('admitted_with_uncertainty_diagnostics', 0) + int(np.count_nonzero(admitted & (withheld | (reason != 0))))
        row['explicit_path_shadow_gates'] = row.get('explicit_path_shadow_gates', 0) + int(np.count_nonzero(_mask(f, 'ATTENUATION_UNRELIABLE_MASK', shape) & ~observed))
        row['native_total'] += int(np.prod(shape))
        for k, v in counts.items():
            row['native'][k] += v
        for bit in (1, 2, 4, 8, 16, 32):
            key = str(bit)
            row['multi_reason_counts'][key] = row['multi_reason_counts'].get(key, 0) + int(np.count_nonzero(reason & bit))

    def projected(self, index, horizontal, represented, observed, eligible, admitted, noecho):
        total = np.ones(admitted.shape, bool)
        candidates = [('geometry_or_age_unsupported', ~horizontal), ('height_unrepresented', ~represented), ('unobserved', ~observed), ('inadmissible', ~eligible), ('nonpositive_score', ~admitted), ('qualified', admitted)]
        counts = _partition(total, candidates)
        row = self.record(index)
        row['projected_total'] += int(admitted.size)
        for k, v in counts.items():
            row['projected'][k] += v
        row['admitted_no_echo_samples'] += int(np.count_nonzero(admitted & noecho))

    def layers(self, layers, sl):
        score, values, winner, ray, gate, height, age, resolution = layers
        for idx, n in zip(*np.unique(winner[winner >= 0], return_counts=True), strict=True):
            self.layer_wins[int(idx)] += int(n)
        selected = (winner >= 0) & np.isfinite(values)
        for idx, n in zip(*np.unique(winner[selected], return_counts=True), strict=True):
            self.layer_echo_wins[int(idx)] += int(n)
        row_slice = sl[0] if isinstance(sl, tuple) else sl
        start, stop = (row_slice.start or 0, row_slice.stop or self.grid.height)
        plane = self.grid.width * self.grid.height
        level = self.slots // plane
        row = self.slots % plane // self.grid.width
        col = self.slots % self.grid.width
        good = (row >= start) & (row < stop)
        local = (level[good], row[good] - start, col[good])
        for name, source in (('value', values), ('source', winner), ('ray', ray), ('gate', gate), ('height', height), ('age', age), ('resolution', resolution), ('score', score)):
            self.samples[name][good] = source[local]

    def finish(self, result):
        rows = []
        winner = np.asarray(result.arrays['WINNER_SOURCE'])
        echo = np.isfinite(result.arrays['CR_DBZH'])
        for idx in sorted(self.records):
            row = self.records[idx]
            if sum(row['native'].values()) != row['native_total'] or sum(row['projected'].values()) != row['projected_total']:
                raise ValueError('fusion audit partition does not close')
            row['column_echo_wins'] = int(np.count_nonzero(echo & (winner == idx)))
            if self.mode != 'experimental_horizontal_max':
                row['same_height_wins'] = self.layer_wins[idx]
                row['same_height_echo_wins'] = self.layer_echo_wins[idx]
                row['not_selected_at_same_height'] = row['projected']['qualified'] - self.layer_wins[idx]
                row['echo_not_selected_by_vertical_max'] = self.layer_echo_wins[idx] - row['column_echo_wins']
                if min(row['not_selected_at_same_height'], row['echo_not_selected_by_vertical_max']) < 0:
                    raise ValueError('fusion winner accounting does not close')
            else:
                row['same_height_wins'] = None
                row['not_selected_at_same_height'] = None
                row['echo_not_selected_by_vertical_max'] = None
            rows.append(row)
        result.metadata['fusion_audit'] = dict(contract=CONTRACT, method=self.mode, status='collected', sources=rows, source_events=list(result.metadata.get('skipped', [])), native_unit='native_ray_gate_per_input_cut', projected_unit='source_cut_grid_cell_configured_height_opportunity', geometry_age_partition='combined_by_existing_footprint_contract', not_meteorological_truth=True)
        result.audit_samples = self.samples
        return result

def _collector(out, grid=None):
    current = _CURRENT.get()
    if current is None:
        return None
    key = id(out)
    if key not in current:
        if grid is None:
            return None
        current[key] = Collector(out, grid)
    return current[key]

def trace_source(out, sweep, station, index, grid, *, admitted=None):
    c = _collector(out, grid)
    if c is not None:
        c.native(sweep, station, index, admitted)

def trace_samples(out, index, horizontal, represented, observed, eligible, admitted, noecho):
    c = _collector(out)
    if c is not None:
        c.projected(index, horizontal, represented, observed, eligible, admitted, noecho)

def trace_layers(out, layers, sl, grid):
    c = _collector(out, grid)
    if c is not None:
        c.layers(layers, sl)

def complete(result):
    request = _REQUEST.get()
    if request is not None:
        result.metadata.update(requested_radars=list(request['requested_radars']), requested_radars_by_band={k: list(v) for k, v in request['requested_radars_by_band'].items()})
    c = _collector(result.arrays)
    if c is not None:
        return c.finish(result)
    return result

def paired_diagnostic(s, x):
    if s is None or x is None:
        return dict(status='missing_band_or_not_collected', population='not_evaluated')
    if s['mode'] == 'experimental_horizontal_max' or x['mode'] == 'experimental_horizontal_max':
        return dict(status='not_height_aligned', population='not_evaluated')
    if s['grid_key'] != x['grid_key'] or not np.array_equal(s['slots'], x['slots']):
        raise ValueError('paired diagnostics require identical frozen sample lattice')
    both = (s['source'] >= 0) & (x['source'] >= 0)
    time_valid = np.isfinite(s['age']) & np.isfinite(x['age'])
    time_difference = np.subtract(s['age'], x['age'], out=np.full(s['age'].shape, np.inf), where=time_valid)
    time = time_valid & (abs(time_difference) <= 60)
    height = np.isfinite(s['height']) & np.isfinite(x['height']) & (abs(s['height'] - x['height']) <= 500)
    resolution = np.isfinite(s['resolution']) & np.isfinite(x['resolution']) & (s['resolution'] > 0) & (x['resolution'] > 0) & (np.maximum(s['resolution'], x['resolution']) <= 2 * np.minimum(s['resolution'], x['resolution']))
    counts = _partition(np.ones(both.shape, bool), [('missing_band_winner', ~both), ('time_incomparable', ~time), ('height_incomparable', ~height), ('resolution_incomparable', ~resolution), ('comparable', np.ones(both.shape, bool))])
    comparable = both & time & height & resolution
    secho, xecho = (np.isfinite(s['value']), np.isfinite(x['value']))
    echoes = comparable & secho & xecho
    delta = x['value'][echoes] - s['value'][echoes]
    example_indices = np.flatnonzero(comparable & (secho != xecho))[:16]
    examples = [dict(slot=int(s['slots'][i]), s_source=int(s['source'][i]), x_source=int(x['source'][i]), s_ray=int(s['ray'][i]), x_ray=int(x['ray'][i]), s_gate=int(s['gate'][i]), x_gate=int(x['gate'][i]), s_echo=bool(secho[i]), x_echo=bool(xecho[i])) for i in example_indices]
    return dict(status='sampled', population='deterministic_echo_independent_configured_level_grid_slots', sample_count=int(len(s['slots'])), sample_limit=MAX_PAIRS, grid_key=s['grid_key'], sample_sha256=hashlib.sha256(s['slots'].astype('<i8').tobytes()).hexdigest(), primary_partition=counts, comparable_echo_pairs=int(echoes.sum()), echo_noecho_conflicts=int(np.count_nonzero(comparable & (secho != xecho))), both_noecho=int(np.count_nonzero(comparable & ~secho & ~xecho)), x_minus_s_mean_db=None if not len(delta) else float(delta.mean()), x_minus_s_min_db=None if not len(delta) else float(delta.min()), x_minus_s_max_db=None if not len(delta) else float(delta.max()), conflict_examples=examples, limits=dict(age_difference_seconds=60, center_height_difference_m=500, resolution_ratio=2), interpretation='same configured level plus diagnostic screens; not equal-volume truth or calibration')
