# Unified native radial objects, candidate v1

Pipeline: immutable native RAW -> measured object windows -> joint object
classification -> gate disposition -> frozen-data comparison. Existing variable
width tracking supplies original runs; its legacy action mask is not an input.
Existing rejected-source IDs are not prerequisites for this detector.

An object contains its full original history, native member coordinates, actual
window boundaries, independent physical window count, measured coverage, edge
contrast, centre/width stability, and weather counterexamples. Membership is
frozen before classification. Accepted targets never nominate other targets.
Duplicate nominations may union exact measured coordinates; they must not
create interpolated coordinates or enlarge a boundary. Genuine geometry gaps,
forks and narrowing constant-physical-width weather remain explicit abstentions.

The joint score is a deterministic evidence score, not a calibrated probability.
Geometry and observed bilateral contrast are required evidence families.
Unknown measurements cannot contribute a quiet-side vote. Ordinary bad windows
reduce object support rather than vetoing the entire object. A window must
independently satisfy measured coverage and bilateral edge requirements before
its members gain disposition eligibility. Weather, barriers and unavailable
targets are always retained; mixed or unknown windows remain review-only.

Output separates candidate membership, strong-object membership, eligible gate
proposal, weather retention and unknown/conflicting segment retention. Audit
mode has zero actions; quarantine mode applies only eligible measured members.
RAW is never edited, missing is never filled, and no-rain is never fabricated.
Every output binds the version, configuration, native geometry/measurements and
exact original membership. Resource exhaustion returns no partial promotion.

Acceptance includes rotation/resolution changes, variable width, discontinuity,
weak targets without previous sources, local contamination, unknown shoulders,
curved/narrowing weather, forks, native gaps, protected weather, and proof replay.
Eight historical examples are regression data, not independent generalization
evidence. Compare on fixed inputs and report weather retention, held-out object
recall, runtime and per-reason abstention. Independent dates/sites are required
before broad readiness; additional gate counts alone do not establish correctness.

Stable subbands may be measured inside a mixed complete parent. Each band starts
from an actual RAW angular run, matches only its frozen original boundary, and
retains every range window's matched/merged/forked/unknown history. Membership is
actual RAW inside the original measured extent. Target plus adjacent windows
train neither boundaries nor edge confirmation. At least five remote measured
windows spanning150km are required. Actual exterior noise/weak reflectivity is
required; an available but nonquiet SNR with missing DBZH proves coverage only.
The exact nominated template must itself occur in at least two remote original
reference windows outside the target and adjacent windows. Approximately
matching boundaries alone cannot validate a width invented by the target.
Local reliable weather stays in measured occupancy and never creates dry edges.
Pure curved/narrowing RAW parents cannot restart as clean subbands; an unresolved
fork remains an abstention. This route has no old source-ID prerequisite.

Optional separated-side confirmation aggregates left and right measurements in
distinct original range windows. Each side needs five known contrasting remote
windows spanning150km, excluding the target and adjacent windows. Known wet
windows remain negative votes; unknown windows never become quiet votes. At
least80% of each side's known windows must be contrasting. Reference support
cannot manufacture target observations: local actual bilateral contrast and
target availability/weather/barriers remain gate requirements. A local original
boundary may breathe within the frozen two-beam association only if that exact
boundary occurs in two remote original geometry windows. Local merged/forked
windows cannot borrow a remote edge to remove their members. Membership is
recorded from original RAW before classification; no accepted target is a source.

Engine integration uses explicit default-off strict Boolean switches for unified
objects, original subbands and separated edges. Separated edges require subbands,
and subbands require unified objects. The separated provider supplements the
original subband provider; each consumes the same immutable original parents.
Unique IDs and shared object/membership budgets apply to their combined ledger.
Resource failure abstains atomically, without partially publishing either route.

Only the unified PROPOSAL_MASK enters engine candidate/geometry qualification,
after original receiver/source IDs are frozen. The outer engine mode owns final
action; the inner object detector remains audit-only. Neither object membership
nor strong-object membership alone authorizes a gate. Actual weather/conflict
and supplied RAW-volume weather protection remain barriers during nomination.

Serialized proof records native ray order, original coordinates/geometry,
DBZH/SNR/RHOHV availability and measured values, barriers, antenna beam, fixed
policy code and object budget. Native serialization version is 1; decision
policy codes are 1 (unified), 3 (original subbands), and 5 (both subband providers).
Writer validation requires immutable DBZH_RAW, rejects partial or unknown proof
fields and replays every decision array from restored original native geometry.
An altered mask, policy, measurement or local native-order record cannot be
trusted merely because its final qualification union is internally consistent.

The runner independently exports NATIVE_QC_ORDER, NATIVE_QC_GOOD_MASK and
NATIVE_QC_GAP_MASK for a sweep with unified proof. The writer binds object proof
to these canonical adapter outputs and to the independently written range,
azimuth, DBZH_RAW, SNR_RAW and RHOHV_RAW. Moment availability is recomputed from
canonical finite values and adapter geometry (RHOHV also requires [0,1]); an
absent moment cannot authorize a proof-carried measurement. Missing canonical
geometry or disagreeing measurements/order are fatal publication errors.
