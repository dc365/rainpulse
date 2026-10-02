"""Bounded original views of repeated RAW rays; action requires an explicit X policy.

Every view chooses an actual acquisition row. Values are never averaged or
mixed across moments. A proposal must survive every admissible view; repeated
rows themselves remain ambiguous and cannot inherit the proposal.
"""
import hashlib
from dataclasses import dataclass, replace
from itertools import product

import numpy as np

from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit

from .geometry import adapt


def _fingerprint(cut):
    h = hashlib.sha256(str(cut.number).encode())
    arrays = {'geometry/azimuth': cut.azimuth_deg, 'geometry/range': cut.range_m,
              'geometry/elevation': cut.elevation_deg, 'geometry/time': cut.ray_time_epoch,
              **{'field/' + k: v for k, v in cut.fields.items()}}
    for name, value in sorted(arrays.items()):
        a = np.asarray(value)
        h.update(name.encode())
        h.update(str((a.shape, a.dtype.str)).encode())
        h.update(a.tobytes())
    return h.hexdigest()


@dataclass(frozen=True)
class Alternative:
    input_sha256: str
    parameter_sha256: str
    choice: int
    sweep: object
    native_indices: np.ndarray


@dataclass(frozen=True)
class Alternatives:
    input_sha256: str
    parameter_sha256: str
    shape: tuple
    groups: tuple
    selections: tuple
    repeated_rows: tuple

    @property
    def count(self):
        return len(self.selections)

    def views(self, cut, cfg):
        if cfg.digest != self.parameter_sha256:
            raise ValueError('native alternative policy changed')
        if _fingerprint(cut) != self.input_sha256:
            raise ValueError('native alternative input identity changed')
        repeated = set(self.repeated_rows)
        for number, chosen in enumerate(self.selections):
            keep = np.array([i for i in range(self.shape[0])
                             if i not in repeated or i in chosen], dtype=int)
            child = replace(cut, azimuth_deg=cut.azimuth_deg[keep],
                            elevation_deg=cut.elevation_deg[keep],
                            ray_time_epoch=cut.ray_time_epoch[keep],
                            fields={k: a[keep] for k, a in cut.fields.items()})
            view = adapt(child, cfg)
            native = keep[view.order]
            native.setflags(write=False)
            yield Alternative(self.input_sha256, self.parameter_sha256, number, view.sweep, native)


def alternatives(cut, cfg, *, maximum_views=16):
    if type(maximum_views) is not int or not 1 <= maximum_views <= 16:
        raise ValueError('native choice cap must be an integer in 1..16')
    cut.validate(allow_duplicate_azimuth=True)
    shape = cut.fields['DBZH'].shape
    if np.prod(shape) > cfg.maximum_sweep_gates:
        raise ResourceLimit('native alternative gate budget exceeded')
    if sum(a.nbytes for a in cut.fields.values()) > 256 * 1024**2:
        raise ResourceLimit('native alternative byte budget exceeded')
    order = np.argsort(cut.azimuth_deg, kind='stable')
    parent = list(range(len(order)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in zip(order, np.roll(order, -1)):
        if (cut.azimuth_deg[b] - cut.azimuth_deg[a]) % 360 <= .01:
            parent[root(int(a))] = root(int(b))
    grouped = {}
    for i in range(len(order)):
        grouped.setdefault(root(i), []).append(i)
    groups = tuple(tuple(g) for g in grouped.values() if len(g) > 1)
    count = 1
    for g in groups:
        count *= len(g)
        if count > maximum_views:
            raise ResourceLimit('native alternative choice budget exceeded')
        az = cut.azimuth_deg[list(g)]
        separation = abs((az[:, None] - az[None, :] + 180) % 360 - 180)
        if separation.max() > .01 + 1e-8:
            raise ResourceLimit('chained native repeated-ray ambiguity')
        if np.ptp(cut.ray_time_epoch[list(g)]) > cfg.maximum_neighbor_time_s:
            raise ResourceLimit('native repeated-ray time ambiguity')
        if np.ptp(cut.elevation_deg[list(g)]) > cfg.maximum_neighbor_elevation_deg:
            raise ResourceLimit('native repeated-ray elevation ambiguity')
    selections = tuple(product(*groups))
    return Alternatives(_fingerprint(cut), cfg.digest, shape, groups, selections,
                        tuple(i for g in groups for i in g))


def consensus(choices, proposals):
    result = np.ones(choices.shape, bool)
    seen = set()
    for view, proposal in proposals:
        if (view.input_sha256 != choices.input_sha256
                or view.parameter_sha256 != choices.parameter_sha256):
            raise ValueError('native choice input or policy identity differs')
        if view.choice in seen or not 0 <= view.choice < choices.count:
            raise ValueError('duplicate or unknown native choice')
        expected = set(range(choices.shape[0])) - set(choices.repeated_rows)
        expected.update(choices.selections[view.choice])
        if set(view.native_indices) != expected or len(view.native_indices) != len(expected):
            raise ValueError('native choice identity differs')
        a = np.asarray(proposal)
        if a.dtype != bool or a.shape != view.sweep.shape:
            raise ValueError('native proposal must be a boolean original-view matrix')
        full = np.zeros(choices.shape, bool)
        full[view.native_indices] = a
        result &= full
        seen.add(view.choice)
    if len(seen) != choices.count:
        raise ValueError('complete native choice set required')
    result[list(choices.repeated_rows)] = False
    return result


def evaluate(cut, cfg, protected, previous_records):
    """Atomic candidate consensus within the cut's existing source allowance."""
    from .source_fans import detect
    from .source_summary import SourceStatistics

    protected = np.asarray(protected)
    if protected.dtype != bool or protected.shape != cut.fields['DBZH'].shape:
        raise ValueError('native protection must bind the complete original matrix')
    result = np.zeros(protected.shape, bool)
    work = previous_records['radial_source'].get('work', {})
    near = previous_records['near_floor_source']
    prior_trials = int(work.get('model_trials', 0)) + int(
        near.get('work', {}).get('model_trials', 0))
    prior_models = int(work.get('model_records', 0)) + int(
        near.get('work', {}).get('model_records', 0))
    prior_geometry = int(work.get('geometry_comparisons', 0)) + int(
        near.get('work', {}).get('geometry_comparisons', 0))
    trials = models = geometry = 0
    views = []
    record = {'version': 'native-original-choice-consensus-v1',
              'action_semantics': 'candidate_withheld_not_confirmed',
              'source_gates': 0, 'previous_source_trials': prior_trials,
              'previous_source_models': prior_models}
    if near.get('status') != 'EVALUATED':
        return result, dict(record, status='UNAVAILABLE_PARENT_EVIDENCE')
    try:
        choices = alternatives(cut, cfg)
        record.update(input_sha256=choices.input_sha256, groups=choices.groups,
                      parameter_sha256=choices.parameter_sha256, choice_count=choices.count)
        if not choices.groups:
            return result, dict(record, status='NO_REPEATED_RAYS')

        def stream():
            nonlocal trials, models, geometry
            for v in choices.views(cut, cfg):
                stats = SourceStatistics.build(v.sweep, cfg)
                stats.maximum_trials -= prior_trials + trials
                stats.maximum_models -= prior_models + models
                if stats.maximum_trials <= 0 or stats.maximum_models <= 0:
                    raise ResourceLimit('existing shared source allowance exhausted')
                try:
                    proposal, proof = detect(v.sweep, cfg, protected=protected[v.native_indices],
                                             prepared=stats, near_floor_references=True)
                finally:
                    trials += stats.trials
                    models += stats.models
                    geometry += stats.geometry_comparisons
                if prior_geometry + geometry > 32 * len(cut.azimuth_deg)**2:
                    raise ResourceLimit('shared native geometry allowance exhausted')
                views.append({'choice': v.choice, 'native_indices': v.native_indices.tolist(),
                              'models': proof, 'work': stats.receipt()})
                yield v, proposal

        result = consensus(choices, stream()) & ~protected
        record.update(status='EVALUATED', source_gates=int(result.sum()), views=views)
    except ResourceLimit as exc:
        result[:] = False
        # Incomplete view proposals have no action authority. Work is retained;
        # accepted original source evidence is owned by the parent, not replaced.
        record.update(status='RESOURCE_OR_GEOMETRY_ABSTAINED', reason=str(exc), source_gates=0)
    record.update(work={'model_trials': trials, 'model_records': models,
                        'geometry_comparisons': geometry},
                  cumulative_source_trials=prior_trials+trials,
                  cumulative_source_models=prior_models+models)
    return result, record
