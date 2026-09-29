# X radial residual repair: acceptance contract

## Evidence encoding

When native QC evidence exceeds its configured JSON byte budget, homogeneous
record lists may use lossless `xqc-record-table-v1` encoding: `columns` is the
ordered key list and `rows` contains values in that order. Nested values retain
JSON types. No record, precision, cause or model is dropped. Module summary
keys remain readable. Legacy lists remain supported. If the lossless encoding
still exceeds the budget, explicit abstention remains mandatory.

## Source acceptance

Frozen failure cases: ZF701 2026-08-28 UTC 00:07:21 cuts 0/1, UTC 00:49:25 cut 0;
ZF702 UTC 00:08:25 cut 0. Raw arrays stay immutable. Evaluate native radial
continuity and fan residuals, all elevations and other times, not just job success
or total removed gates. Protected weather, missing SNR, unknown angular coverage,
range-varying rain and independent rain cores are regression controls. No fixed
station, azimuth, time or elevation exclusion is permitted. Candidate results
remain ineligible for trusted fusion/QPE/forecast.

## Implemented corrections

- Preserve the original evidence byte cap with reversible record tables; retain
  explicit abstention when the compact form still does not fit.
- Fit primary (median) and upper (90th percentile) response modes separately.
  Both exclude the target and guard blocks; receiver stationarity, range leverage,
  measured shoulders, response slope and protected-weather rules are unchanged.
- A below-floor block median is not an empty shoulder when independently fitted
  source gates occupy that same block. Unknown coverage remains a hard barrier.
- Admit an independently fitted corridor when at least the existing minimum
  fraction of measured above-floor gates is explained across the minimum range
  span and reference block count. Missing neighbour REF is not a veto.
- Associate short interior gaps between original source anchors only. Both SNR
  and range-normalized REF must stay below the neighbouring source envelope plus
  the existing spread limit. No iterative growth, end extrapolation, missing-gate
  creation or protected-weather override is allowed.

## Verification before deployment

Regression failures were observed before the corresponding fixes: secondary
response mode 9% detection; sparse fan corridor 0%; receiver dropout 0%; whole-cut
record overflow removed all proposed actions. Paired weather-core tests remain
negative. The complete XQC/multiband suite passes 160 tests at this stage.
Real controls: ZF101 828 strong distant gates untouched, no new source detections;
ZF701 UTC03:26 all nine cuts have zero source detections. These are controls,
not independent precipitation truth. Raw data and screenshots stay in ignored
`.build`; synthetic regressions are committed.

Real samples now EVALUATED including ZF702 previously rejected by the 4 MiB
record cap. Remaining native >=15 dBZ gates beyond15 km (not an annotated error
metric): ZF701 next cuts0/1 691/613 ->312/201; 00:49 cut0 1390->279;
ZF702 32799->1187. Visual and deployment acceptance are separately required.

Review found and corrected an association boundary bug before release: a
protected gate, unavailable receiver measurement, or measured below-floor quiet
gate anywhere between the two anchors now blocks completion. Missing REF with
continuous measured SNR may be crossed, but the missing REF gate is never acted
on. Missing-SNR/protected regressions were observed red before this correction.
