"""Immutable candidates -> receiver models -> dual-view review -> gate proposal.

No morphology-only actions, no model probability, no weak-moment deletions,
no negative evidence from missing layers, and no iterative mask growth.
"""
from dataclasses import dataclass
from enum import IntFlag
import numpy as np
from . import VERSION
from .data import checked_mask, ResourceLimit, json_bytes
from .objects import extract_objects, capability
from .source import fit_sources, local_weather
from .graph import associate


class Reason(IntFlag):
    RAW_OBJECT = 1
    TARGET_SOURCE_MODEL = 2
    CORROBORATED_SOURCE = 4
    LOCAL_WEATHER = 8
    SPATIAL_WEATHER = 16
    EXTERNAL_WEATHER = 32
    MIXED = 64
    INSUFFICIENT_EVIDENCE = 128
    EXPERIMENT_PROPOSAL = 256
    RESOURCE_ABSTAINED = 512


@dataclass
class VolumeEvidence:
    arrays: list
    objects: list
    models: list
    links: list
    summary: dict


def evaluate(sweeps, cfg, *, protected=None):
    sweeps = tuple(sweeps)
    if not sweeps or len({s.name for s in sweeps}) != len(sweeps):
        raise ValueError("unique nonempty volume sweep identities required")
    before = [s.digest for s in sweeps]
    if protected is None:
        protected = [np.zeros(s.shape, bool) for s in sweeps]
    if len(protected) != len(sweeps):
        raise ValueError("weather list geometry differs")
    protected = [checked_mask(v,s.shape,"protected") for s,v in zip(sweeps,protected,strict=True)]
    basic = {"version": VERSION, "phase": cfg.phase, "mode": cfg.mode, "config_sha256": cfg.digest,
             "operational_eligible": False, "raw_digests": before, "new_confirmed_gates": 0,
             "filled_gates": 0, "scores_are_probabilities": False}
    arrays, objects, models, caps, weather = [], [], [], [], []
    try:
        if sum(np.prod(s.shape) for s in sweeps) > cfg.maximum_gates:
            raise ResourceLimit("native volume gates")
        for s in sweeps:
            if cfg.phase:
                a, obj, cap = extract_objects(s, cfg)
            else:
                a, obj, cap = {"VOR_CANDIDATE_MASK": np.zeros(s.shape,"uint8"),
                               "VOR_OBJECT_ID": np.zeros(s.shape,"uint32"),
                               "VOR_RADIAL_GEOMETRY_MASK": np.zeros(s.shape,"uint8"),
                               "VOR_SCALE_BITS": np.zeros(s.shape,"uint16"),
                               "VOR_CONTOUR_COUNT": np.zeros(s.shape,"uint8")}, [], capability(s,cfg)
            radial_ids = [o["id"] for o in obj if o["radial_geometry"]]
            source_domain = (a["VOR_RADIAL_GEOMETRY_MASK"] == 1) & (a["VOR_CANDIDATE_MASK"] == 1)
            # Models are trained before association; subsequent graph state cannot retrain them.
            if cfg.phase >= 2:
                sf, rec, status = fit_sources(s, cfg, source_domain)
                w = local_weather(s,cfg)
            else:
                sf, rec, status = fit_sources(s, cfg, np.zeros(s.shape,bool))
                w = np.zeros(s.shape,bool)
            a.update(sf)
            cap["source_status"] = status if cfg.phase >= 2 else "DISABLED_BY_PHASE"
            arrays.append(a); objects.append(obj); models.append(rec); caps.append(cap); weather.append(w)
        linked, links = associate(sweeps, arrays, weather, cfg, objects=objects) if cfg.phase >= 2 else (
            [{"VOR_SOURCE_CORROBORATED_MASK": np.zeros(s.shape,"uint8"), "VOR_SPATIAL_WEATHER_MASK": np.zeros(s.shape,"uint8"),
              **{k: np.full(s.shape,-1,"int32") for k in ("VOR_DONOR_SWEEP","VOR_DONOR_RAY","VOR_DONOR_GATE")}} for s in sweeps], [])
        for i,s in enumerate(sweeps):
            a=arrays[i]; a.update(linked[i])
            candidate = a["VOR_CANDIDATE_MASK"] == 1
            source = (a["VOR_SOURCE_MATCH_MASK"] == 1) & (a["VOR_SOURCE_CORROBORATED_MASK"] == 1)
            wx = weather[i] | (a["VOR_SPATIAL_WEATHER_MASK"] == 1) | protected[i]
            mixed = candidate & source & wx
            supported = candidate & source & ~wx
            unknown = candidate & ~source & ~wx
            state = np.where(s.observed,1,0).astype("uint8")
            state[unknown]=2; state[wx & s.observed]=3; state[supported]=4; state[mixed]=5
            proposal = supported & (cfg.mode == "experiment_quarantine") & (cfg.phase >= 2)
            reason = np.zeros(s.shape,"uint16")
            for mask, bit in ((candidate, Reason.RAW_OBJECT), (a["VOR_SOURCE_MATCH_MASK"] == 1,Reason.TARGET_SOURCE_MODEL),
                              (a["VOR_SOURCE_CORROBORATED_MASK"] == 1,Reason.CORROBORATED_SOURCE),
                              (weather[i],Reason.LOCAL_WEATHER), (a["VOR_SPATIAL_WEATHER_MASK"] == 1,Reason.SPATIAL_WEATHER),
                              (protected[i],Reason.EXTERNAL_WEATHER), (mixed,Reason.MIXED),
                              (unknown,Reason.INSUFFICIENT_EVIDENCE), (proposal,Reason.EXPERIMENT_PROPOSAL)):
                reason[mask & s.observed] |= int(bit)
            a.update(VOR_STATE=state, VOR_REASON=reason, VOR_PROPOSAL_MASK=proposal.astype("uint8"),
                     VOR_WEATHER_MASK=(wx & s.observed).astype("uint8"),
                     VOR_UNKNOWN_MASK=((unknown|mixed)&s.observed).astype("uint8"))
            caps[i].update(source_supported_gates=int(supported.sum()), mixed_gates=int(mixed.sum()),
                           unknown_gates=int(unknown.sum()), proposed_gates=int(proposal.sum()))
        if [s.digest for s in sweeps] != before:
            raise RuntimeError("raw mutation detected")
        return VolumeEvidence(arrays, objects, models, links, {**basic,"status":"EVALUATED", "sweeps":caps,
                              "candidate_gates":sum(int(a["VOR_CANDIDATE_MASK"].sum()) for a in arrays),
                              "proposed_gates":sum(int(a["VOR_PROPOSAL_MASK"].sum()) for a in arrays),
                              "objects":sum(map(len,objects)),"reference_models":sum(map(len,models)),"links":len(links)})
    except ResourceLimit as exc:
        # Return a complete zero-action record for EVERY sweep, never partially applied work.
        arrays=[]
        for s in sweeps:
            a={k:np.zeros(s.shape,"uint8") for k in ("VOR_CANDIDATE_MASK","VOR_PROPOSAL_MASK","VOR_WEATHER_MASK", "VOR_UNKNOWN_MASK")}
            a["VOR_OBJECT_ID"]=np.zeros(s.shape,"uint32")
            a["VOR_STATE"]=np.where(s.observed,2,0).astype("uint8")
            a["VOR_REASON"]=np.where(s.observed,int(Reason.RESOURCE_ABSTAINED),0).astype("uint16")
            a["VOR_UNKNOWN_MASK"]=s.observed.astype("uint8")
            arrays.append(a)
        return VolumeEvidence(arrays,[],[],[],{**basic,"status":"RESOURCE_ABSTAINED","reason":str(exc),
                   "sweeps":[capability(s,cfg) for s in sweeps],"candidate_gates":0,"proposed_gates":0})
