# P0–P2 measurement/admission and broad-source contract

Opt-in pipeline `qc-opensource-7.2.0`, decision `generalization-p0p2-v1`.
Existing profiles and their scientific parameter hashes are frozen.

P0: CONFIG_NOT_READY is administrative only when it is the sole degradation
reason and channel status is explicitly OK. Unknown/degraded/missing telemetry,
other reasons and UNAVAILABLE retain physical penalties. Raw health and lifecycle
are never changed. Admission remains blocked; this is not calibrated readiness.
`P2_ADMIN_PENALTY_REMOVED_MASK` records gates crossing the quantitative threshold
only because that administrative multiplier was removed. Earlier rejection,
quarantine and field validity are not restored.

P1: A new measurement-supported domain includes original fitted inlier gates
regardless of object's shape. `P2_RANGE_ROUTE_CODE` 1=narrow, 2=wide, 3=compact,
4=wide+compact. Shape selects review; it is not pollution evidence. Missing and
linked undecided outliers never enter fitted support. Legacy V5 candidates remain
unchanged. New quarantine requires original range fit AND held-out OC1 coherent
source/target compatibility AND no positive comparable weather support or
local power-enhancement conflict. Local smoothness from the same coherent source
is recorded as correlated evidence, not a decisive independent weather veto.
Unverified numeric plateaus are not confirmed; no new confirmed-RFI flags.

P2: OC1 and additional broad-source quarantine are accounted for separately.
Crossing the configured quantitative-loss budget raises a review-required state;
it does NOT restore high-risk values into QPE. No entire scan is blindly deleted.
Only measured qualifying candidates are isolated. Full details live in checksum-
bound assets; completion messages remain bounded. Budget exceedance and admission
issues forbid automatic promotion, not raw-data viewing.

Strict invariants: raw unchanged; valid no-echo/missing/low-quality distinct;
no fixed station/azimuth rules; no interpolation or recursive new seeds;
no removal from high reflectivity, width or high/low rho alone; new derived
identities never masquerade as the old V7/OC1 products. No weather truth or skill
claim follows from engineering tests or counts.
