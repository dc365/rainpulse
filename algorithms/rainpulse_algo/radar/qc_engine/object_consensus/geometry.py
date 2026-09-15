"""Original ray topology and measured domains; no raster morphology or interpolation."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np


def runs(mask):
    ends = np.diff(np.r_[False, mask, False].astype("int8"))
    return [(int(a), int(b)) for a, b in zip(np.flatnonzero(ends == 1), np.flatnonzero(ends == -1), strict=True)]


def wrap(x, period=360.0):
    return (x + period/2) % period - period/2


def edge_geometry(raw, cfg):
    n = raw.shape[0]
    delta = (np.roll(raw.azimuth, -1) - raw.azimuth) % 360
    valid_delta = delta[(delta > 1e-6) & (delta < 90)]
    spacing = float(np.median(valid_delta)) if len(valid_delta) else 1.0
    ok = raw.good & np.roll(raw.good, -1) & ~raw.gaps
    ok &= (delta >= spacing * 0.05) & (delta <= spacing * cfg.azimuth_gap_factor)
    ok &= abs(raw.elevation-np.roll(raw.elevation, -1)) <= cfg.maximum_elevation_difference_deg
    if not raw.full_ppi or n < 2:
        ok[-1] = False
    return ok, delta, spacing


@dataclass
class Domain:
    identity: int
    ray: int
    lo: int
    hi: int
    indices: np.ndarray
    blocks: tuple
    intervals: tuple


def build_domains(raw, cfg):
    echo = raw.observed & raw.good[:, None] & (raw.fields["DBZH"] >= cfg.minimum_echo_dbz)
    echo &= (raw.ranges >= cfg.range_min_m)[None, :] & (raw.ranges < cfg.range_max_m)[None, :]
    ids = np.zeros(raw.shape, "uint32")
    records, domains = [], []
    for ray in range(raw.shape[0]):
        groups = []
        for lo, hi in runs(echo[ray]):
            if groups:
                prev = groups[-1]
                start, end = prev[0][0], prev[-1][1]
                gap = (lo-end) * raw.dr
                measured = sum(b-a for a, b in prev) + hi-lo
                # Only original missing can link identity. Observed low echo is a barrier.
                empty = not raw.observed[ray, end:lo].any()
                fraction = 1 - measured / (hi-start)
                if empty and gap <= cfg.maximum_identity_gap_m and fraction <= cfg.maximum_identity_gap_fraction:
                    prev.append((lo, hi))
                    continue
            groups.append([(lo, hi)])
        for parts in groups:
            lo, hi = parts[0][0], parts[-1][1]
            if (hi-lo)*raw.dr < cfg.minimum_domain_span_m:
                continue
            if len(domains) >= cfg.maximum_domains:
                raise ValueError("domain budget exceeded; no partial result")
            idx = np.concatenate([np.arange(a, b) for a, b in parts])
            identity = len(domains)+1
            ids[ray, idx] = identity
            bins = np.floor((raw.ranges[idx]-cfg.range_min_m)/cfg.block_m).astype(int)
            blocks = tuple(int(x) for x in np.unique(bins))
            domains.append(Domain(identity, ray, lo, hi, idx, blocks, tuple(parts)))
            records.append({"domain_id": identity, "ray": ray, "gate_intervals": parts,
                            "span_m": (hi-lo)*raw.dr, "measured_m": len(idx)*raw.dr,
                            "missing_m": (hi-lo-len(idx))*raw.dr,
                            "semantics": "bounded_raw_structure_not_pollution_label"})
    # Evidence graph joins adjacent *actually overlapping* domains. It never shares
    # training samples across rays or propagates an action across the edge.
    parent = list(range(len(domains)+1))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    edges, delta, _spacing = edge_geometry(raw, cfg)
    links = []
    for ray in np.flatnonzero(edges & (delta <= cfg.bundle_maximum_angle_deg)):
        nr = (ray+1) % raw.shape[0]
        valid = (ids[ray] > 0) & (ids[nr] > 0)
        if not valid.any():
            continue
        pairs, count = np.unique(np.stack((ids[ray, valid], ids[nr, valid]), axis=1), axis=0, return_counts=True)
        for (a, b), c in zip(pairs, count, strict=True):
            if c*raw.dr >= cfg.bundle_minimum_overlap_m:
                a, b = int(a), int(b)
                parent[find(max(a,b))] = find(min(a,b))
                links.append({"a": a, "b": b, "measured_overlap_m": int(c)*raw.dr,
                              "action_propagation": False, "reference_sharing": False})
    root_map, next_id = {}, 1
    bundle_lookup = np.zeros(len(domains)+1, "uint32")
    for i in range(1, len(domains)+1):
        p = find(i)
        if p not in root_map:
            root_map[p] = next_id
            next_id += 1
        bundle_lookup[i] = root_map[p]
    for rec in records:
        rec["bundle_id"] = int(bundle_lookup[rec["domain_id"]])
    return domains, ids, bundle_lookup[ids], records, links


def local_angular_width(mask, raw, cfg):
    """Width of actual contiguous supported rays at each gate; no missing fill."""
    edges, delta, spacing = edge_geometry(raw, cfg)
    out = np.full(raw.shape, np.nan, "float32")
    n = raw.shape[0]
    for gate in np.flatnonzero(mask.any(axis=0)):
        active = mask[:, gate]
        if active.all() and edges.all():
            out[:, gate] = 360.0
            continue
        seen = np.zeros(n, bool)
        for first in np.flatnonzero(active):
            if seen[first]:
                continue
            lo = int(first)
            for _ in range(n):
                prev = (lo-1) % n
                if not (active[prev] and edges[prev]) or prev == first:
                    break
                lo = prev
            members, width, p = [], 0.0, lo
            for _ in range(n):
                members.append(p)
                seen[p] = True
                nxt = (p+1) % n
                if not (edges[p] and active[nxt]) or nxt == lo:
                    break
                width += delta[p]
                p = nxt
            out[members, gate] = width + spacing
    return out
