"""Same-palette, nearest-native-gate comparison panels; never source observations."""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np

from rainpulse_algo.diagnostics.png import encode_rgba_png
from rainpulse_algo.diagnostics.renderer import REFLECTIVITY_STOPS, _scalar_rgba
from rainpulse_algo.radar.qc_geometry import nearest_azimuth_matches
from rainpulse_algo.radar.qc_input import open_qc_input


def _ppi(values, valid, azimuth, ranges, size=640):
    coord = np.linspace(-1.0, 1.0, size)
    x, y = np.meshgrid(coord, -coord)
    radius = np.hypot(x, y) * ranges[-1]
    angle = np.degrees(np.arctan2(x, y)) % 360
    index, delta, supported = nearest_azimuth_matches(angle.ravel(), azimuth)
    angles = np.sort(azimuth % 360)
    gaps = np.diff(np.r_[angles, angles[0] + 360])
    positive = gaps[(gaps > 0) & (gaps < 180)]
    spacing = float(np.median(positive)) if positive.size else 0
    distance = radius.ravel()
    next_index = np.clip(np.searchsorted(ranges, distance), 0, len(ranges) - 1)
    previous = np.maximum(0, next_index - 1)
    use_previous = np.abs(distance - ranges[previous]) <= np.abs(distance - ranges[next_index])
    gate = np.where(use_previous, previous, next_index)
    dr = float(np.median(np.diff(ranges)))
    good = supported & (delta <= spacing * 0.55) & (distance <= ranges[-1])
    good &= distance >= max(0.0, ranges[0] - dr / 2)
    good &= valid[index, gate]
    rgba = _scalar_rgba(values[index, gate], good, REFLECTIVITY_STOPS)
    return rgba.reshape((size, size, 4))


def write_comparison_images(bundles, report, output: Path):
    """All fields come from validated full bundles, not the selected radial preview."""
    output = Path(output)
    output.mkdir()
    roots = {k: open_qc_input(v).root for k, v in bundles.items()}
    panels = []
    for sweep in report["cases"][0]["sweeps"]:
        name = sweep["sweep"]
        a, b = roots["v3"][name], roots["paper_fusion_v4"][name]
        raw = b["DBZH_RAW"][:]
        observed = b["VALID_MASK"][:] == 1
        azimuth, ranges = b["azimuth"][:], b["range"][:]
        field_list = [
            ("raw", raw, observed, "Original observed reflectivity"),
            ("v3", a["DBZH_USABLE"][:], a["QPE_ELIGIBLE_MASK"][:] == 1, "V3 QC-eligible"),
            (
                "afl",
                raw,
                observed & (b["AFL_CANDIDATE_MASK"][:] == 0),
                "AFL candidate preview / NOT QPE",
            ),
            (
                "afl_local",
                raw,
                observed & (b["AFL_LOCAL_CANDIDATE_MASK"][:] == 0),
                "AFL-local preview / NOT QPE",
            ),
        ]
        if sweep["methods"]["rdd_reference"]["status"].startswith("not_executed"):
            field_list.append(("rdd", None, None, "NOT EXECUTED"))
        else:
            field_list.append(
                (
                    "rdd",
                    raw,
                    observed & (b["RDD_CANDIDATE_MASK"][:] == 0),
                    "External RDD preview / NOT QPE",
                )
            )
        field_list.append(
            (
                "fusion",
                b["DBZH_USABLE"][:],
                b["QPE_ELIGIBLE_MASK"][:] == 1,
                "Paper fusion V4 QC-eligible",
            )
        )
        for method, values, valid, label in field_list:
            if values is None:
                panels.append(
                    f"<section><h3>{html.escape(name)} · RDD</h3>"
                    "<p>NOT EXECUTED — no verified reference. "
                    "Not a zero-detection result.</p></section>"
                )
                continue
            # Values below the palette threshold are not rendered, never recoded as missing.
            display = valid & np.isfinite(values) & (values >= REFLECTIVITY_STOPS[0][0])
            image = _ppi(values, display, azimuth, ranges)
            file = f"{name}-{method}.png"
            (output / file).write_bytes(encode_rgba_png(image))
            panels.append(
                f"<section><h3>{html.escape(name + ' · ' + label)}</h3>"
                f'<img alt="{html.escape(label)}" src="{file}"></section>'
            )
    legend = " ".join(f"{value:g} dBZ" for value, _ in REFLECTIVITY_STOPS)
    page = """<!doctype html><meta charset="utf-8"><title>QC comparison</title>
<style>
body{font:14px system-ui;margin:24px;background:#171d22;color:#eee}
main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px}
section{background:#101417;padding:12px}img{width:100%;background:#000}
h3{font-size:14px}p{line-height:1.6}
</style>"""
    page += (
        "<h1>Frozen input comparison — engineering candidate</h1>"
        f"<p>Same native geometry and palette: {legend}. "
        "Nearest native bins; angular gaps are not filled. Transparency includes "
        "unrendered values below −10 dBZ and unavailable/withheld values: "
        "consult numeric masks, not PNGs, for scoring. "
        "No meteorological truth is inferred from these previews.</p><main>"
        + "".join(panels)
        + "</main>"
    )
    (output / "index.html").write_text(page)
