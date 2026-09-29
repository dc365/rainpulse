"""Bounded RAW context. Unknown upper coverage never means absent weather.

A verified upper-weather conflict can downgrade confirmation to mixed/CR-only,
never restore a withheld gate. No historical catalog search or future lookup.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal
import numpy as np
import re
from pydantic import BaseModel, ConfigDict, Field


class ContextPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    mode: Literal['disabled', 'audit', 'mixed_review'] = 'disabled'
    maximum_donors: int = Field(strict=True, default=2, ge=1, le=4)
    maximum_bytes: int = Field(strict=True, default=64 * 1024**2, ge=4096, le=256 * 1024**2)
    maximum_pairs: int = Field(strict=True, default=2_000_000, ge=1, le=8_000_000)
    maximum_seconds: float = Field(default=180., gt=0., le=360.)
    maximum_horizontal_m: float = Field(default=500., gt=0., le=1500.)
    minimum_vertical_m: float = Field(default=250., ge=100., le=1000.)
    maximum_vertical_m: float = Field(default=3000., ge=1000., le=6000.)
    minimum_snr_db: float = Field(default=15., ge=10., le=30.)
    minimum_rhohv: float = Field(default=.97, ge=.95, le=1.)


@dataclass(frozen=True)
class Donor:
    cut: object
    metadata: dict


@dataclass(frozen=True)
class ContextBundle:
    donors: tuple
    record: dict


def cut_bytes(cut):
    return sum(np.asarray(a).nbytes for a in (cut.azimuth_deg, cut.range_m,
        cut.elevation_deg, cut.ray_time_epoch, *cut.fields.values()))


class GroupContextProvider:
    """Reads descriptors before choosing at most N actual-height neighbours.

    Installed by GroupCuts/readers over an already verified immutable mapping.
    Never retains donors across tasks, never invokes QC on a donor.
    """
    def __init__(self, cuts):
        self.cuts = cuts

    def load(self, target, metadata, policy):
        descriptions = []
        center = float(np.median(target.elevation_deg))
        for number in self.cuts.numbers:
            if number == target.number:
                continue
            g = self.cuts.root[f'sweep_{number:03d}']
            descriptor = g['elevation']
            if len(descriptor.shape) != 1 or np.prod(descriptor.shape) > 4096:
                continue
            e = np.asarray(descriptor[:])
            if not len(e) or not np.isfinite(e).all():
                continue
            delta = float(np.median(e)) - center
            if delta <= .2:
                continue
            size = sum(int(np.prod(g[k].shape)) * np.dtype(g[k].dtype).itemsize
                       for k in g.array_keys())
            descriptions.append((delta, number, size))
        donors, used = [], 0
        for _, number, size in sorted(descriptions):
            if len(donors) >= policy.maximum_donors:
                break
            if used + size > policy.maximum_bytes:
                continue
            value = self.cuts.read(number)
            actual = cut_bytes(value.sweeps[0])
            if used + actual > policy.maximum_bytes:
                continue
            donors.append(Donor(value.sweeps[0], dict(value.metadata)))
            used += actual
        return ContextBundle(tuple(donors), {'status': 'BOUND' if donors else 'NO_BOUNDED_DONOR',
            'reader': 'verified_groupcuts', 'decoded_bytes': used})


def context_for_cut(volume, cut, cfg):
    policy = cfg.context
    if policy.mode == 'disabled':
        return ContextBundle((), {'status': 'DISABLED'})
    provider = getattr(volume, 'xqc_context_provider', None)
    if provider is not None:
        return provider.load(cut, volume.metadata, policy)
    # Full in-memory native inputs already have all of these observations.
    candidates = [Donor(s, dict(volume.metadata)) for s in volume.sweeps if s.number != cut.number]
    # A caller may bind frozen past/same-volume donors, never a search callback.
    candidates.extend(getattr(volume, 'xqc_frozen_context', ()))
    candidates.sort(key=lambda d: (abs(float(np.median(d.cut.elevation_deg)) - float(np.median(cut.elevation_deg))),
                                   str(d.metadata.get('scan_id')), d.cut.number))
    donors, used = [], 0
    for d in candidates:
        size = cut_bytes(d.cut)
        if len(donors) >= policy.maximum_donors:
            break
        if used + size <= policy.maximum_bytes:
            used += size; donors.append(d)
    return ContextBundle(tuple(donors), {'status': 'BOUND' if donors else 'NOT_BOUND_SINGLE_CUT',
        'reader': 'frozen_native_input', 'decoded_bytes': used})


def evaluate_context(s, metadata, cfg, bundle):
    from ..model import epoch
    from .geometry import adapt
    from rainpulse_algo.radar.qc_engine.volume_review.clutter_fusion.context import ground, height, sample_ground
    policy = cfg.context
    shape = s.shape
    positive = np.zeros(shape, bool)
    measured = np.zeros(shape, bool)
    donor_id = np.full(shape, -1, np.int16)
    donor_ray = np.full(shape, -1, np.int32)
    donor_gate = np.full(shape, -1, np.int32)
    def finish(record):
        return positive, record, measured, donor_id, donor_ray, donor_gate
    records = []
    if bundle is None or not bundle.donors or policy.mode == 'disabled':
        return finish({'status': 'DISABLED' if policy.mode == 'disabled' else (bundle.record.get('status') if bundle else 'NOT_BOUND')})
    if len(bundle.donors) > policy.maximum_donors or sum(cut_bytes(d.cut) for d in bundle.donors) > policy.maximum_bytes:
        return finish({'status': 'DONOR_BUDGET_ABSTAINED'})
    z, observed = s.moment('DBZH');sn, sa = s.moment('SNR');rho, ra = s.moment('RHOHV')
    # Positive context cannot confirm the target is clean: only inspect target
    # measurements already compatible with weather, and downgrade to mixed.
    rows, gates = np.nonzero(observed & sa & ra & (sn >= policy.minimum_snr_db) & (rho >= .9))
    if len(rows) * len(bundle.donors) > policy.maximum_pairs:
        return finish({'status': 'PAIR_BUDGET_ABSTAINED'})
    if s.ray_time_s is None:
        return finish({'status': 'TARGET_TIME_UNAVAILABLE'})
    if not len(rows):
        return finish({'status': 'NO_TARGET_COMPATIBLE_MEASUREMENT'})
    required = ('radar_id', 'scan_id', 'asset_sha256', 'volume_start', 'volume_end', 'available_at')
    if any(not metadata.get(k) for k in required):
        return finish({'status': 'TARGET_IDENTITY_UNAVAILABLE'})
    tr = s.ranges[gates];el = s.elevation[rows]
    tg, th = ground(tr, el), height(tr, el)
    seen = set()
    for i, donor in enumerate(bundle.donors):
        m = donor.metadata
        if not isinstance(m, dict) or any(not m.get(k) for k in required):
            records.append({'status': 'DONOR_IDENTITY_UNAVAILABLE', 'sweep_number': donor.cut.number})
            continue
        if not re.fullmatch('[0-9a-f]{64}', str(m['asset_sha256'])):
            records.append({'status': 'DONOR_ASSET_ID_INVALID', 'sweep_number': donor.cut.number})
            continue
        ident = (m.get('asset_sha256'), donor.cut.number)
        if ident in seen:
            raise ValueError('duplicate context donor identity')
        seen.add(ident)
        status = 'ACCEPTED'
        if m.get('radar_id') != metadata.get('radar_id') or not metadata.get('radar_config_version') or m.get('radar_config_version') != metadata.get('radar_config_version'):
            status = 'IDENTITY_OR_PROCESSING_MISMATCH'
        same = m.get('scan_id') == metadata.get('scan_id')
        if same and (m.get('asset_sha256') != metadata.get('asset_sha256') or donor.cut.number == int(s.name.split('_')[-1])):
            status = 'SELF_OR_ASSET_MISMATCH'
        if not same and epoch(m['volume_end']) >= epoch(metadata['volume_start']):
            status = 'NOT_STRICTLY_PAST'
        if epoch(m['available_at']) > epoch(metadata['available_at']):
            status = 'FUTURE_ARRIVAL'
        entry = {'status': status, 'scan_id': m.get('scan_id'), 'asset_sha256': m.get('asset_sha256'),
                 'sweep_number': donor.cut.number}
        records.append(entry)
        if status != 'ACCEPTED':
            continue
        start, end, arrival = (epoch(m[k]) for k in ('volume_start', 'volume_end', 'available_at'))
        times = np.asarray(donor.cut.ray_time_epoch)
        if end < start or arrival < end or not np.isfinite(times).all() or np.any(np.rint(times*1e6) < round(start*1e6)) or np.any(np.rint(times*1e6) > round(end*1e6)):
            entry['status'] = 'DONOR_TIME_CONTRACT_INVALID'
            continue
        from rainpulse_algo.radar.qc_engine.volume_review.data import ResourceLimit
        try:
            dv = adapt(donor.cut, cfg)
        except ResourceLimit as exc:
            entry.update(status='DONOR_GEOMETRY_ABSTAINED', detail=str(exc))
            continue
        d = dv.sweep
        dr, dg, foot, dh = sample_ground(d, s.azimuth[rows], tg)
        azdelta = np.deg2rad((d.azimuth[dr] - s.azimuth[rows] + 180) % 360 - 180)
        donor_ground = ground(d.ranges[dg], d.elevation[dr])
        horizontal = np.sqrt(np.maximum(0., (donor_ground-tg)**2 + 2*donor_ground*tg*(1-np.cos(azdelta))))
        delta = dh - th
        foot &= (abs(d.ray_time_s[dr]-s.ray_time_s[rows]) <= policy.maximum_seconds)
        foot &= (horizontal <= policy.maximum_horizontal_m) & (delta >= policy.minimum_vertical_m) & (delta <= policy.maximum_vertical_m)
        dz, da = d.moment('DBZH');ds, das = d.moment('SNR');rr, dar = d.moment('RHOHV');dd, dad = d.moment('ZDR')
        actual = foot & da[dr,dg] & das[dr,dg]
        measured[rows[actual],gates[actual]]=True
        weather = actual & dar[dr,dg] & dad[dr,dg] & (ds[dr,dg]>=policy.minimum_snr_db) & (rr[dr,dg]>=policy.minimum_rhohv)
        weather &= (dd[dr,dg]>=-.5)&(dd[dr,dg]<=3.)
        # Beam verification is a separate explicit asset/metadata contract.
        target_contract = metadata.get('clutter_context_contract', {})
        donor_contract = m.get('clutter_context_contract', {})
        if not isinstance(target_contract, dict) or not isinstance(donor_contract, dict):
            raise ValueError('context beam contract must be an object')
        bw1, bw2 = target_contract.get('beam_width_deg'), donor_contract.get('beam_width_deg')
        verified = bool(target_contract.get('beam_source') and donor_contract.get('beam_source') and
                        type(bw1) in (int,float) and type(bw2) in (int,float) and
                        0 < bw1 <= 3 and 0 < bw2 <= 3)
        compatible_count = int(weather.sum())
        if verified:
            weather &= height(d.ranges[dg],d.elevation[dr]-bw2/2) > height(tr,el+bw1/2)
        else:
            weather[:] = False
        # Retain the provenance of the first positive donor, not a later
        # unrelated measurement that happens to cover the same target.
        chosen = actual & (donor_id[rows,gates] < 0)
        chosen |= weather & ~positive[rows,gates]
        ii = (rows[chosen], gates[chosen])
        donor_id[ii] = i
        donor_ray[ii] = dv.order[dr[chosen]]
        donor_gate[ii] = dg[chosen]
        positive[rows[weather],gates[weather]]=True
        entry.update(measured_gates=int(actual.sum()), weather_compatible_gates=compatible_count, verified_conflict_gates=int(weather.sum()),
                     verified_beam=verified)
    return finish({**bundle.record, 'status': 'EVALUATED', 'donors':records,
        'measured_gates':int(measured.sum()), 'verified_conflict_gates':int(positive.sum()),
        'unknown_is_no_echo':False, 'action':'mixed_cr_withheld_only' if policy.mode=='mixed_review' else 'diagnostic'})


def bind_group_context(volume, cuts):
    """Attach a lazy provider without adding object references to metadata."""
    if cuts.station.band != 'X':
        return volume
    cfg = cuts.station.x_qc.enhancement
    if cfg is not None and cfg.get('context', {}).get('mode', 'disabled') != 'disabled':
        volume.xqc_context_provider = GroupContextProvider(cuts)
    return volume


def use_context_streaming(station):
    """Legacy standalone Zarr must use the same bounded donor reader as fusion."""
    enhancement = station.x_qc.enhancement
    return bool(station.band == 'X' and station.source == 'normalized_zarr' and
                enhancement is not None and
                enhancement.get('context', {}).get('mode', 'disabled') != 'disabled')
