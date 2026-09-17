# RC1 / qc-opensource-7.4.0 — experimental radial and clutter branch

Baseline: 3b8035104061cfe2db9da654006cffe0007665c8. No production profile is selected automatically. The extension is absent from old parameter hashes. New profile mode is `audit` by default; `experiment_quarantine` is an explicit independent identity. `operational_eligible` remains false.

## Observation and action semantics

All algorithms operate on original polar measurements, before grid/mosaic. Float NaN is missing; an explicit valid-no-echo mask may contribute to the clutter-history denominator but never creates a DBZH measurement. A finite low echo is not a missing gap. Bad ray geometry abstains; it does not rewrite a finite original measurement to missing.

Morphology proposes only. Raw texture, source signature, prior, Doppler and environmental support are separate evidence families. Low RHOHV, zero velocity, narrow shape, ocean location, persistence and small size are not sufficient single-condition decisions. Multiple transforms of DBZH count as one family. Missing external support is unknown, not no-weather evidence.

`RC1_CLASS` uint8: 0 unknown, 1 local-weather candidate, 2 ground-clutter candidate, 3 anomalous-propagation candidate, 4 sea-clutter candidate, 5 biological candidate, 6 radial candidate, 7 conflicting candidates. These are uncalibrated diagnostic classes, not verified physical truth. `RC1_REASON` uint32 gives available evidence and abstention/competition. `RC1_PROPOSED_MASK`, `RC1_ADDED_QUARANTINE_MASK`, `RC1_RADIAL_CANDIDATE_MASK`, `RC1_PRIOR_AVAILABLE_MASK`, `RC1_WEATHER_PROTECTED_MASK`, `RC1_MODEL_CONFLICT_MASK` are separate. No new confirmed-RFI or confirmed-nonmeteorological flag is emitted by RC1.

Audit preserves baseline values, action, QI and eligibility byte-for-byte. Experimental policy never restores existing rejects/quarantine, changes raw values or makes missing gates eligible. Its additions are DOWNWEIGHT + RFI_QUARANTINE_MASK + LOW_QUALITY, with all quantitative/trust eligibility removed. Historical field RFI_QUARANTINE_MASK is used as the existing generic risk withholding interface; RC1_CLASS/REASON records the non-RFI subtype. No claim that every withheld gate is RFI.

`RC1_BASE_<field>` stores the exact finalized pre-extension arrays. Validation first replays the old validator against a read-only overlay of these baseline fields, then validates the final output as baseline plus recorded additions. Do not pass modified actions directly into old stage-ledger checks. Diagnostic DBZH_QC remains the original measured field; business masks carry withholding, not fabricated zero rain. Phase-derived values remain raw diagnostics but are unusable where their trust/QPE masks are zero.

Budgets are experiment stop/review boundaries, not automatic truth rules. A whole-cut budget stop leaves the frozen baseline unchanged and explicitly marks manual review; it must not be counted as successful enhanced publication. Detailed fields/objects stay in the QC asset. Completion events contain bounded scalars and a content-verified summary pointer only.

## Versioned clutter prior

A prior is built from unique scan identities and unique input-content hashes explicitly reviewed as clear air. It is partitioned by radar, cut geometry, scan strategy and hardware/calibration version. Inputs include UTC timestamps and a review receipt; the latest history timestamp must precede the target measurement. Exact geometry is supported; small azimuth jitter can be conservatively aligned with a one-to-one nearest-ray mapping and an explicit tolerance; arbitrary interpolation is not supported.

Observed denominator = finite available DBZH OR explicit verified valid-no-echo. Missing is never counted as a zero hit. Preserve per-gate observation, echo-hit and independent-day counts, smoothed frequency, Wilson lower/upper bounds, and uncertainty. Low support yields NaN, not zero prior. The stock defaults (20 days / 200 observations) are engineering evidence gates inherited from the project, not universal radar standards. A prior is not a permanent deletion map and must lose to strong current weather counterevidence.

The prior NPZ uses only numeric arrays and includes versioned numeric metadata plus UTF-8 JSON as a uint8 array; no pickle. Content hash includes geometry, masks, counts and metadata. It is not compatible with a silently inferred old map. The loader validates all gates/count consistency and future-data exclusion before use. Missing prior disables only the prior-specific branch.

## Asset-chain audit

Schema `rainpulse.qc-lineage-rc1.v1`: nodes are immutable artifacts {id, kind, sha256, file, parents, product_semantics, grid_id, observation_time_utc, qc_versions}; edges bind raw->QC->Hybrid->Grid->Mosaic->QPE->diagnostic inputs. Cycles, missing parents, future timestamps where past-only applies, unknown product semantics and hash mismatch fail the audit. A selected grid-cell source record must identify the actual QC asset and raw ray/gate, not infer it from a station name. Source contributions normalize only over eligible observed donors. This local checker cannot prove the server export is truthful; the original job/manifest exports must be supplied.

Do not compare composite maximum reflectivity with low-level Hybrid reflectivity as if they were the same product. Rendering metadata and lower display thresholds are audit dimensions, not algorithm skill. No renderer changes in RC1.

## Acceptance

Required negatives: weak weather, stationary/zero-radial-speed rain, strong small cells, coastal rain, melting-layer/low-rho weather, sparse far range, incomplete sector scans, phase wrap, missing polarimetry, clutter-weather overlap. Separate confirmed precision/recall (null without labels), experimental isolation, true-weather loss, coverage loss and per-site worst case. Repeated scan_id is one physical sample. Demo/training cases cannot be an independent holdout. Measure core/context/serialization/upload separately; no synthetic time extrapolation to 105.
