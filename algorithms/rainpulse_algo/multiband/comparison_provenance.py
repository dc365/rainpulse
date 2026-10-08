# ruff: noqa: E501, I001
"""Versioned provenance for independently indexed S, X and joint products.

Only the current producer may make absent positional indices explicit. This is
not a migration reader for untrusted/legacy published artifacts. No winner is
renumbered, and a source table is never filtered behind its winner arrays.
"""
from __future__ import annotations
import copy
import numpy as np
CONTRACT = 'rainpulse.sx-source-v2'
PRODUCTS = {'sx_composite': None, 's_only': 'S', 'x_only': 'X'}
WINNER_FIELDS = ('WINNER_SOURCE', 'WINNER_SWEEP_NUMBER', 'WINNER_RAY', 'WINNER_GATE', 'WINNER_HEIGHT_MSL_M', 'WINNER_AGE_SECONDS', 'WINNER_RESOLUTION_M', 'WINNER_QUALITY_SCORE', 'COVERAGE_SOURCE', 'OBSERVED_MASK', 'NO_ECHO_MASK', 'UNCERTAIN_MASK', 'UNRESOLVED_MASK', 'INPUT_QUALITY_REASON', 'AVAILABLE_BAND_BITS', 'QUALIFIED_BAND_BITS', 'VALID_LAYER_COUNT', 'OBSERVED_MIN_HEIGHT_MSL_M', 'OBSERVED_MAX_HEIGHT_MSL_M')

def _integer_array(value, shape, name, minimum=-1, maximum=2 ** 31 - 1):
    a = np.asarray(value)
    if a.shape != shape or a.dtype.kind not in 'iuf' or (not np.isfinite(a).all()) or np.any(a != np.floor(a)) or np.any(a < minimum) or np.any(a > maximum):
        raise ValueError(f'invalid provenance array {name}')
    return a.astype(np.int32, copy=False)

def _index(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or (not 0 <= value < 2 ** 31):
        raise ValueError('invalid source integer: ' + name)
    return int(value)

def scope(result, band=None):
    if band not in (None, 'S', 'X'):
        raise ValueError('invalid product band')
    a, m = (result.arrays, result.metadata)
    shape = np.asarray(a['CR_DBZH']).shape
    if len(shape) != 2:
        raise ValueError('comparison requires a two-dimensional field')
    sources = copy.deepcopy(m.get('sources', []))
    if len(sources) > 4096:
        raise ValueError('comparison provenance source budget exceeded')
    indices = []
    station_bands = {}
    cuts = set()
    cuts_known = True
    for i, s in enumerate(sources):
        if not isinstance(s, dict) or s.get('band') not in ('S', 'X') or (not s.get('radar_id')) or (not s.get('scan_id')):
            raise ValueError('incomplete current-producer source identity')
        if 'index' in s and _index(s['index'], 'index') != i:
            raise ValueError('source position/index conflict; remap explicitly')
        s['index'] = i
        radar = s['radar_id']
        if not isinstance(radar, str) or len(radar) > 96:
            raise ValueError('invalid station identifier')
        if radar in station_bands and station_bands[radar] != s['band']:
            raise ValueError('station has conflicting bands')
        station_bands[radar] = s['band']
        if 'sweep_number' in s:
            _index(s['sweep_number'], 'sweep_number')
            s['source_granularity'] = 'native_cut'
        else:
            s['source_granularity'] = 'native_volume'
        if band is not None and s['band'] != band:
            continue
        indices.append(i)
        if 'sweep_number' in s:
            cuts.add((radar, s['scan_id'], s['sweep_number']))
        elif 'sweep_numbers' in s:
            for n in s['sweep_numbers']:
                cuts.add((radar, s['scan_id'], _index(n, 'sweep_numbers')))
        else:
            cuts_known = False
    stations = [dict(index=i, radar_id=r, band=station_bands[r]) for i, r in enumerate(sorted(station_bands))]
    winner = _integer_array(a.get('WINNER_SOURCE', np.full(shape, -1)), shape, 'WINNER_SOURCE')
    echo = np.isfinite(a['CR_DBZH'])
    if np.any(echo & ((winner < 0) | (winner >= len(sources)))):
        raise ValueError('finite echo has no valid current source')
    if np.any(winner >= len(sources)):
        raise ValueError('winner outside source table')
    used = sorted(map(int, np.unique(winner[echo])))
    if any((i not in indices for i in used)):
        raise ValueError('product winner belongs to another band')
    for name in ('WINNER_RAY', 'WINNER_GATE'):
        v = _integer_array(a.get(name, np.full(shape, -1)), shape, name)
        if np.any(echo & (v < 0)):
            raise ValueError('echo winner has missing native coordinates')
    native_sweep = _integer_array(a['WINNER_SWEEP_NUMBER'], shape, 'WINNER_SWEEP_NUMBER') if 'WINNER_SWEEP_NUMBER' in a else None
    for i in used:
        s = sources[i]
        selected = echo & (winner == i)
        if native_sweep is None:
            if s['source_granularity'] != 'native_cut':
                raise ValueError('volume source requires native winner sweep array')
        elif np.any(native_sweep[selected] < 0):
            raise ValueError('missing winner sweep')
        elif 'sweep_number' in s and np.any(native_sweep[selected] != s['sweep_number']):
            raise ValueError('winner sweep differs from cut source')
        elif 'sweep_numbers' in s and (not np.isin(native_sweep[selected], s['sweep_numbers']).all()):
            raise ValueError('winner sweep not in current input volume')
    requested = m.get('requested_radars')
    if band is not None:
        by_band = m.get('requested_radars_by_band', {})
        requested = by_band.get(band)
    if requested is not None:
        if not isinstance(requested, (list, tuple)) or not all((isinstance(r, str) and r for r in requested)):
            raise ValueError('invalid requested radar list')
        requested = sorted(set(requested))
    ledger = m.get('fusion_audit', {}).get('sources')
    native_qualified = spatial_qualified = None
    if isinstance(ledger, list):
        by_index = {int(x['index']): x for x in ledger}
        native_qualified = sorted({sources[i]['radar_id'] for i in indices if by_index.get(i, {}).get('native', {}).get('qualified', 0) > 0})
        spatial_qualified = sorted({sources[i]['radar_id'] for i in indices if by_index.get(i, {}).get('projected', {}).get('qualified', 0) > 0})
    return dict(contract=CONTRACT, sources=sources, source_indices=indices, stations=stations, winner_source_indices=used, requested_radars=requested, counts=dict(requested_station_count=None if requested is None else len(requested), input_station_count=len({sources[i]['radar_id'] for i in indices}), source_entry_count=len(indices), source_cut_count=len(cuts) if cuts_known else None, native_qualified_station_count=None if native_qualified is None else len(native_qualified), qualified_station_count=None if spatial_qualified is None else len(spatial_qualified), winner_station_count=len({sources[i]['radar_id'] for i in used})), count_semantics='qualified=at_least_one_admitted_projected_sample; winner=finite_column_echo')

def native_sweep_array(result, product_scope):
    a = result.arrays
    if 'WINNER_SWEEP_NUMBER' in a:
        return np.asarray(a['WINNER_SWEEP_NUMBER'])
    win = np.asarray(a['WINNER_SOURCE'])
    out = np.full(win.shape, -1, np.int32)
    for s in product_scope['sources']:
        if s.get('source_granularity') == 'native_cut':
            out[win == s['index']] = s['sweep_number']
    return out

def station_arrays(result, product_scope):
    a = result.arrays
    win, echo = (np.asarray(a['WINNER_SOURCE']), np.isfinite(a['CR_DBZH']))
    station_index = np.full(win.shape, -1, np.int32)
    bands = np.zeros(win.shape, np.uint8)
    by_id = {s['radar_id']: s['index'] for s in product_scope['stations']}
    for s in product_scope['sources']:
        mask = echo & (win == s['index'])
        station_index[mask] = by_id[s['radar_id']]
        bands[mask] = 1 if s['band'] == 'S' else 2
    return (station_index, bands)

def comparison_arrays(result, band_results):
    arrays = dict(result.arrays)
    scopes = {'sx_composite': scope(result)}
    for band in ('S', 'X'):
        component = band_results.get(band)
        if component is None:
            continue
        key = band.lower() + '_only'
        scopes[key] = scope(component, band)
        arrays['CR_DBZH_' + band + '_ONLY'] = component.arrays['CR_DBZH']
        for name in WINNER_FIELDS:
            if name in component.arrays:
                arrays[name + '_' + band + '_ONLY'] = component.arrays[name]
        arrays['WINNER_SWEEP_NUMBER_' + band + '_ONLY'] = native_sweep_array(component, scopes[key])
        site, bits = station_arrays(component, scopes[key])
        arrays['WINNER_STATION_INDEX_' + band + '_ONLY'] = site
        arrays['WINNER_BAND_' + band + '_ONLY'] = bits
    arrays['WINNER_SWEEP_NUMBER'] = native_sweep_array(result, scopes['sx_composite'])
    arrays['WINNER_STATION_INDEX'], arrays['WINNER_BAND'] = station_arrays(result, scopes['sx_composite'])
    arrays['WINNER_SITE'] = np.where(arrays['WINNER_STATION_INDEX'] >= 0, arrays['WINNER_STATION_INDEX'], np.nan)
    return (arrays, scopes)
