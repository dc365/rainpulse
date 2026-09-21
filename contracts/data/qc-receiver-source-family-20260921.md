# Shared coherent source family v1

Base: dbe3ca8d5df00fea7709b859b4cb3b99b8c1f95a. Nested opt-in
`volume_review.receiver_domain.source_family`. Absence preserves historical
configuration serialization and RDR results. No deployment defaults change.

## Scientific scope
Only coherent receiver-like signatures. A source family is a fold-scoped
measurement hypothesis, NOT identification of an emitter and NOT independent
weather truth. At least two original neighboring rays must separately satisfy
the frozen long-reference model. Donor acquisition times and elevations must
be comparable. The target ray cannot be a donor or a donor shoulder. Target
block and guards are excluded across ALL rays. No QC labels train models.

Own target-ray amplitude/bias are trained on supported physical 2 km blocks
in original range order, with bounded stationary runs and bounded coalescing
of repeated states. Support gaps and guard blocks are not interpolated; each
run and its real indices are recorded. Block-held-out amplitude and bias
predictions are checked. The target cannot select or fit its own reference.
Every accepted target must match exactly one pre-built state and at least two
original donor observations at the SAME range gate, and have two distinct,
actually measured OUTER SNR shoulders. A same-range donor vetoed by independent or unattributed weather protection
cannot support target admission. Neither missing shoulders nor missing
donors count as negative weather evidence.

## Fallback and action
Only previously model-unavailable RDR targets are eligible for the new route.
An existing long/segment model's explicit mismatch is never retried through a
shared family. Family audit leaves prior numerical actions unchanged. In
experiment mode `full_policy=cr_only/quarantine` controls ONLY new family matches,
subject to the parent's action ceiling. Existing parent quarantine is retained
even when the family is CR-only. Partial matches are diagnostic by default, optionally CR-only; they
never independently change QPE. Independent/unattributed weather protections,
polarimetric conflict and suspect numeric tails remain vetoes.

RDR_FAMILY_REASON provides bitwise gate-local accounting for attempted folds,
missing family/state reference, current donor/shoulder failure, power/polar
conflict, ambiguous state, full/partial match and weather protection. A known
source-compatible but unresolved gate is NOT automatically deleted. Such
coverage appears in a separate numerical CR risk layer. No value restoration,
no missing-as-zero, no recursive propagation, no raw modifications.

## Provenance and validation
`RDR_FAMILY_*` are observed-gate diagnostics. `RDR_MODEL_ID` links each usable
family target to a nested record containing original donor references,
ordered own-state runs, original observation digests and cross predictions.
Records and donor/shoulder INDEX VALUES (not just rows of arrays) are converted
back to acquisition order by the adapter. New schema carries explicit order.
Old parent validation runs on exact RDR_BEFORE views before any new delta
validation. Family validation checks no old model takeover, support, unique
state, residual bounds, vetoes and action equality. A family resource failure on any sweep discards new family results for the
entire volume and re-evaluates the frozen parent RDR without the family. Old
parent decisions remain, with explicit RESOURCE_ABSTAINED_PARENT_RETAINED status. All profiles remain
`operational_eligible=false`.

## Acceptance boundaries
Synthetic passing cases and raw input replay are not net production-QC skill.
Compare exact same parent contexts, complete serialized QC and four-station CR
winner receipts. Pollution recall, weather loss and uncertain coverage must be
reported separately; do not label the full screenshot rectangle as truth.
