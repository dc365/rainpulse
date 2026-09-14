"""Physical-area residual review. Sparse appearance is never sufficient evidence."""

from __future__ import annotations

import numpy as np
from scipy import ndimage

from .polar_objects import gate_area_km2, label_polar


def speckle_candidates(native, cfg, *, baseline_eligible, protected, pol_bad, low_snr):
    observed = native.field_available["DBZH"] & native.geometry_good[:, None]
    z = native.fields["DBZH"]
    raw_echo = observed & (z >= cfg.minimum_echo_dbz)
    residual = raw_echo & baseline_eligible
    labels, total = label_polar(residual, native)
    if total > cfg.maximum_objects:
        raise ValueError("V6 speckle object budget exceeded; no partial decision")
    area = gate_area_km2(native)
    mode = ("wrap" if native.full_ppi else "constant", "constant")
    size = (3, cfg.speckle_window_gates)
    obs_fraction = ndimage.uniform_filter(observed.astype(float), size=size, mode=mode)
    echo_fraction = ndimage.uniform_filter(raw_echo.astype(float), size=size, mode=mode)
    echo_fraction = np.divide(
        echo_fraction, obs_fraction, out=np.full(native.shape, np.nan), where=obs_fraction > 0
    )
    # Discontinuous angular topology invalidates the neighbourhood; no missing=clear-air.
    topo = native.support(np.ones(native.shape, bool), 1, 0)
    supported = topo & (obs_fraction >= cfg.speckle_observation_fraction)
    candidate = np.zeros(native.shape, bool)
    ids = np.zeros(native.shape, "uint32")
    area_field = np.full(native.shape, np.nan, "float32")
    records = []
    for identity, box in enumerate(ndimage.find_objects(labels), 1):
        if box is None:
            continue
        region = labels[box] == identity
        rr, gg = np.where(region)
        local_area = float(area[box][region].sum())
        radial_span = float((gg.max() - gg.min() + 1) * native.gate_spacing_m)
        maximum = float(np.max(z[box][region]))
        if (
            local_area > cfg.speckle_maximum_area_km2
            or radial_span > cfg.speckle_maximum_span_m
            or maximum >= cfg.speckle_maximum_dbz
            or protected[box][region].any()
        ):
            continue
        evidence = (pol_bad[box] | low_snr[box]) & supported[box]
        evidence &= echo_fraction[box] <= cfg.speckle_maximum_raw_echo_fraction
        chosen = region & evidence
        if not chosen.any():
            continue
        # Each selected gate needs raw noise evidence; other gates in the same blob stay.
        candidate[box] |= chosen
        ids[box][chosen] = len(records) + 1
        area_field[box][chosen] = local_area
        records.append(
            dict(
                object_id=len(records) + 1,
                area_km2=local_area,
                radial_span_m=radial_span,
                maximum_dbz=maximum,
                reviewed_gates=int(region.sum()),
                candidate_gates=int(chosen.sum()),
            )
        )
    return {
        "V6_SPECKLE_CANDIDATE_MASK": candidate.astype("uint8"),
        "V6_SPECKLE_OBJECT_ID": ids,
        "V6_SPECKLE_AREA_KM2": area_field,
        "V6_RAW_NEIGHBOUR_OBS_FRACTION": np.where(observed, obs_fraction, np.nan).astype("float32"),
        "V6_RAW_NEIGHBOUR_ECHO_FRACTION": np.where(observed, echo_fraction, np.nan).astype(
            "float32"
        ),
    }, dict(
        method="raw-supported-physical-speckle-v6",
        objects=records,
        candidate_gates=int(candidate.sum()),
        status="applied",
    )
