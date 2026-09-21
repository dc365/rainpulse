"""P3: max over actual eligible observations, with winner/runner-up receipts."""
from dataclasses import dataclass
import hashlib
import numpy as np
from pyproj import Geod
from .geometry import EARTH
from .sampling import polar_targets
from .receipts import npz_bytes, snapshot_group
from .data import array_digest, json_bytes


@dataclass
class Composite:
    arrays: dict
    bounds: list
    sources: list


def build_composite(roots,reject_mask,*,maximum_size=1200,sites=None):
    if not roots or not 16<=maximum_size<=1200:
        raise ValueError("nonempty volume list and bounded grid required")
    clutter_ids = {r.attrs.get("qc_clutter_fusion_sha256") for r in roots}
    if len(clutter_ids) != 1:
        raise ValueError("CR clutter-fusion generations are mixed")
    clutter_active = None not in clutter_ids
    if clutter_active:
        from .clutter_fusion.config import ClutterFusionConfig
        for root in roots:
            cfg = ClutterFusionConfig.model_validate(root.attrs.get("qc_clutter_fusion_config", {}))
            if cfg.digest != root.attrs.get("qc_clutter_fusion_sha256"):
                raise ValueError("CR clutter-fusion config identity differs")
    near_joint_active = clutter_active and roots[0].attrs["qc_clutter_fusion_config"].get("near_revision") is not None
    receiver_ids = {r.attrs.get("qc_receiver_domain_sha256") for r in roots}
    if len(receiver_ids) != 1:
        raise ValueError("CR receiver-domain generations are mixed")
    receiver_active = None not in receiver_ids
    family_configs = [r.attrs.get("qc_receiver_domain_config", {}).get("source_family") for r in roots]
    family_active = any(c is not None for c in family_configs)
    if family_active:
        from .receiver_domain.config import ReceiverDomainConfig
        if not receiver_active or any(c is None for c in family_configs):
            raise ValueError("CR source-family generations are mixed")
        for root in roots:
            cfg = ReceiverDomainConfig.model_validate(root.attrs.get("qc_receiver_domain_config", {}))
            if cfg.digest != root.attrs.get("qc_receiver_domain_sha256"):
                raise ValueError("CR source-family configuration identity differs")
    near_ids = {r.attrs.get("qc_near_measurement_sha256") for r in roots}
    if len(near_ids) != 1:
        raise ValueError("CR near-measurement generations are mixed")
    near_active = None not in near_ids
    configs={r.attrs.get("qc_volume_review_sha256") for r in roots}
    if None in configs or len(configs)!=1:
        raise ValueError("mixed/missing volume-review configuration in CR")
    if any(r.attrs.get("qc_volume_review_phase")!=3 for r in roots):
        raise ValueError("CR review requires P3 on every participating volume")
    geod=Geod(ellps="WGS84"); footprints=[]; sources=[]; sweeps=[]; ids=set()
    for rid,root in enumerate(roots):
        for key in ("radar_id","scan_id","asset_id","qc_parameters_sha256"):
            if not root.attrs.get(key):
                raise ValueError("unidentified CR input: "+key)
        for key in ("qc_parameters_sha256","qc_volume_review_sha256"):
            h=root.attrs[key]
            if not isinstance(h,str) or len(h)!=64 or any(c not in '0123456789abcdef' for c in h):
                raise ValueError("invalid CR input digest: "+key)
        station=root.attrs.get("radar_id"); site=(sites or {}).get(station)
        lon=float(site["longitude_deg"] if site else root.attrs["site_longitude_deg"])
        lat=float(site["latitude_deg"] if site else root.attrs["site_latitude_deg"])
        if not np.isfinite([lon,lat]).all() or not -180<=lon<=180 or not -90<lat<90:
            raise ValueError("invalid site geometry")
        radius=max(float(root[f"sweep_{int(n):03d}"]["range"][-1]) for n in root["sweep_number"][:])
        x,y,_=geod.fwd(np.full(360,lon),np.full(360,lat),np.arange(360),np.full(360,radius))
        footprints.append((float(min(x)),float(min(y)),float(max(x)),float(max(y))))
        for number in root["sweep_number"][:]:
            number=int(number); key=f"sweep_{number:03d}"; group=root[key]
            if "DBZH_QC" not in group:
                continue
            identity=(station,root.attrs.get("scan_id"),number)
            if identity in ids:
                raise ValueError("duplicate volume/sweep contributor")
            ids.add(identity)
            a=snapshot_group(group)
            for required in ("REFLECTIVITY_ELIGIBLE_FOR_CR","CR_UNCERTAIN_MASK","VALID_MASK","QC_FLAGS"):
                if required not in a:
                    raise ValueError("CR lacks qualification field "+required)
            for k in ("REFLECTIVITY_ELIGIBLE_FOR_CR","CR_UNCERTAIN_MASK","VALID_MASK"):
                if a[k].shape!=a["DBZH_QC"].shape or not np.isin(a[k],(0,1)).all():
                    raise ValueError("invalid CR binary field "+k)
            if clutter_active:
                for ck in ("CF_NONMET_SUPPORTED_MASK", "CF_MIXED_MASK", "CF_CR_WITHHELD_MASK"):
                    if ck not in a or a[ck].shape != a["DBZH_QC"].shape or not np.isin(a[ck], (0,1)).all():
                        raise ValueError("CR missing clutter evidence " + ck)
                if near_joint_active:
                    for key in ("CF_NR_ACTION_MASK","CF_NR_DEM_ACTION_MASK","CF_NR_CR_WITHHELD_MASK","CF_NR_DEM_CR_WITHHELD_MASK"):
                        if key not in a or a[key].shape != a["DBZH_QC"].shape or not np.isin(a[key],(0,1)).all():
                            raise ValueError("CR missing near joint evidence " + key)
                    for key in ("CF_NR_CR_WITHHELD_MASK","CF_NR_DEM_CR_WITHHELD_MASK"):
                        if np.any((a[key]==1)&(a["REFLECTIVITY_ELIGIBLE_FOR_CR"]==1)):
                            raise ValueError("near joint/DEM contribution leaked into CR")
                if np.any((a["CF_CR_WITHHELD_MASK"] == 1) & (a["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1)):
                    raise ValueError("clutter withheld contribution leaked into CR")
            if near_active:
                for key in ("NMR_NONMET_CANDIDATE_MASK", "NMR_LOW_SNR_UNCERTAIN_MASK", "NMR_CR_WITHHELD_MASK"):
                    if key not in a or a[key].shape != a["DBZH_QC"].shape or not np.isin(a[key], (0, 1)).all():
                        raise ValueError("CR missing near evidence: " + key)
                if np.any((a["NMR_CR_WITHHELD_MASK"] == 1) & (a["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1)):
                    raise ValueError("near withheld measurement leaked into CR")
            if receiver_active:
                for key in ("RDR_SOURCE_MASK", "RDR_PARTIAL_MATCH_MASK", "RDR_MIXED_MASK", "RDR_CR_WITHHELD_MASK"):
                    if key not in a or a[key].shape != a["DBZH_QC"].shape or not np.isin(a[key], (0,1)).all():
                        raise ValueError("CR missing receiver evidence " + key)
                if np.any((a["RDR_CR_WITHHELD_MASK"] == 1) & (a["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1)):
                    raise ValueError("receiver withheld measurement leaked into CR")
            if family_active:
                for key in ("RDR_FAMILY_REFERENCE_MASK", "RDR_FAMILY_UNRESOLVED_MASK", "RDR_FAMILY_CR_WITHHELD_MASK"):
                    if key not in a or a[key].shape != a["DBZH_QC"].shape or not np.isin(a[key], (0, 1)).all():
                        raise ValueError("CR missing source-family evidence " + key)
                for key in ("RDR_FAMILY_OBJECT_ID", "RDR_FAMILY_REASON"):
                    if key not in a or a[key].shape != a["DBZH_QC"].shape or a[key].dtype != np.dtype("uint32"):
                        raise ValueError("CR missing source-family provenance " + key)
                if np.any((a["RDR_FAMILY_CR_WITHHELD_MASK"] == 1) & (a["REFLECTIVITY_ELIGIBLE_FOR_CR"] == 1)):
                    raise ValueError("source-family withheld contribution leaked into CR")
            sources.append({"radar":rid,"radar_id":station,"scan_id":root.attrs.get("scan_id"),"sweep":number,
                            "qc_asset_id":root.attrs.get("asset_id"),"qc_parameters_sha256":root.attrs.get("qc_parameters_sha256"),
                            "numeric_sha256":array_digest(a),"height_datum":"above_source_radar_effective_4_3_earth"})
            sweeps.append((len(sources)-1,lon,lat,a))
    west,south,east,north=min(x[0] for x in footprints),min(x[1] for x in footprints),max(x[2] for x in footprints),max(x[3] for x in footprints)
    if east-west>180:
        raise ValueError("dateline spanning CR unsupported")
    step=max(.01,(east-west)/(maximum_size-2),(north-south)/(maximum_size-2))
    west,south=np.floor(west/step)*step,np.floor(south/step)*step
    width,height=int(np.ceil((east-west)/step)),int(np.ceil((north-south)/step))
    east,north=west+width*step,south+height*step
    shape=(height,width)
    arrays={k:np.full(shape,np.nan,"float32") for k in ("CR_RAW","CR_TRUSTED","CR_UNCERTAIN","CR_RUNNER_UP","WINNER_HEIGHT_ABOVE_RADAR_M")}
    arrays.update({k:np.full(shape,-1,"int32") for k in ("WINNER_SOURCE","WINNER_RAY","WINNER_GATE","RUNNER_UP_SOURCE","RUNNER_UP_RAY","RUNNER_UP_GATE")})
    arrays["WINNER_REASON"]=np.zeros(shape,"uint16")
    if clutter_active:
        arrays.update({k: np.full(shape, np.nan, "float32") for k in (
            "CR_CLUTTER_CANDIDATE", "CR_CLUTTER_MIXED", "CR_CLUTTER_WITHHELD")})
        arrays["WINNER_CLUTTER_CLASS"] = np.zeros(shape, "uint8")
        arrays["WINNER_CLUTTER_REASON"] = np.zeros(shape, "uint16")
    if near_joint_active:
        arrays.update({k:np.full(shape,np.nan,"float32") for k in
            ("CR_NEAR_JOINT_NONMET","CR_NEAR_JOINT_WITHHELD","CR_TERRAIN_UNRELIABLE","WINNER_CUMULATIVE_BLOCKAGE")})
        arrays["WINNER_NEAR_JOINT_STATE"]=np.zeros(shape,"uint8")
        arrays["WINNER_NEAR_JOINT_REASON"]=np.zeros(shape,"uint16")
    if near_active:
        arrays.update({k: np.full(shape, np.nan, "float32") for k in (
            "CR_NEAR_NONMET_CANDIDATE", "CR_NEAR_LOW_RELIABILITY", "CR_NEAR_WITHHELD")})
    if receiver_active:
        arrays.update({k: np.full(shape, np.nan, "float32") for k in (
            "CR_RECEIVER_SOURCE", "CR_RECEIVER_PARTIAL", "CR_RECEIVER_MIXED", "CR_RECEIVER_WITHHELD")})
    if family_active:
        arrays.update({k: np.full(shape, np.nan, "float32") for k in (
            "CR_RECEIVER_FAMILY", "CR_RECEIVER_FAMILY_UNRESOLVED", "CR_RECEIVER_FAMILY_WITHHELD")})
        arrays["WINNER_RECEIVER_FAMILY_OBJECT_ID"] = np.zeros(shape, "uint32")
        arrays["WINNER_RECEIVER_FAMILY_REASON"] = np.zeros(shape, "uint32")
    for start in range(0,height,128):
        end=min(start+128,height); sl=np.s_[start:end,:]
        xx,yy=np.meshgrid(west+(np.arange(width)+.5)*step,north-(np.arange(start,end)+.5)*step)
        for sid,lon,lat,a in sweeps:
            angle,_,distance=geod.inv(np.full(xx.shape,lon),np.full(xx.shape,lat),xx,yy)
            # Row selection depends only on azimuth, then invert using its actual elevation.
            ray,_,_=polar_targets(a["azimuth"],a["range"],distance,angle)
            elev=np.deg2rad(a["elevation"][ray]); arc=distance/EARTH
            denominator=np.cos(elev+arc)
            slant=EARTH*np.sin(arc)/np.maximum(denominator,1e-6)
            ray,gate,foot=polar_targets(a["azimuth"],a["range"],slant,angle)
            foot &= denominator>0
            raw=a["DBZH_RAW"][ray,gate]; value=a["DBZH_QC"][ray,gate]
            observed=foot & (a["VALID_MASK"][ray,gate]==1) & np.isfinite(value)
            trusted=observed & (a["REFLECTIVITY_ELIGIBLE_FOR_CR"][ray,gate]==1) & ((a["QC_FLAGS"][ray,gate]&np.uint32(reject_mask))==0)
            unknown=observed & (a["CR_UNCERTAIN_MASK"][ray,gate]==1)
            arrays["CR_RAW"][sl]=np.fmax(arrays["CR_RAW"][sl],np.where(foot,raw,np.nan))
            arrays["CR_UNCERTAIN"][sl]=np.fmax(arrays["CR_UNCERTAIN"][sl],np.where(unknown,value,np.nan))
            if near_active:
                for target, source_mask in (("CR_NEAR_NONMET_CANDIDATE", "NMR_NONMET_CANDIDATE_MASK"),
                        ("CR_NEAR_LOW_RELIABILITY", "NMR_LOW_SNR_UNCERTAIN_MASK"),
                        ("CR_NEAR_WITHHELD", "NMR_CR_WITHHELD_MASK")):
                    selected = observed & (a[source_mask][ray,gate] == 1)
                    arrays[target][sl] = np.fmax(arrays[target][sl], np.where(selected, value, np.nan))
            if receiver_active:
                for target, mask in (("CR_RECEIVER_SOURCE", "RDR_SOURCE_MASK"), ("CR_RECEIVER_PARTIAL", "RDR_PARTIAL_MATCH_MASK"),
                        ("CR_RECEIVER_MIXED", "RDR_MIXED_MASK"), ("CR_RECEIVER_WITHHELD", "RDR_CR_WITHHELD_MASK")):
                    selected = observed & (a[mask][ray,gate] == 1)
                    arrays[target][sl] = np.fmax(arrays[target][sl], np.where(selected,value,np.nan))
            if family_active:
                for target, mask in (("CR_RECEIVER_FAMILY", "RDR_FAMILY_REFERENCE_MASK"),
                        ("CR_RECEIVER_FAMILY_UNRESOLVED", "RDR_FAMILY_UNRESOLVED_MASK"),
                        ("CR_RECEIVER_FAMILY_WITHHELD", "RDR_FAMILY_CR_WITHHELD_MASK")):
                    selected = observed & (a[mask][ray,gate] == 1)
                    arrays[target][sl] = np.fmax(arrays[target][sl], np.where(selected, value, np.nan))
            if clutter_active:
                for target, mask in (("CR_CLUTTER_CANDIDATE", "CF_NONMET_SUPPORTED_MASK"),
                        ("CR_CLUTTER_MIXED", "CF_MIXED_MASK"), ("CR_CLUTTER_WITHHELD", "CF_CR_WITHHELD_MASK")):
                    selected = observed & (a[mask][ray,gate] == 1)
                    arrays[target][sl] = np.fmax(arrays[target][sl], np.where(selected, value, np.nan))
            if near_joint_active:
                for target,mask in (("CR_NEAR_JOINT_NONMET","CF_NR_ACTION_MASK"),
                                    ("CR_NEAR_JOINT_WITHHELD","CF_NR_CR_WITHHELD_MASK"),
                                    ("CR_TERRAIN_UNRELIABLE","CF_NR_DEM_ACTION_MASK")):
                    selected=observed&(a[mask][ray,gate]==1)
                    arrays[target][sl]=np.fmax(arrays[target][sl],np.where(selected,value,np.nan))
            old=arrays["CR_TRUSTED"][sl]
            wins=trusted & (~np.isfinite(old)|(value>old))
            for target,source in (("CR_RUNNER_UP","CR_TRUSTED"),("RUNNER_UP_SOURCE","WINNER_SOURCE"),
                                  ("RUNNER_UP_RAY","WINNER_RAY"),("RUNNER_UP_GATE","WINNER_GATE")):
                arrays[target][sl][wins]=arrays[source][sl][wins]
            seconds=trusted & ~wins & (~np.isfinite(arrays["CR_RUNNER_UP"][sl])|(value>arrays["CR_RUNNER_UP"][sl]))
            arrays["CR_RUNNER_UP"][sl][seconds]=value[seconds]
            arrays["RUNNER_UP_SOURCE"][sl][seconds]=sid
            arrays["RUNNER_UP_RAY"][sl][seconds]=ray[seconds]; arrays["RUNNER_UP_GATE"][sl][seconds]=gate[seconds]
            arrays["CR_TRUSTED"][sl][wins]=value[wins]
            arrays["WINNER_SOURCE"][sl][wins]=sid; arrays["WINNER_RAY"][sl][wins]=ray[wins]; arrays["WINNER_GATE"][sl][wins]=gate[wins]
            if clutter_active:
                arrays["WINNER_CLUTTER_CLASS"][sl][wins] = a["CF_CLASS"][ray,gate][wins]
                arrays["WINNER_CLUTTER_REASON"][sl][wins] = a["CF_REASON"][ray,gate][wins]
            if near_joint_active:
                for target,source in (("WINNER_NEAR_JOINT_STATE","CF_NR_STATE"),
                                      ("WINNER_NEAR_JOINT_REASON","CF_NR_REASON"),
                                      ("WINNER_CUMULATIVE_BLOCKAGE","CF_NR_DEM_CBB")):
                    arrays[target][sl][wins]=a[source][ray,gate][wins]
            if family_active:
                arrays["WINNER_RECEIVER_FAMILY_OBJECT_ID"][sl][wins] = a["RDR_FAMILY_OBJECT_ID"][ray,gate][wins]
                arrays["WINNER_RECEIVER_FAMILY_REASON"][sl][wins] = a["RDR_FAMILY_REASON"][ray,gate][wins]
            rr=a["range"][gate]; ee=np.deg2rad(a["elevation"][ray])
            h=np.sqrt(EARTH**2+rr*rr+2*EARTH*rr*np.sin(ee))-EARTH
            arrays["WINNER_HEIGHT_ABOVE_RADAR_M"][sl][wins]=h[wins]
            if "CR_QUALIFICATION_REASON" in a:
                arrays["WINNER_REASON"][sl][wins]=a["CR_QUALIFICATION_REASON"][ray,gate][wins]
    arrays["CR_VALID_MASK"]=np.isfinite(arrays["CR_TRUSTED"]).astype("uint8")
    arrays["CR_UNCERTAIN_COVERAGE_MASK"]=np.isfinite(arrays["CR_UNCERTAIN"]).astype("uint8")
    return Composite(arrays,[float(v) for v in (west,south,east,north)],sources)


def trace_pixel(product,roots,row,column):
    if not 0<=row<product.arrays["CR_TRUSTED"].shape[0] or not 0<=column<product.arrays["CR_TRUSTED"].shape[1]:
        raise ValueError("pixel outside grid")
    sid=int(product.arrays["WINNER_SOURCE"][row,column])
    if sid<0:
        return {"status":"NO_TRUSTED_OBSERVATION","not_zero_rain":True}
    info=product.sources[sid]; group=roots[info["radar"]][f"sweep_{info['sweep']:03d}"]
    if array_digest(snapshot_group(group))!=info["numeric_sha256"]:
        raise ValueError("trace source has changed")
    ray=int(product.arrays["WINNER_RAY"][row,column]); gate=int(product.arrays["WINNER_GATE"][row,column])
    value=float(group["DBZH_QC"][ray,gate])
    if value!=float(product.arrays["CR_TRUSTED"][row,column]) or group["REFLECTIVITY_ELIGIBLE_FOR_CR"][ray,gate]!=1:
        raise ValueError("winner does not reconstruct")
    return {"status":"RECONSTRUCTED",**info,"ray":ray,"gate":gate,"value_dbz":value,
            "height_above_source_radar_m":float(product.arrays["WINNER_HEIGHT_ABOVE_RADAR_M"][row,column])}


class BeforeVolumeRoot:
    """Reconstruct parent CR inputs after an all-volume enhancement abstention."""
    def __init__(self,root):
        self.root=root;self.attrs=root.attrs
    def __getitem__(self,key):
        value=self.root[key]
        if key.startswith("sweep_") and key[6:].isdigit():
            from .validation import LegacyView
            return LegacyView(value)
        return value


def diagnostic_composite(roots,reject_mask,*,objects,sites=None,legacy_compositor=None):
    if legacy_compositor is None:
        from ....diagnostics.composite import composite_reflectivity
        legacy_compositor=composite_reflectivity
    old,bounds=legacy_compositor(roots,reject_mask,sites=sites)
    active=[r.attrs.get("qc_volume_review_phase")==3 for r in roots]
    if not any(active):
        return old,bounds,{}
    if not all(active):
        raise ValueError("P3 CR input generations are mixed")
    modes={r.attrs.get("qc_volume_review_mode") for r in roots}
    if len(modes)!=1 or not modes.issubset({"audit","experiment_quarantine"}):
        raise ValueError("P3 CR modes differ or are undefined")
    product=build_composite(roots,reject_mask,sites=sites)
    payload=npz_bytes(product.arrays); path="volume_review/composite.npz"
    objects[path]=payload
    meta={"schema":"rainpulse.cr-source-receipt-v1","bounds":product.bounds,"sources":product.sources,
          "payload_sha256":hashlib.sha256(payload).hexdigest(),"numeric_sha256":array_digest(product.arrays),
          "aggregation":"maximum_eligible_over_sweeps_and_radars", "operational_eligible":False,
          "winner_tie_policy":"first_in_recorded_source_order", "unknown_is_clear_air":False}
    objects["volume_review/composite.json"]=json_bytes(meta)
    failed=any(r.attrs.get("qc_volume_review_status")=="RESOURCE_ABSTAINED" for r in roots)
    if failed:
        old,bounds=legacy_compositor([BeforeVolumeRoot(r) for r in roots],reject_mask,sites=sites)
    experimental=modes=={"experiment_quarantine"} and not failed
    return (product.arrays["CR_TRUSTED"],product.bounds) + ({"cr_candidate_path":path,
            "cr_source_receipt_path":"volume_review/composite.json","cr_applied":experimental,
            "cr_qualification_version":"cr-eligibility-v1"},) if experimental else (old,bounds,{
            "cr_candidate_path":path,"cr_source_receipt_path":"volume_review/composite.json","cr_applied":False,
            "cr_fallback_reason":"RESOURCE_ABSTAINED_PARENT_RESTORED" if failed else "AUDIT_ONLY"})
