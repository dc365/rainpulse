# Py-ART object morphology audit (no disposition)

Identify objects on RAW DBZH at fixed 20/35/45/55 dBZ thresholds with Py-ART
find_objects, without smoothing. Split at every internal geometry gap, exclude
invalid geometry and missing gates. Only a continuous full PPI may wrap at zero.
Objects across thresholds are correlated, not independent votes.

Output per-threshold integer labels and object records: measured gate count,
range span, circular angular width, approximate transverse width at median range,
radial/transverse aspect, and optional held-out source-power residual support.
An unavailable residual is missing evidence, never a zero residual. An object ID
or elongation alone MUST NOT change eligibility, QI, or usable reflectivity.
This audit precedes association and gate-level adjudication; it does not fill gaps.
