# S/X finite close-out contracts — 2026-10-08

## Scope
These additive diagnostics and execution optimizations do not change QC,
attenuation, admission, comparison methods, operational or QPE eligibility.
`experimental_horizontal_max`, `quality_height`, `quality_height_v2` remain
separate scientific policies. This contract does not certify rainfall accuracy.

## Sources (rainpulse.sx-source-v2)

Integration retains the published `sx-configured-series-v2` SQL digest, including
the full Worker identity, frozen upstream S QC policy and legacy run isolation.
The `series_mode=1` envelope does not assign new IDs to old results. Only the
selected series has a known frame count; other counts remain null. Exact time
lookups never round a minute to a six-minute frame. `WINNER_BAND` is a category:
0 means no finite echo winner, 1 S and 2 X; it is not a rainfall amount.
Each comparison product has its own **explicit indexed** source table. Entries
are not filtered or renumbered behind a winner array. `source_granularity` is
`native_volume` or `native_cut`; a volume winner also requires a native sweep
number. Native ray/gate integers always refer to the original input.
A separate sorted station table maps station IDs, not cut indices. No-echo is
not missing: a valid no-echo cell may have a coverage source but no echo winner.
The existing `source_count` is deprecated as a station count. New counts separate
requested/input/native-qualified/spatially-qualified/echo-winning stations and
known cuts. Unknown legacy counts are null, never guessed to zero.
A legacy artifact without this contract is reported as legacy/unresolved.

S-only/X-only provenance arrays use `_S_ONLY`/`_X_ONLY` suffixes in the shared
NPZ and canonical field names in their own probe. A consumer must use the table
for that product. New `WINNER_STATION_INDEX`/display `WINNER_SITE` are station
indices. `WINNER_SOURCE` is unchanged. `WINNER_BAND` is uint8 0/1/2; no echo
winner is 0, not an invented S or X observation.

## Read-only audit (rainpulse.sx-audit-v1)
Native gates and projected source/cell/height opportunities have DIFFERENT
units. Native primary reason categories are exclusive, ordered, and sum to
native gate count. Multi-reason bit counts are separate and never summed as a
removal count. Projected categories sum to all examined opportunities; geometry
and observation age share an explicit category if the existing footprint does
not expose them separately. Missing source/arrival reasons are source events,
not gate counts. No diagnostic changes admission or restores RAW.

Same-height pairs use at most 4096 deterministic, echo-independent positions
from the configured level/grid lattice. Both band winners are sampled at the
same configured height. Additional diagnostic screens are age difference <=60s,
beam-center-height difference <=500m, effective-resolution ratio <=2. These are
DIAGNOSTIC comparison limits, not QC or calibration constants. Report sampled
population, unavailable/comparison-limited states and echo/no-echo conflicts.
They do not prove exact equal scattering volume or meteorological truth. No
online bias correction follows. Horizontal products explicitly do not provide
same-height pairs.

## Equivalent execution
Horizontal mode keeps the existing 1 degree angular tolerance, actual ray age,
range-width policy and strict greater-than tie rule. Caches are task-local and
byte bounded. Geographic display sampling keeps the original transform, grid
row order, nearest-cell policy, palette and PNG codec. New diagnostic images
are permitted; existing reflectivity fields and image pixels must remain equal.
Raw arrays and source admission are never mutated. Complete publication remains
atomic; diagnostics and provenance must validate before publication.
