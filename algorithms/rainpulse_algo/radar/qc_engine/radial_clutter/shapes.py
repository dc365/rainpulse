"""Directional morphology proposes structure, never confirmed pollution."""
from importlib.metadata import version

import numpy as np
from skimage.morphology import footprint_rectangle, opening

from ..narrow_local import local_widths
from .geometry import edge_geometry, measured, shifted_measured
from .segments import associate


def propose_radials(n, cfg):
    if version("scikit-image") != "0.26.0":
        raise RuntimeError("RC1 requires scikit-image 0.26.0")
    observed = measured(n, "DBZH") & n.geometry_good[:, None]
    z = n.fields["DBZH"]
    echo = observed & (z >= cfg.radial_echo_dbz)
    scales = np.zeros(n.shape, "uint8")
    for bit, length_m in enumerate(cfg.radial_lengths_m):
        length = int(np.ceil(length_m / n.gate_spacing_m)) | 1
        if length <= n.shape[1]:
            hit = opening(echo, footprint_rectangle((1, length)), mode="constant", cval=0)
            scales |= (hit & echo).astype("uint8") * (1 << bit)
    associated = associate(echo, observed, n.gate_spacing_m, cfg)
    proposal = echo & ((scales > 0) | associated)
    width = local_widths(proposal, n)
    proposal &= width <= cfg.maximum_radial_width_deg
    _, _, spacing = edge_geometry(n)
    flank = np.zeros(n.shape, bool)
    contrast = np.full(n.shape, np.nan, "float32")
    for angle in cfg.shoulder_offsets_deg:
        offset = max(1, int(round(angle / spacing)))
        if offset >= n.shape[0] / 2:
            continue
        left, lok = shifted_measured(n, -offset)
        right, rok = shifted_measured(n, offset)
        available = observed & observed[left] & observed[right] & (lok & rok)[:, None]
        value = z - np.maximum(z[left], z[right])
        better = available & (~flank | (value > contrast))
        contrast[better] = value[better]
        flank |= available
    return {"RC1_RADIAL_CANDIDATE_MASK": proposal.astype("uint8"),
            "RC1_RADIAL_SCALES": scales,
            "RC1_RADIAL_WIDTH_DEG": width,
            "RC1_SHOULDER_AVAILABLE_MASK": flank.astype("uint8"),
            "RC1_SHOULDER_CONTRAST_DB": contrast}
