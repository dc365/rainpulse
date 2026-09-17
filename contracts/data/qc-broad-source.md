# Experimental broad-sector source model

Native polar, immutable raw DBZH/SNR/RHOHV/ZDR/PHIDP only. Reference fits exclude
both target 50-km block and its immediate guard blocks on every supporting ray.
At least five measured reference blocks spanning 200 km predict the target;
angular agreement uses only these held-out-reference fits and cannot cross gaps.
The model describes a joint source hypothesis, not independent votes, an RFI
probability, a saturation assertion, or a confirmed nonmeteorological flag.

BWS_CANDIDATE_MASK uint8, BWS_REASON uint16, BWS_FOLD_ID uint32 and
BWS_RANGE_RESIDUAL_DB float32 (NaN unavailable) share the native sweep geometry.
Reasons: reference fit=1, angular agreement=2, target match=4, weather protection=8,
target conflict=16, unverified numeric plateau=32. Missing observations never
become candidates. Targets must independently fit the range/polarization model;
weather support, target enhancement/conflict, and numeric plateaus veto action.

Default mode is audit (no changes to actions/QPE). Only an explicitly versioned
experimental quarantine profile may isolate qualified candidates. It must keep
raw fields, change no confirmed-RFI bits, clear quantitative trust and surface
review/budget status. No automated operational promotion or whole-sector deletion.

## 7.3.1 paired range term

Opt-in shared_range_term estimates DBZH-SNR-20log10(r/km) using only reference
blocks, excluding target and guards. Two disjoint ray groups must agree; failures
use the original zero-term model and retain explicit failure diagnostics. The term
is processing calibration, never independent RFI evidence. effective range is
limited by measured native gates (observed_range); no synthetic extension.
Every block records coefficient, group fits and status. Legacy defaults and
semantic profile hashes remain unchanged. No operational auto-promotion.
