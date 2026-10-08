# ruff: noqa: E501, I001
"""Single-encode S/X comparison bundle with truthful scopes and native probes.

Reuses the existing palette, PNG codec, probe format and meteorological results.
No diagnostic field is fed back into QC or source selection.
"""
from __future__ import annotations
import hashlib
import json
import numpy as np
from .comparison_provenance import CONTRACT, WINNER_FIELDS, comparison_arrays
from .composite_sampling import batch, sample
from .fusion_audit import paired_diagnostic
METHODS = {'experimental_horizontal_max_v1': ('experimental_horizontal_max', 'S+X 水平最大值试验 · 非等高/未标定'), 'quality_select_at_height_then_vertical_max_v1': ('quality_height', '同高度质量选源后垂直最大值 · 基线候选'), 'quality_select_at_height_then_vertical_max_v2': ('quality_height_v2', '同高度质量选源后垂直最大值 · 严格资格候选')}
DIFF_LEGEND = [dict(minimum=v, label=str(v), color=c) for v, c in zip((-20, -15, -10, -5, 0, 5, 10, 15, 20), ('#1f5bab', '#4084c8', '#7eb7dd', '#c4deeb', '#f6f6ef', '#f5cdaa', '#e08969', '#c04c3f', '#8e1f32'), strict=True)]
SITE_COLORS = ('#147d92', '#b469cc', '#d09a32', '#4c9957', '#ce6161', '#5979c2')

def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def _blob(objects, key, value):
    data = _json(value)
    if len(data) > 8 * 1024 ** 2:
        raise ValueError('S/X diagnostic JSON exceeds its 8 MiB limit')
    objects[key] = data
    return dict(object_path=key, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))

def _summary(component):
    if component is None:
        return dict(valid_echo_cells=0, valid_no_echo_cells=0, uncertain_only_cells=0)
    a = component.arrays
    finite = np.isfinite(a['CR_DBZH'])
    observed = np.asarray(a.get('OBSERVED_MASK', np.zeros(finite.shape)), bool)
    noecho = np.asarray(a.get('NO_ECHO_MASK', np.zeros(finite.shape)), bool)
    uncertain = np.asarray(a.get('UNCERTAIN_MASK', np.zeros(finite.shape)), bool)
    if np.any(noecho & (~observed | finite | uncertain)):
        raise ValueError('invalid existing clear/missing/echo product states')
    return dict(valid_echo_cells=int(finite.sum()), valid_no_echo_cells=int(noecho.sum()), uncertain_only_cells=int((~finite & uncertain).sum()))

def _state(counts, present):
    if not present:
        return 'no_inputs'
    if counts['valid_echo_cells']:
        return 'available'
    if counts['valid_no_echo_cells']:
        return 'no_echo'
    return 'no_qualified_echo'

@batch
def build(result, band_results, *, render_api=None, probe_writer=None):
    if render_api is None:
        from . import product as render_api
    if probe_writer is None:
        from rainpulse_algo.diagnostics.radar_probe import attach_probe
        probe_writer = attach_probe
    p = render_api
    arrays, scopes = comparison_arrays(result, band_results)
    m = dict(result.metadata)
    method, method_label = METHODS.get(m.get('method'), (m.get('method', 'unknown'), '方法身份待核验'))
    objects = {'cr.png': p.quicklook(arrays['CR_DBZH']), 'uncertain.png': p.quicklook(arrays['CR_UNCERTAIN_DBZH'])}
    products, layers = ([], [dict(object_path='cr.png', title=method_label, field='CR_DBZH'), dict(object_path='uncertain.png', title='未定回波（不替代合格产品）', field='CR_UNCERTAIN_DBZH')])
    components = {'s_only': band_results.get('S'), 'x_only': band_results.get('X'), 'sx_composite': result}
    for pid, band, label in (('s_only', 'S', 'S 单独'), ('x_only', 'X', 'X 单独'), ('sx_composite', 'S+X', method_label)):
        component = components[pid]
        counts = _summary(component)
        field = 'CR_DBZH' if pid == 'sx_composite' else 'CR_DBZH_' + band + '_ONLY'
        if field not in arrays:
            arrays[field] = np.full(arrays['CR_DBZH'].shape, np.nan, np.float32)
        path = f'comparison/{pid}.png'
        entry = dict(product_id=pid, band=band, label=label, status=_state(counts, component is not None), object_path=path if component is not None else None, **counts, unit='dBZ', method=method, method_label=method_label, operational_eligible=bool(m.get('operational_eligible', False)), qpe_eligible=bool(m.get('qpe_eligible', False)))
        if component is not None:
            objects[path] = p.quicklook(arrays[field])
            layers.append(dict(object_path=path, title=label, field=field, band=band))
            entry['provenance'] = scopes[pid]
            entry['sources'] = scopes[pid]['sources']
            entry['source_indices'] = scopes[pid]['source_indices']
            entry['source_count'] = scopes[pid]['counts']['source_entry_count']
            entry['source_count_semantics'] = 'source_entries_not_stations'
            entry.update(scopes[pid]['counts'])
            entry['skipped'] = component.metadata.get('skipped', [])
            entry['provenance']['sources_sha256'] = hashlib.sha256(_json(entry['sources'])).hexdigest()
            entry['reason'] = None if counts['valid_echo_cells'] else '仅有效无回波覆盖；未定单独保留' if counts['valid_no_echo_cells'] else '有输入但无本方法合格回波；不表示无雨'
            used = scopes[pid]['winner_source_indices']
            entry['echo_contributing_bands'] = sorted({entry['sources'][i]['band'] for i in used})
        else:
            entry.update(sources=[], source_indices=[], source_count=0, input_station_count=0, source_entry_count=0, source_cut_count=0, native_qualified_station_count=0, qualified_station_count=0, winner_station_count=0, requested_station_count=len(set(m['requested_radars_by_band'][band])) if band in m.get('requested_radars_by_band', {}) else None, echo_contributing_bands=[], reason='本时次没有该波段输入')
        entry['contributing_bands'] = [band] if band != 'S+X' and (counts['valid_echo_cells'] or counts['valid_no_echo_cells']) else []
        products.append(entry)
    by_id = {x['product_id']: x for x in products}
    joint = by_id['sx_composite']
    joint['contributing_bands'] = [b for b in ('S', 'X') if by_id[b.lower() + '_only']['contributing_bands']]
    if len(joint['contributing_bands']) == 1:
        joint['label'] = method_label + ' · 仅' + joint['contributing_bands'][0] + '贡献'
        absent = 'X' if joint['contributing_bands'][0] == 'S' else 'S'
        joint['reason'] = absent + ' 波段没有合格贡献；未形成双波段融合。' + (joint['reason'] or '')
    elif not joint['contributing_bands']:
        joint['label'] = method_label + ' · 无合格贡献'
    elif len(joint['echo_contributing_bands']) == 1 and joint['valid_echo_cells']:
        joint['label'] += ' · 最终回波来自 ' + joint['echo_contributing_bands'][0]
        joint['reason'] = '最终组合的有效回波来自 ' + joint['echo_contributing_bands'][0]
    if not joint['valid_echo_cells'] and joint['valid_no_echo_cells']:
        joint['label'] += '（仅有效无回波像元）'
        joint['reason'] = (joint['reason'] or '') + '；最终组合含 ' + str(joint['valid_no_echo_cells']) + ' 个有效无回波像元'
    s_values, x_values = (arrays['CR_DBZH_S_ONLY'], arrays['CR_DBZH_X_ONLY'])
    both = np.isfinite(s_values) & np.isfinite(x_values)
    difference = np.full(s_values.shape, np.nan, np.float32)
    difference[both] = x_values[both] - s_values[both]
    arrays['DBZH_X_MINUS_S'] = difference
    path = 'comparison/x_minus_s.png'
    objects[path] = p.difference_quicklook(difference)
    products.append(dict(product_id='x_minus_s', band='X-S', label='X-only CR − S-only CR', status='available' if both.any() else 'no_overlap', object_path=path, valid_cells=int(both.sum()), unit='dBZ', reason='二维产品差；未匹配高度/采集时刻，不是标定误差'))
    layers.append(dict(object_path=path, title='X-only CR − S-only CR', field='DBZH_X_MINUS_S'))
    joint_value = arrays['CR_DBZH']
    overlap = np.isfinite(joint_value) & np.isfinite(s_values)
    arrays['DBZH_SX_MINUS_S'] = np.where(overlap, joint_value - s_values, np.nan)
    zero = np.zeros(joint_value.shape, bool)
    so = np.asarray(band_results['S'].arrays['OBSERVED_MASK'], bool) if band_results.get('S') is not None else zero
    xo = np.asarray(band_results['X'].arrays['OBSERVED_MASK'], bool) if band_results.get('X') is not None else zero
    arrays['X_ADDED_COVERAGE'] = np.where(xo & ~so, 1.0, np.nan)
    arrays['X_ADDED_ECHO_COVERAGE'] = np.where(np.isfinite(x_values) & ~np.isfinite(s_values), 1.0, np.nan)
    arrays['X_ECHO_WITHOUT_S_OBSERVATION'] = np.where(np.isfinite(x_values) & ~so, 1.0, np.nan)
    arrays['X_SELECTED_WHERE_S_ECHO'] = np.where((arrays['WINNER_BAND'] == 2) & np.isfinite(s_values), 1.0, np.nan)
    site_legend = [dict(minimum=s['index'], label=s['radar_id'], color=SITE_COLORS[s['index'] % len(SITE_COLORS)]) for s in scopes['sx_composite']['stations']]
    extra = [('sx_minus_s', 'S+X CR − S-only CR', 'DBZH_SX_MINUS_S', 'dBZ', DIFF_LEGEND), ('x_added_coverage', 'X 新增观测覆盖（含有效无回波）', 'X_ADDED_COVERAGE', '', [dict(minimum=1, label='新增覆盖', color='#b469cc')]), ('x_added_echo', 'X 有回波而 S-only 无有限回波', 'X_ADDED_ECHO_COVERAGE', '', [dict(minimum=1, label='X 回波', color='#b469cc')]), ('winner_band', '获胜波段', 'WINNER_BAND', '', [dict(minimum=1, label='S', color='#147d92'), dict(minimum=2, label='X', color='#b469cc')]), ('winner_site', '获胜站点', 'WINNER_SITE', '', site_legend), ('winner_age', '观测年龄', 'WINNER_AGE_SECONDS', 's', [dict(minimum=v, label=str(v), color=c) for v, c in zip((0, 60, 120, 240, 420), ('#e8f1ef', '#83c6bc', '#3e9c92', '#d2b36d', '#bd604a'), strict=True)]), ('winner_quality', '启发式质量得分（非概率）', 'WINNER_QUALITY_SCORE', '', [dict(minimum=v, label=str(v), color=c) for v, c in zip((0, 0.25, 0.5, 0.75, 1), ('#bd604a', '#d2b36d', '#83c6bc', '#3e9c92', '#147467'), strict=True)])]
    if 'crs' in m:
        for pid, label, field, unit, legend in extra:
            if field not in arrays:
                continue
            geometry, value = sample(arrays[field], m)
            classified = value
            if pid in ('winner_age', 'winner_quality'):
                classified = np.full(value.shape, np.nan)
                for e in legend:
                    classified[value >= e['minimum']] = e['minimum']
            path = f'map/{pid}.png'
            objects[path] = p.difference_quicklook(value) if pid == 'sx_minus_s' else p.categorical_preview(classified, legend)
            products.append(dict(product_id=pid, label=label, status='available' if np.isfinite(value).any() else 'no_coverage', unit=unit, legend=legend, map={**geometry, 'object_path': path}, field=field))
            layers.append(dict(object_path=path, title=label, field=field))
        for pid, field in (('s_only', 'CR_DBZH_S_ONLY'), ('x_only', 'CR_DBZH_X_ONLY'), ('sx_composite', 'CR_DBZH')):
            entry = by_id[pid]
            if entry['object_path'] is None:
                continue
            geometry, value = sample(arrays[field], m)
            path = f'map/{pid}.png'
            objects[path] = p.quicklook(value)
            entry['map'] = {**geometry, 'object_path': path}
            layers.append(dict(object_path=path, title=entry['label'], field=field))
        for entry in products:
            if not entry.get('map'):
                continue
            pid = entry['product_id']
            field = entry.get('field') or {'s_only': 'CR_DBZH_S_ONLY', 'x_only': 'CR_DBZH_X_ONLY', 'sx_composite': 'CR_DBZH'}.get(pid)
            if field not in arrays:
                continue
            _, scalar = sample(arrays[field], m)
            fields = {field: scalar[::-1]}
            if pid in scopes:
                suffix = '' if pid == 'sx_composite' else '_S_ONLY' if pid == 's_only' else '_X_ONLY'
                for name in (*WINNER_FIELDS, 'WINNER_STATION_INDEX', 'WINNER_BAND'):
                    key = name + suffix
                    if key in arrays:
                        _, value = sample(arrays[key], m)
                        fields[name] = value[::-1]
                fields['CR_DBZH'] = scalar[::-1]
            identity = dict(analysis_time=m['analysis_time'], product_id=pid, grid_id=m['grid_id'])
            if pid in scopes:
                identity.update(source_contract=CONTRACT, sources_sha256=scopes[pid]['sources_sha256'])
            entry['map']['probe'] = probe_writer(objects, f'query/composite/{pid}', fields, image_path=entry['map']['object_path'], identity=identity)
    readiness = dict(contract='rainpulse.sx-readiness-v1', method=method, analysis_time=m.get('analysis_time'), network_sha256=m.get('network_sha256'), operational_eligible=bool(m.get('operational_eligible', False)), qpe_eligible=bool(m.get('qpe_eligible', False)), products={pid: dict(counts=s['counts'], source_indices=s['source_indices'], winner_source_indices=s['winner_source_indices']) for pid, s in scopes.items()}, qualification_semantics='passed_current_method_not_automatic_quantitative_readiness')
    audit = dict(contract='rainpulse.sx-selection-audit-v1', method=method, analysis_time=m.get('analysis_time'), products={pid: c.metadata.get('fusion_audit', {'status': 'not_collected_legacy_or_external_result'}) for pid, c in components.items() if c is not None}, paired=paired_diagnostic(getattr(components['s_only'], 'audit_samples', None), getattr(components['x_only'], 'audit_samples', None)), column_difference=dict(semantics='CR_X_minus_CR_S_not_calibration', finite_pairs=int(both.sum())), added_coverage=dict(includes_valid_no_echo=int(np.count_nonzero(xo & ~so)), x_echo_without_s_observation=int(np.count_nonzero(np.isfinite(x_values) & ~so)), x_selected_with_s_echo=int(np.count_nonzero((arrays['WINNER_BAND'] == 2) & np.isfinite(s_values)))), meteorological_accuracy='not_evaluated')
    refs = dict(readiness=_blob(objects, 'fusion-readiness.json', readiness), selection=_blob(objects, 'source-selection-audit.json', audit))
    encoded = p.encode_arrays(arrays)
    objects['arrays.npz'] = encoded
    manifest = dict(m, source_contract=CONTRACT, source_scope=scopes['sx_composite'], sources=scopes['sx_composite']['sources'], stations=scopes['sx_composite']['stations'], method_key=method, method_label=method_label, audit=refs, arrays_sha256=hashlib.sha256(encoded).hexdigest(), layers=layers, comparison=dict(cadence_seconds=int(m['cadence_seconds']), same_grid=True, difference_semantics='column_CR_X_minus_CR_S_where_both_finite_not_calibration', difference_valid_cells=int(both.sum()), products=products, comparison_group=hashlib.sha256(encoded).hexdigest(), display_note='同一冻结输入/网格；观测年龄保留，缺测不作为零雨量'), legend=[dict(minimum_dbzh=float(n), rgb=list(map(int, c))) for n, c in zip(p.LEVELS, p.COLORS, strict=True)], display_note='投影快视图与地理图明确区分；来源以各产品显式索引表为准')
    objects['manifest.json'] = _json(manifest)
    return objects
