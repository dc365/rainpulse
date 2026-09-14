# QC residual V6 (engineering candidate)

V6 extends `rainpulse.qc-radar-volume` 1.0 with optional numeric fields. Existing
V1--V5 configuration files, semantic hashes and baseline decisions remain frozen.
New profiles require `qc-opensource-6.0.0`, `residual-v6`, `residual`, `cross_radar`
and `literature` together. Operational eligibility stays false.

## Invariants

Raw gate values are immutable. Missing is not zero, no-rain, or an RFI seed.
Association, fit support, candidate membership and final measurement action are
separate states. V6 does not restore a V5 rejection or quarantine. Phase trust
and QPE eligibility must follow any new action before phase processing.
Residual growth is a bounded, single evaluation from frozen raw evidence and
original anchors; generated exclusions never become new anchors. Mere loss of
QPE eligibility, a small object, high reflectivity, or high/low RHOHV alone is
not a confirmed non-meteorological cause.

## Optional fields (all native ray x gate geometry)

* uint8 masks: `V6_*_MASK`; candidate, inspected, supported, confirmed addition,
  quarantined addition, linked review, original weather protection are separate.
* uint32 IDs: `V6_NARROW_OBJECT_ID`, `V6_RANGE_OBJECT_ID`,
  `V6_SPECKLE_OBJECT_ID`. Zero means absent; IDs are per sweep, not global.
* uint8 `V6_NARROW_TYPE`: 0 none, 1 continuous, 2 interrupted, 3 short, 4 remote.
* uint32 `V6_DECISION_REASON`: additive reason bits defined in residual.py.
* int32 `V6_PARENT_RAY`, `V6_PARENT_GATE`: original indices of the frozen source
  anchor for peripheral review, -1 means no anchor. They are not invented data.
* float32 physical lengths/areas/residuals: NaN when unavailable. Units are
  encoded by `_M`, `_KM2`, `_DB` suffixes.
* Baseline masks `V6_BASELINE_REJECT_MASK`, `V6_BASELINE_QUARANTINE_MASK`,
  `V6_BASELINE_ELIGIBLE_MASK` refer to the embedded V5 on the SAME input/context.

A `residual_v6` sweep summary records parameters, all candidate counts, final
incremental actions, reason counts, object statistics and module status. Counts
are diagnostics, not skill. Evaluation uses fixed raw observation and label
domains, and reports confirmed recall separately from withholding/coverage loss.

PPI sampling uses source-cell footprints, not extrapolation into missing angles
or before/after the measured range. Pixel trace and PNG use one sampler. The
renderer fix is versioned `native-footprint-v2`; it changes display geometry,
not raw or QC values. Duplicate azimuth centres are ambiguous and transparent.
