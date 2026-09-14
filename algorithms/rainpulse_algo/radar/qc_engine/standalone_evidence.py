"""Non-circular Stage A used by CURRENT and REFERENCE observations alike.

No neighbouring final QC, environment reads, network or publication. The result
is local measurement evidence, not a statement that missing data mean no rain.
Content identity includes actual matrices, profile and this module's code family.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import time

import numpy as np

from ..qc_geometry import nearest_azimuth_matches
from .algorithms import library_evidence
from .crossradar import fuse_crossradar
from .decision import Action, decide
from .fingerprints import array_digest
from .hypotheses import arbitrate_hypotheses, build_hypotheses
from .objects import radial_objects
from .paper_fusion import fuse_paper_decision, paper_evidence
from .radial import local_radial_candidates
from .range_signature import range_signatures
from .residual import residual_decision


@dataclass
class StandaloneEvidence:
    identity: str
    library: object
    objects: object
    radial: np.ndarray
    radial_record: dict
    papers: object
    range_evidence: object
    decision: object
    donor_usable: np.ndarray
    donor_unknown: np.ndarray
    temporal_candidate: np.ndarray
    temporal_available: np.ndarray
    summary: dict


def stage_a_key(native, profile, local_prior=None):
    folder = Path(__file__).parent
    code = {f: hashlib.sha256((folder/f).read_bytes()).hexdigest() for f in (
        "standalone_evidence.py", "algorithms.py", "adapters.py", "objects.py",
        "decision.py", "paper_fusion.py", "afl.py", "crossradar.py", "range_signature.py",
        "residual.py", "narrow_local.py", "narrow_spike.py", "residual_association.py",
        "hypotheses.py", "speckle_review.py", "segments.py", "polar_objects.py",
    )}
    fields = {k: array_digest(v) for k,v in sorted(native.fields.items())}
    availability = {k: array_digest(v) for k,v in sorted(native.field_available.items())}
    identity = {"method": "independent-stage-a-v7", "profile": profile.parameters_hash,
                "libraries": [profile.arm_pyart_version, profile.wradlib_version],
                "code": code, "fields": fields, "available": availability,
                "azimuth": array_digest(native.azimuth), "range": array_digest(native.ranges),
                "elevation": array_digest(native.elevation), "geometry": array_digest(native.geometry_good),
                "gaps": array_digest(native.gap_after), "cut": native.name,
                "ray_order": array_digest(native.original_indices),
                "cut_metadata": native.audit.get("cut_metadata"),
                "static_prior": None if local_prior is None else array_digest(local_prior)}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def stage_a(native, profile, local_prior=None):
    started = time.perf_counter()
    key = stage_a_key(native, profile, local_prior)
    evidence = library_evidence(native, profile, local_prior)
    objects = radial_objects(native, profile.rfi_objects) if profile.rfi_objects is not None else None
    if objects is None:
        radial, record = local_radial_candidates(native, profile.rfi)
    else:
        radial, record = objects.candidate, objects.summary()
    result = decide(native, evidence, profile, rfi_candidate=radial,
                    clutter_prior=local_prior, object_evidence=objects)
    papers = paper_evidence(native, profile) if profile.literature is not None else None
    if papers is not None:
        result = fuse_paper_decision(native, evidence, result, papers, profile)
    signatures = None
    if profile.cross_radar is not None:
        signatures = range_signatures(native, profile.cross_radar)
        result = fuse_crossradar(native, result, signatures, profile)
    if profile.residual is not None:
        result, _ = residual_decision(native, result, profile)
    graph = None
    if profile.evidence_graph is not None and profile.evidence_graph.graph_enabled:
        graph = build_hypotheses(native, result, profile)
        result = arbitrate_hypotheses(native, result, graph, profile)
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    proposed = radial.copy()
    for name in ("PAPER_CANDIDATE_MASK", "V5_RANGE_CANDIDATE_MASK", "V6_NARROW_CANDIDATE_MASK",
                 "V7_GRAPH_REVIEW_MASK"):
        proposed |= result.arrays.get(name, np.zeros(native.shape)) == 1
    # Shape-only unresolved observations ABSTAIN; they do not vote for clear air.
    uncertain = (proposed | (result.arrays["QC_ACTION"] != Action.KEEP)) & observed
    usable = observed & ~uncertain & (result.arrays["QPE_ELIGIBLE_MASK"] == 1)
    unknown = observed & ~usable & (result.arrays["QC_ACTION"] != Action.REJECT)
    # Negative recurrence votes require actual evaluability. Missing RHOHV and
    # shoulders cannot create a false "no interference" vote.
    background = result.arrays.get("RFI_BACKGROUND_AVAILABLE_MASK", np.zeros(native.shape)) == 1
    temporal_available = observed & (background | native.field_available.get("RHOHV", False) | proposed)
    summary = {"method": "independent-stage-a-v7", "identity": key,
               "donor_usable_gates": int(usable.sum()), "donor_unknown_gates": int(unknown.sum()),
               "rejected_gates": int((result.arrays["QC_ACTION"] == Action.REJECT).sum()),
               "local_prior_status": "available" if local_prior is not None else "not_supplied",
               "temporal_meaning": "shape_recurrence_not_independent_confirmation",
               "elapsed_ms": (time.perf_counter()-started)*1000}
    # Reused within a frozen task; never cache a Stage B result across cutoffs.
    for a in (usable, unknown, proposed, temporal_available):
        a.setflags(write=False)
    return StandaloneEvidence(key, evidence, objects, radial, record, papers,
                              signatures, result, usable, unknown, proposed,
                              temporal_available, summary)


def aggregate_stage_a_temporal(current, past):
    if len(past) > 3:
        raise ValueError("at most 3 unique physical temporal observations")
    count = np.zeros(current.shape, "uint8")
    votes = count.copy()
    for native, result in past:
        if current.name != native.name or not np.array_equal(current.ranges, native.ranges):
            continue
        if current.audit.get("cut_metadata") != native.audit.get("cut_metadata"):
            continue
        ix, delta, ok = nearest_azimuth_matches(current.azimuth, native.azimuth)
        tolerance = min(current.audit["azimuth_spacing_deg"], native.audit["azimuth_spacing_deg"])*0.45
        ok &= delta <= tolerance
        ok &= current.geometry_good & native.geometry_good[ix]
        ok &= np.abs(current.elevation-native.elevation[ix]) <= 0.1
        measured = (result.temporal_available[ix] & ok[:,None]
                    & current.field_available["DBZH"])
        count += measured.astype("uint8")
        votes += (measured & result.temporal_candidate[ix]).astype("uint8")
    persistence = np.divide(votes, count, out=np.full(current.shape, np.nan, "float32"), where=count>0)
    return {"TEMPORAL_CANDIDATE_PERSISTENCE": persistence, "TEMPORAL_RFI_SAMPLE_COUNT": count}
