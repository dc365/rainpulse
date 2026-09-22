# Residual texture and isolation audit (2026-09-22)

`residual-texture-isolation-20260922-v1` is an audit-only native polar object
rule. It separates weak residual objects into textured blobs and small isolated
speckle, preserves weather/strong-echo barriers, and does not change QC, CR,
QPE, or image output.

The first implementation uses bounded physical texture windows, polar evidence,
object size/area, boundary density, and a 3 km neighborhood echo context. The
decision is object-level; no image-only deletion is used.

## BJT 2026-08-28 09:00 Z9598 replay

Input was the current QC volume and the latest near-object CR diagnostic job
`0fd8bdec-3668-40c9-9073-b999b6336936`.

- Elapsed: 3.299 s total; 0.486 s load, 2.651 s evaluation.
- Peak RSS: 596.4 MiB for the standalone current-volume replay.
- Candidate gates: 28,698 across 9 evaluated sweeps.
- Accepted audit objects: 4,260 textured-blob gates and 2,949 isolated-speckle
  gates.
- Latest weak CR winner pixels in 0–75 km: 1,265; audit object coverage was
  69 pixels (8 blob, 61 isolated), including 45/1,019 at 10–50 km.
- A deliberately broad isolation threshold reached 163/1,265, while an almost
  context-free small-object rule reached 532/1,265. The latter over-selects and
  is not suitable as an action rule.

A second audit pass added matched vertical-context discontinuity and object-level
speckle fraction. It ran in 4.160 s with 729.2 MiB peak RSS and covered 78/1,265
weak CR winners (8 blob, 70 isolated), including 53/1,019 at 10–50 km. Vertical
support is diagnostic evidence only; it is not treated as proof of weather.

Conclusion: the first version is fast enough for an offline/audit path and
provides the requested texture/isolation evidence, but conservative coverage is
still insufficient for production removal. Keep it audit-only and use the
broad result only as a calibration bound; do not claim the visible residual is
solved yet.
