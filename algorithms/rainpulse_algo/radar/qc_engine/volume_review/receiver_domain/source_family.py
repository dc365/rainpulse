"""Fold-scoped coherent source objects, ordered own states and one-hop evidence.

Only raw measured neighbors donate reference information. Targets/guard blocks
and the entire target ray are excluded from donor training. Ordered range runs
supply the target ray's amplitude, never the target residual. Shared signatures
are NOT independent meteorological truth; missing values are never synthesized.
"""
from collections import Counter
from dataclasses import dataclass
from enum import IntFlag
import hashlib
import itertools
import numpy as np
from ..data import ResourceLimit, array_digest, json_bytes
from ..geometry import wrap, runs
from .core import domains, fit_fold, supported_indices, intervals, MOMENTS


class Reason(IntFlag):
    ATTEMPTED = 1
    DONOR_REFERENCE_INSUFFICIENT = 2
    DONOR_FAMILY_AMBIGUOUS = 4
    OWN_STATE_INSUFFICIENT = 8
    FAMILY_REFERENCE = 16
    POWER_CONFLICT = 32
    POLAR_CONFLICT = 64
    POLAR_INCOMPLETE = 128
    NUMERIC_TAIL = 256
    OUTER_SIDE_UNAVAILABLE = 512
    OUTER_SIDE_CONFLICT = 1024
    CURRENT_DONORS_INSUFFICIENT = 2048
    STATE_AMBIGUOUS = 4096
    FULL_MATCH = 8192
    PARTIAL_MATCH = 16384
    INDEPENDENT_OR_UNKNOWN_WEATHER = 32768
    LOCAL_COHERENCE_CONFLICT = 65536
    SOURCE_SUPPORTED = 131072
    RAW_SNR_UNAVAILABLE = 262144


DTYPES = {
    **{k: "uint8" for k in ("FAMILY_ATTEMPTED_MASK", "FAMILY_REFERENCE_MASK",
        "FAMILY_PARENT_AVAILABLE_MASK", "FAMILY_CURRENT_SUPPORT_MASK",
        "FAMILY_SIDE_MEASURED_MASK", "FAMILY_UNRESOLVED_MASK",
        "FAMILY_DONOR_COUNT", "FAMILY_SAME_RANGE_DONORS", "FAMILY_MATCH_COUNT",
        "FAMILY_STATE_ID")},
    "FAMILY_OBJECT_ID": "uint32", "FAMILY_REASON": "uint32",
    "FAMILY_REFERENCE_DISTANCE_M": "float32",
}


class FamilyResourceLimit(ResourceLimit):
    """Discard new family decisions, retaining the frozen parent RDR result."""


@dataclass
class Budget:
    folds: int = 0
    donor_trials: int = 0
    ordered_blocks: int = 0
    objects: int = 0

    def take(self, field, limit, amount=1):
        value = getattr(self, field) + amount
        if value > limit:
            raise FamilyResourceLimit("source-family " + field + " budget; discard whole volume")
        setattr(self, field, value)


def _indices(bounds):
    return np.concatenate([np.arange(lo, hi, dtype=int) for lo, hi in bounds]) if bounds else np.zeros(0, int)


def near_rows(s, row, cfg):
    """Contiguous angular neighbors only; no raw-array row-number assumption."""
    fc = cfg.source_family
    result = []
    for direction in (-1, 1):
        current = row
        for _ in range(s.shape[0]-1):
            nxt = (current+direction) % s.shape[0]
            edge = current if direction == 1 else nxt
            if s.gap_after[edge] or not s.good[nxt]:
                break
            offset = float(wrap(s.azimuth[nxt]-s.azimuth[row]))
            if abs(offset) > fc.maximum_neighbor_angle_deg:
                break
            current = nxt
            if abs(s.elevation[nxt]-s.elevation[row]) > fc.maximum_elevation_difference_deg:
                continue
            if s.ray_time_s is None:
                if fc.require_ray_times:
                    continue
            elif abs(s.ray_time_s[nxt]-s.ray_time_s[row]) > fc.maximum_time_difference_s:
                continue
            result.append((abs(offset), offset, nxt))
    # Signed angular offset is a rotation-invariant tie break.
    return [x[2] for x in sorted(set(result))]


def _compatible_models(s, models, cfg):
    fc = cfg.source_family
    for x, y in itertools.combinations(models, 2):
        if (abs(float(wrap(x['phase_center_deg']-y['phase_center_deg']))) > fc.maximum_donor_phase_difference_deg
                or abs(x['zdr_center_db']-y['zdr_center_db']) > fc.maximum_donor_zdr_difference_db
                or abs(x['offset_db']-y['offset_db']) > fc.maximum_donor_offset_difference_db):
            return False
        lo = max(x['reference_min_m'], y['reference_min_m'])/1000.
        hi = min(x['reference_max_m'], y['reference_max_m'])/1000.
        if lo >= hi:
            return False
        difference = ((x['beta_db_per_km']-y['beta_db_per_km'])*np.array([lo, hi])
                      + x['offset_db']-y['offset_db'])
        if max(abs(difference)) > fc.maximum_donor_relation_difference_db:
            return False
    return True


def _own_states(s, row, block, shared, cfg, prepared, budget):
    """Keep physical range order. Group supported stable runs, never fill gaps.

    Each 2 km cell has explicit actual sample support. Unstable cells, missing
    cells, target/guard cells and changing amplitudes cut runs. Repeated runs
    may share an amplitude state only under a bounded complete-range center
    criterion. All states are built without reading the current target.
    """
    f, a = prepared; fc = cfg.source_family; r = s.ranges
    bix = (r//cfg.block_m).astype(int); fine = (r//fc.local_block_m).astype(int)
    train = (r >= cfg.minimum_range_m) & (abs(bix-block) > cfg.guard_blocks)
    ok = train.copy()
    for k in MOMENTS:
        ok &= a[k][row]
    ok &= ((f['SNR'][row] >= cfg.minimum_snr_db)
           & (abs(wrap(f['PHIDP'][row]-shared['phase_center_deg'])) <= cfg.target_phase_tolerance_deg)
           & (abs(f['ZDR'][row]-shared['zdr_center_db']) <= cfg.target_zdr_tolerance_db)
           & (f['RHOHV'][row] >= max(0., shared['rho_bounds'][0]-.05))
           & (f['RHOHV'][row] <= min(1., shared['rho_bounds'][1]+.05)))
    minimum = max(2, int(np.ceil(fc.minimum_local_support_m/s.dr)))
    ii = supported_indices(np.flatnonzero(ok), fine, minimum)
    blocks = []
    for b in np.unique(fine[ii]):
        budget.take('ordered_blocks', fc.maximum_ordered_blocks)
        jj = ii[fine[ii] == b]; values = f['SNR'][row, jj]
        center = float(np.median(values))
        if np.percentile(abs(values-center), 90) <= cfg.maximum_snr_p90_db:
            blocks.append({'block': int(b), 'indices': jj, 'center': center})
    ordered_runs = []
    for entry in blocks:
        if (not ordered_runs or entry['block'] != ordered_runs[-1][-1]['block']+1
                or np.ptp([x['center'] for x in ordered_runs[-1]]+[entry['center']]) > fc.maximum_state_center_spread_db):
            ordered_runs.append([])
        ordered_runs[-1].append(entry)
    # Run coalescing preserves run identities and does not interpolate gaps.
    clusters = []
    for run in ordered_runs:
        centers = [x['center'] for x in run]
        choices = [i for i, cl in enumerate(clusters)
                   if np.ptp([x['center'] for rr in cl for x in rr]+centers) <= fc.maximum_state_center_spread_db]
        if len(choices) > 1:
            return [], 'OWN_STATE_AMBIGUOUS'
        if choices:
            clusters[choices[0]].append(run)
        else:
            clusters.append([run])
    # Do not split an arbitrary broad continuous distribution into many bins.
    if len(clusters) > fc.maximum_states:
        return [], 'TOO_MANY_ORDERED_STATES'
    states = []
    for cluster in clusters:
        entries = [entry for run in cluster for entry in run]
        if len(entries) < fc.minimum_own_blocks:
            continue
        jj = np.sort(np.concatenate([entry['indices'] for entry in entries]))
        if len(jj)*s.dr < fc.minimum_own_support_m:
            continue
        snr = f['SNR'][row, jj]; center = float(np.median(snr))
        if np.percentile(abs(snr-center), 90) > cfg.maximum_snr_p90_db:
            continue
        x = r[jj]/1000.
        relation = f['DBZH'][row, jj]-snr-20*np.log10(x)-shared['beta_db_per_km']*x-shared['offset_db']
        bias = float(np.median(relation))
        if abs(bias) > fc.maximum_ray_bias_db or np.percentile(abs(relation-bias), 90) > cfg.maximum_relation_error_db:
            continue
        checks = []; valid = True; block_ids = np.unique(fine[jj])
        for train_ids in (block_ids[::2], block_ids[1::2]):
            tr = np.isin(fine[jj], train_ids); te = ~tr
            if min(tr.sum(), te.sum())*s.dr < fc.minimum_local_support_m:
                valid = False; break
            sn = float(np.median(snr[tr])); off = float(np.median(relation[tr]))
            sn_err = float(np.percentile(abs(snr[te]-sn), 90))
            rel_err = float(np.percentile(abs(relation[te]-off), 90))
            if sn_err > cfg.maximum_snr_p90_db or rel_err > cfg.maximum_relation_error_db or abs(off) > fc.maximum_ray_bias_db:
                valid = False; break
            checks.append({'train_blocks': train_ids.tolist(), 'validation_blocks': [int(b) for b in block_ids if b not in train_ids],
                           'snr_median_db': sn, 'bias_db': off, 'snr_prediction_p90_db': sn_err,
                           'relation_prediction_p90_db': rel_err})
        if not valid:
            continue
        states.append({'state_id': len(states)+1, 'snr_median_db': center,
            'snr_p90_db': float(np.percentile(abs(snr-center), 90)), 'ray_bias_db': bias,
            'relation_p90_db': float(np.percentile(abs(relation-bias), 90)),
            'sample_count': int(len(jj)), 'support_m': float(len(jj)*s.dr),
            'reference_intervals': intervals(jj, s.shape[1]), 'reference_blocks': block_ids.tolist(),
            'ordered_runs': [{'blocks': [e['block'] for e in run],
                              'intervals': intervals(np.sort(np.concatenate([e['indices'] for e in run])), s.shape[1])}
                             for run in cluster], 'cross_predictions': checks,
            'reference_sha256': array_digest({'indices': jj, **{k: f[k][row, jj] for k in MOMENTS}})})
    return states, 'FITTED' if states else 'OWN_STATE_INSUFFICIENT'


def fit_family(s, row, block, cfg, prepared=None, *, donor_prepared=None, budget=None):
    """Target-independent source reference. No recursive borrowing allowed."""
    f, a = domains(s, cfg) if prepared is None else prepared
    fc = cfg.source_family; budget = Budget() if budget is None else budget
    if fc is None:
        return None, 'DISABLED'
    if donor_prepared is None:
        aa = dict(a); aa['SNR'] = a['SNR'].copy(); aa['SNR'][row] = False
        donor_prepared = f, aa
    models = []
    for donor in near_rows(s, row, cfg):
        budget.take('donor_trials', fc.maximum_donor_trials)
        m, _ = fit_fold(s, donor, block, cfg, donor_prepared)
        if m is not None:
            models.append(m)
    if len(models) < fc.minimum_donors:
        return None, 'DONOR_SUPPORT'
    if len(models) > fc.maximum_donors:
        raise FamilyResourceLimit('too many family donors; not truncated')
    if not _compatible_models(s, models, cfg):
        return None, 'AMBIGUOUS_DONOR_FAMILY'
    offsets = [float(wrap(s.azimuth[m['ray']]-s.azimuth[row])) for m in models]+[0.]
    sides = []
    for direction, edge in ((-1, min(offsets)), (1, max(offsets))):
        options = []
        for m in models:
            for side in m['shoulders']:
                sr = side['ray']; angle = float(wrap(s.azimuth[sr]-s.azimuth[row]))
                if (sr != row and direction*(angle-edge) > .01 and abs(angle) <= cfg.maximum_flank_angle_deg):
                    options.append((abs(angle), angle, sr))
        if not options:
            return None, 'OUTSIDE_SHOULDER_SUPPORT'
        _, angle, sr = min(options)
        sides.append({'ray': int(sr), 'angle_offset_deg': angle})
    if sides[0]['ray'] == sides[1]['ray']:
        return None, 'REUSED_OUTER_SIDE'
    shared = {'beta_db_per_km': float(np.median([m['beta_db_per_km'] for m in models])),
              'offset_db': float(np.median([m['offset_db'] for m in models])),
              'phase_center_deg': float(np.angle(np.mean(np.exp(1j*np.deg2rad([m['phase_center_deg'] for m in models]))), deg=True)),
              'zdr_center_db': float(np.median([m['zdr_center_db'] for m in models])),
              'rho_bounds': [float(min(m['rho_bounds'][0] for m in models)), float(max(m['rho_bounds'][1] for m in models))]}
    states, status = _own_states(s, row, block, shared, cfg, (f,a), budget)
    if not states:
        return None, status
    pool = (s.ranges >= cfg.minimum_range_m) & (abs((s.ranges//cfg.block_m).astype(int)-block) > cfg.guard_blocks)
    digest = array_digest({'range': s.ranges[pool], **{k: np.where(a[k][row,pool], f[k][row,pool], np.nan) for k in MOMENTS}})
    rec = {'ray': int(row), 'target_block': int(block), 'guard_blocks': cfg.guard_blocks,
        'reference_route': 'shared_coherent_source_family', 'reference_policy': 'all_rays_target_guard_excluded_target_ray_not_donor',
        'donors': models, 'shoulders': sides, 'states': states, **shared,
        'own_discovery_sha256': digest, 'same_range_support_required': fc.minimum_donors,
        'neighbor_is_weather_truth': False, 'recursive_donation': False,
        'state_method': 'ordered_supported_range_runs_with_bounded_repeated_states'}
    rec['reference_sha256'] = hashlib.sha256(json_bytes(rec)).hexdigest()
    return rec, 'FITTED'


def _polar(s, row, j, model, cfg, prepared):
    f, a = prepared; count = np.zeros(len(j), 'uint8'); compatible = np.ones(len(j), bool)
    for k in ('PHIDP', 'ZDR', 'RHOHV'):
        valid = a[k][row,j]; value = f[k][row,j]; count += valid
        if k == 'PHIDP':
            ok = abs(wrap(value-model['phase_center_deg'])) <= cfg.target_phase_tolerance_deg
        elif k == 'ZDR':
            ok = abs(value-model['zdr_center_db']) <= cfg.target_zdr_tolerance_db
        else:
            ok = (value >= max(0, model['rho_bounds'][0]-.05)) & (value <= min(1, model['rho_bounds'][1]+.05))
        compatible &= ~valid | ok
    tail = s.available.get('ZDR', np.zeros(s.shape, bool))[row,j] & (abs(f['ZDR'][row,j]) >= cfg.maximum_abs_reference_zdr_db)
    return count, compatible, tail


def project_family(s, row, j, model, cfg, prepared, *, donor_veto=None):
    """Test targets against every train-built state; no nearest-target-state fit."""
    f, a = prepared; fc = cfg.source_family; r = s.ranges[j]/1000.
    snr = f['SNR'][row,j]
    count, polar, tail = _polar(s, row, j, model, cfg, prepared)
    measured = np.ones(len(j), bool); side = measured.copy()
    for shoulder in model['shoulders']:
        sr = shoulder['ray']; measured &= a['SNR'][sr,j]
        side &= a['SNR'][sr,j] & ((snr-f['SNR'][sr,j]) >= cfg.angular_contrast_db)
    donor_count = np.zeros(len(j), 'uint8')
    for donor in model['donors']:
        dr = donor['ray']; c, compatible, dtail = _polar(s, dr, j, donor, cfg, prepared)
        pred = donor['snr_median_db']+20*np.log10(r)+donor['beta_db_per_km']*r+donor['offset_db']
        valid = a['DBZH'][dr,j] & a['SNR'][dr,j] & (c == 3) & compatible & ~dtail
        valid &= (abs(f['SNR'][dr,j]-donor['snr_median_db']) <= cfg.maximum_snr_p90_db)
        valid &= abs(f['DBZH'][dr,j]-pred) <= cfg.maximum_target_residual_db
        if donor_veto is not None:
            valid &= ~donor_veto[dr,j]
        donor_count += valid
    current = side & (donor_count >= fc.minimum_donors)
    votes = np.zeros(len(j), 'uint8'); selected = np.zeros(len(j), 'uint8')
    residual = np.full(len(j), np.nan, 'float32'); ds = residual.copy(); distance = residual.copy()
    power_any = np.zeros(len(j), bool)
    for state in model['states']:
        pred = state['snr_median_db']+20*np.log10(r)+model['beta_db_per_km']*r+model['offset_db']+state['ray_bias_db']
        de = f['DBZH'][row,j]-pred; sd = snr-state['snr_median_db']
        power = a['SNR'][row,j] & (abs(de) <= cfg.maximum_target_residual_db) & (abs(sd) <= cfg.maximum_snr_p90_db)
        power_any |= power
        vote = power & polar & ~tail & current
        votes += vote
        fresh = vote & (selected == 0)
        selected[fresh] = state['state_id']; residual[fresh] = de[fresh]; ds[fresh] = sd[fresh]
        ii = _indices(state['reference_intervals']); pos = np.searchsorted(s.ranges[ii], s.ranges[j])
        dd = np.minimum(abs(s.ranges[j]-s.ranges[ii[np.clip(pos,0,len(ii)-1)]]),
                        abs(s.ranges[j]-s.ranges[ii[np.clip(pos-1,0,len(ii)-1)]]))
        distance[fresh] = dd[fresh]
    unique = votes == 1
    # Displayed residual refers only to a unique selected state; no best-fit target choice.
    residual[~unique] = np.nan; ds[~unique] = np.nan; distance[~unique] = np.nan; selected[~unique] = 0
    full = unique & (count == 3); partial = unique & (count < 3)
    reason = np.full(len(j), int(Reason.ATTEMPTED | Reason.FAMILY_REFERENCE), 'uint32')
    for m, bit in ((~power_any, Reason.POWER_CONFLICT), (~polar, Reason.POLAR_CONFLICT),
                   (count < 3, Reason.POLAR_INCOMPLETE), (tail, Reason.NUMERIC_TAIL),
                   (~measured, Reason.OUTER_SIDE_UNAVAILABLE), (measured & ~side, Reason.OUTER_SIDE_CONFLICT),
                   (donor_count < fc.minimum_donors, Reason.CURRENT_DONORS_INSUFFICIENT),
                   (votes > 1, Reason.STATE_AMBIGUOUS), (full, Reason.FULL_MATCH), (partial, Reason.PARTIAL_MATCH),
                   (~a['SNR'][row,j], Reason.RAW_SNR_UNAVAILABLE)):
        reason[m] |= int(bit)
    return {'full': full, 'partial': partial, 'count': count, 'polar': polar, 'tail': tail,
            'power': power_any, 'residual': residual, 'snr_delta': ds, 'distance': distance,
            'donor_count': donor_count, 'side_measured': measured, 'current': current,
            'side_conflict': ~side, 'votes': votes, 'state_id': selected, 'reason': reason,
            'unresolved': power_any & ~(full | partial)}


def extend(s, cfg, prepared, domain, out, records):
    """Inside the existing RDR stage, before the one joint action decision."""
    f, a = prepared; fc = cfg.source_family; budget = Budget(); failures = Counter()
    own = out['RDR_MODEL_AVAILABLE_MASK'] == 1
    out['RDR_FAMILY_PARENT_AVAILABLE_MASK'][:] = own
    candidates = domain & ~own & a['SNR'] & (f['SNR'] >= cfg.minimum_snr_db)
    bix = (s.ranges//cfg.block_m).astype(int)
    for row in np.flatnonzero(s.good & candidates.any(axis=1)):
        aa = dict(a); aa['SNR'] = a['SNR'].copy(); aa['SNR'][row] = False
        for block in np.unique(bix[candidates[row]]):
            budget.take('folds', fc.maximum_family_folds)
            # Evaluate all measured parent-unmodeled targets in the nominated block,
            # including measured weak/contradictory targets, for closure accounting.
            j = np.flatnonzero(domain[row] & ~own[row] & (bix == block))
            out['RDR_FAMILY_ATTEMPTED_MASK'][row,j] = 1
            model, status = fit_family(s, int(row), int(block), cfg, prepared, donor_prepared=(f,aa), budget=budget)
            if model is None:
                failures[status] += 1
                bit = (Reason.DONOR_FAMILY_AMBIGUOUS if 'AMBIGUOUS_DONOR' in status else
                       Reason.OWN_STATE_INSUFFICIENT if status.startswith(('OWN_', 'TOO_MANY_')) else
                       Reason.DONOR_REFERENCE_INSUFFICIENT)
                out['RDR_FAMILY_REASON'][row,j] = int(Reason.ATTEMPTED | bit)
                continue
            budget.take('objects', fc.maximum_family_models)
            model_id = len(records)+1
            records.append({'id': model_id, **model})
            donor_veto = (out['RDR_INDEPENDENT_WEATHER_MASK'] == 1) | (out['RDR_UNKNOWN_PROTECTION_MASK'] == 1)
            v = project_family(s, row, j, model, cfg, prepared, donor_veto=donor_veto)
            for key, value in (
                ('FULL_MATCH_MASK', v['full']), ('PARTIAL_MATCH_MASK', v['partial']),
                ('MODEL_AVAILABLE_MASK', True), ('MODEL_ID', model_id),
                ('RESIDUAL_DB', v['residual']), ('SNR_DELTA_DB', v['snr_delta']),
                ('TARGET_POLAR_COUNT', v['count']), ('TARGET_POWER_MATCH_MASK', v['power']),
                ('TARGET_POLAR_CONFLICT_MASK', ~v['polar']), ('TARGET_TAIL_MASK', v['tail']),
                ('TARGET_SIDE_CONFLICT_MASK', v['side_conflict']),
                ('FAMILY_REFERENCE_MASK', True), ('FAMILY_OBJECT_ID', model_id),
                ('FAMILY_DONOR_COUNT', len(model['donors'])), ('FAMILY_SAME_RANGE_DONORS', v['donor_count']),
                ('FAMILY_CURRENT_SUPPORT_MASK', v['current']), ('FAMILY_SIDE_MEASURED_MASK', v['side_measured']),
                ('FAMILY_MATCH_COUNT', v['votes']), ('FAMILY_STATE_ID', v['state_id']),
                ('FAMILY_REASON', v['reason']), ('FAMILY_REFERENCE_DISTANCE_M', v['distance'])):
                out['RDR_'+key][row,j] = value
    return {'version': fc.version, 'mode': fc.mode, 'full_policy': fc.full_policy, **vars(budget), 'failure_counts': dict(failures),
            'donors_are_independent_weather_truth': False, 'no_recursive_donation': True}


def finish(out, cfg):
    """Keep unresolved source-domain risks explicit without inventing actions."""
    ref = out['RDR_FAMILY_REFERENCE_MASK'] == 1
    full = out['RDR_FULL_MATCH_MASK'] == 1; partial = out['RDR_PARTIAL_MATCH_MASK'] == 1
    source = out['RDR_SOURCE_MASK'] == 1
    hard = (out['RDR_INDEPENDENT_WEATHER_MASK'] == 1) | (out['RDR_UNKNOWN_PROTECTION_MASK'] == 1)
    local = out['RDR_LOCAL_COHERENCE_MASK'] == 1
    why = out['RDR_FAMILY_REASON']
    why[ref & hard] |= int(Reason.INDEPENDENT_OR_UNKNOWN_WEATHER)
    why[ref & local & ~source] |= int(Reason.LOCAL_COHERENCE_CONFLICT)
    why[ref & source] |= int(Reason.SOURCE_SUPPORTED)
    risk = ref & (out['RDR_TARGET_POWER_MATCH_MASK'] == 1) & ~source
    out['RDR_FAMILY_UNRESOLVED_MASK'][:] = risk
    return {'matched_full_gates': int((full & ref).sum()), 'matched_partial_gates': int((partial & ref).sum()),
            'source_gates': int((source & ref).sum()), 'unresolved_gates': int(risk.sum()),
            'attempted_gates': int(out['RDR_FAMILY_ATTEMPTED_MASK'].sum()),
            'reason_bit_counts': {k.name: int(((why & int(k)) != 0).sum()) for k in Reason},
            'unknown_action': 'diagnostic_only_except_explicit_partial_cr_policy'}


def parent_only_evidence(evidence, cfg, reason):
    """Decorate a re-evaluated frozen parent without changing any parent field."""
    from .core import Evidence
    arrays = dict(evidence.arrays)
    shape = arrays["RDR_OBSERVED_MASK"].shape
    for key, dt in DTYPES.items():
        arrays["RDR_"+key] = np.full(shape, np.nan if dt == "float32" else 0, dt)
    arrays["RDR_FAMILY_PARENT_AVAILABLE_MASK"][:] = arrays["RDR_MODEL_AVAILABLE_MASK"]
    summary = dict(evidence.summary, config_sha256=cfg.digest)
    summary["source_family"] = {"version": cfg.source_family.version,
        "status": "RESOURCE_ABSTAINED_PARENT_RETAINED", "reason": str(reason),
        "matched_full_gates": 0, "source_gates": 0, "unresolved_gates": 0}
    return Evidence(arrays, evidence.models, summary)
