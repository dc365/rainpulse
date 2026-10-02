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
