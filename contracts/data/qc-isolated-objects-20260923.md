# Physical, support-aware isolated-object review v1

Opt-in owner: `volume_review.clutter_fusion.isolated_objects`. No new worker,
queue, input asset, display threshold or global default. This extends the CF
single disposition owner. Parent source-family/near/DEM/background behavior is
not reinterpreted. The paper's weak-echo kernels are diagnostic only.

## Input and geometry

Use immutable original DBZH and its availability on each sweep. Label original
above-structural-threshold echoes, not a post-QC image or eligibility mask. Strong
and already-rejected echoes stay in the segmentation/support field. Native
connected components respect angular gaps and the true 360-degree seam. Area is
the sum of original polar footprint sectors projected onto the local horizontal
plane under the existing 4/3-Earth model. Gate count is never pixel count.

Smallness requires bounded physical area AND containing diameter. Ring quadrature
uses metres and explicit nearest original footprints; it is not a new observation
or independent sample. Count unique native supports as well as physical-area
coverage. Require both physical annuli and every quadrant to have sufficient known
support. Unknown locations contribute to the WORST-CASE echo fraction; they never
vote for quiet surroundings. Out-of-domain, declared gaps, insufficient angular
resolution and large native footprints fail closed for this optional review.

Known support means a finite, available raw reflectivity within physical bounds,
or Sweep.no_echo explicitly supplied by an upstream verified observation contract.
The production adapter currently does not infer no_echo from vendor codes. A
finite value below the structural threshold is a measured subthreshold value,
NOT an assertion of zero precipitation. NaN is never a no-echo observation.

## Qualification

Isolation alone cannot change CR or QPE. Every affected target must have its own
reliable current nonmeteorological polar feature (or the existing strict partial
polar feature). At least the configured fraction of ORIGINAL object gates must
have such evidence. Low SNR alone, a weak-kernel response, a large texture, local
PBB or absence of weather support do not meet this requirement.

Protect any object containing strong echo, hard/local/unattributed weather,
weather proxy, mixed evidence or background enhancement, and objects having such
weather in the surrounding sampled support. Do not propagate action to missing-
moment members of an accepted object. No filling, erosion, dilation or repeated
post-deletion iteration is performed by this extension.

CR-only can use physical isolation + current polar support. Quantitative
quarantine additionally requires the target's existing stable background/current
nonmet comparison, strictly causal measured temporal match, verified Doppler or
verified vertical evidence. Several polar transforms remain ONE family; several
structural measures remain ONE family. The new path never loosens a legacy
protection and never claims a confirmed physical species or interference source.

## Diagnostics and disposition

`CF_ISO_*` native arrays describe identity, area, diameter, support, reasons,
current/context evidence, decisions and net-new attribution. `CF_ISO_WEAK_*`
contains two fixed-metre diagonal sums. Unsupported scores remain NaN; optional
paper-style zero-background scores exist solely for comparison and cannot enter
a decision. Scores are dBZ sums, not power or rainfall. Original values and
observation states never change. No new phase/attenuation correction is made.

All modes retain exact CF-before state. `audit` adds only diagnostic fields and
leaves parent actions and old evidence untouched. CR-only does not change QPE,
trust/QI/flags. Quarantine uses the CF existing trust, QPE and derived-invalidation
rules. Existing CF CR-withheld masks remain the CR generator's admission guard.
Metadata and the winner-audit utility resolve the original object from existing
WINNER_SOURCE/RAY/GATE arrays; no PNG is used as a source of decisions.

## Validation boundary

The output replay validator re-evaluates qualification from saved evidence and
checks raw identity, support inequalities, target evidence, protected-object and
net-new masks. Geometric evidence is bound to the raw sweep digest and recorded
object receipts; an independent replay must recompute geometry for truth of the
measurements. Audit counterfactuals are mandatory. Current examples are developer
cases, not independent meteorological validation.
