# Residual repair 6.1 contract (engineering candidate)

Pipeline `qc-opensource-6.1.0`, decision `residual-v6.1`, flags v2.
Frozen V1–V6 profile files are not rewritten. A new `residual_repair` block is
required only for 6.1. Existing residual thresholds are not relaxed.

- All open-source profiles request the same geometry-resource loader. V6's
  missing whitelist entry is a correctness fix. Reproducing the **buggy** online
  V6 resource path requires the original source commit, not a relabelled 6.1 job.
- Local-width repair adds narrow candidates; it does not remove previously
  accepted V6 candidates or automatically reject shape-only hypotheses.
- Footprint-aware peripheral review is bounded by native topology, angular
  radius, footprint-edge gap and observed/protected barriers. Every witness is
  an original anchor; new exclusions cannot seed another pass.
- `V61_NARROW_STAGE_REASON` uint32 is a bit field for the **local repair route**:
  1 weather/observed-noecho barrier, 2 no candidate evidence, 4 insufficient span,
  8 insufficient measured length, 16 locally broad support, 32 aspect failure,
  64 evidence fraction failure, 128 candidate, 256 shape-model ineligible.
  It does not claim a true meteorological label.
- `V61_NARROW_LOCAL_WIDTH_DEG` float32 is NaN off evaluated support.
- `V61_PERIPHERAL_CENTER_DISTANCE_M` and `V61_PERIPHERAL_FOOTPRINT_GAP_M`
  describe the selected compatible witness, not a calibrated confidence.
- `V61_POL_RELIABLE_MASK`, `V61_SNR_AVAILABLE_MASK`,
  `V61_PHASE_BAD_MASK`, `V61_ZDR_BAD_MASK`, `V61_LOW_RHO_MASK` expose
  the existing measurement gate. A missing field is unavailable, never zero.
- `V61_REVIEW_OUTCOME` uint8 is engineering disposition: 0 not proposed,
  1 blocked by weather, 2 candidate retained (insufficient evidence),
  3 candidate quarantined, 4 candidate rejected, 5 original observation missing.
- Confirmed, quarantined, valid no-echo and missing remain distinct. Original
  values/flags/field validity are not filled or interpolated.

6.1 context provenance includes resource statuses, content hashes for used radar
configs and DEM manifest, and a semantic digest of prepared context arrays.
Missing terrain/unknown altitude datum is reported; it is not clear-air evidence.
Compare `shared_baseline` (controlled algorithm experiment) separately from
`each_profile` (actual current Worker preparation path). Never call either an
exact reconstruction of a historical job without its original context assets.

The forensic exporter is read-only and verifies the original RGBA PNG, layer
identity and sampling version before tracing. Schema, manifest and output limits
prevent screenshots/maps from being silently used as native pixel coordinates.
