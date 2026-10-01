# Fragment line evidence v1

## Frozen-parent angular response probe (diagnostic only)

Original sources may bracket a target ray within two actual native beams. Each
reference ray must have a unique guarded original power-state model (target and
adjacent 20km blocks excluded); at least three original reference rays are
required. Outermost original references define the angular linear response;
interior original rays independently check it within2.5dB. No target trains the
curve, no one-sided extrapolation, no original multi-state ambiguity resolution
using targets, no crossing native angular gaps or protected target/source paths.
References belong to the same frozen RAW parent, remain original ledger IDs,
and each endpoint is within120km of the target. A curve fit/residual is only a
diagnostic explanation, never an independent RFI vote or automatic deletion.

## Original-source power states (diagnostic v1)

`fan_power_states_enabled` defaults off and requires fan_joint. State discovery
uses only complete original ledger sources on the same ray and protected-safe
section, excluding the target 20km block and its two neighboring guard blocks.
Power is DBZH minus the 20log(range) term; >=6dB gaps may split up to three
states. Every state independently requires 10km actual support, three 20km
blocks, 60km span, reference P90 <=2.5dB and alternating-block median difference
<=1.5dB. Source and target SNR must actually exist and be >=3dB. Target power
must match within2.5dB and SNR be within the original state's 5–95 percentile
interval, with 2dB tolerance. Targets remain in the frozen RAW family, <=120km
from original sources. Multiple matching sources/states permanently abstain.

`RV2_FAN_STATE_*` stores candidate/match/hold/state/source IDs, source and target
actual SNR, and independently fitted reference parameters. Writer validation
recomputes discovery and references using immutable source DBZH and recorded
SNR. Missing values remain NaN. This match is diagnostic only: it adds no action,
candidate for old source fitting, interference vote, new source, or fill. Weather
barriers, raw geometry and existing receiver fit identities remain unchanged.
HOLD_REASON=1 no bounded same-ray original source, 2 missing/low actual target SNR,
3 insufficient independent original states, 4 fitted original states do not match,
32 competing original sources/states; 0 means diagnostic match or non-candidate.

## Frozen broad RAW families (diagnostic v1)

### Original-source model qualification

`fan_joint_enabled` requires RAW fan families. A RAW family is not an independent
source. For each target, use an ORIGINAL ledger source on the same native ray;
do not pool unrelated source IDs or infer angular gain from accepted tails.
Use complete original source gates, not their intersection with a narrow tile.
Exclude the target 20 km range block and one guard block on either side from
reference fitting. Require >=10 km actual reference support in >=3 blocks over
>=60 km, and consistent odd/even block reference medians (<=1.5 dB).
Fit the range-normalized DBZH intercept from original reference gates only;
reference p90 residual and actual target residual must each be <=2.5 dB.
This is same-source consistency, not a new independent polarimetric vote.

Targets stay in the frozen RAW family and <=120 km from original source gates;
explicit weather/geometry barriers forbid linking even across an empty gap.
New qualifications cannot become sources. Persist original source measurements,
source ID, RAW family ID, target block, physical coordinates, model intercept,
reference support/block count and target residual; serialization recomputes
references including guard exclusions. Ambiguous qualifying source IDs abstain.
Only experimental quarantine may act; audit remains diagnostic. Cross-ray gain,
unanchored classification and independent weather/holdout acceptance remain
separate required work, not implied by this qualification.

`raw_fan_families_enabled` requires the complete source ledger. Before any new
actions, nominate measured RAW DBZH>=0 in 20 km blocks whose radial occupancy
has >=2 samples and >=500 m actual support. Angular bands require >=2 native
rays and width 2–90 degrees. These thresholds nominate, NEVER authorize removal.
Single short fragments inside an original band remain included. Unknown
shoulders are recorded as unavailable, not observed no-echo.

Link blocks only within 60 km and a fixed first-block boundary tolerance of two
native beams. No newly linked edge can enlarge that angular envelope. Protected
gates, geometry gaps and ambiguous competing objects break linkage. Persist
RAW object ID, original ledger seed IDs (never promoted), first and local angular
boundaries, actual range/support/occupancy and missing-side availability.
Every candidate retains NO_INDEPENDENT_QUALIFICATION; action mask is absent.
Original source IDs in one RAW object are a list of clues, not evidence that they
share a transmitter. This diagnostic must not alter old fit inputs or actions.

## Discontinuous native tracks, 2026-10-01

### Frozen raw-source envelopes and strict unanchored qualification (v2)

`source_envelope_enabled` requires residual objects. Before proposing actions,
freeze independent seed masks and raw nominations from the original native
measurements; do not use DBZH_QC or newly linked remnants as seeds. Source
objects join >=1 km raw nominated fragments across <=60 km gaps, within a
geometry/weather/conflict/plateau-safe angular corridor. Require >=10 km original
seed support, >=60 km original nominated extent and aspect >=8. Each tail must
have locally consistent boundaries, flank contrast or unknown isolated shoulders,
>=500 m observed fragment support, lie within the frozen raw extent plus <=10 km
padding, and be <=120 km from an original seed. These two fixed bounds never
advance on accepting a new fragment. Crossing a protected corridor is forbidden.

RV2_ENVELOPE_{MASK,PARENT_ID,SEED_ID,RAW_OBJECT_ID,LEFT_DEG,RIGHT_DEG,
START_M,END_M,ANCHOR_DISTANCE_M} records actual action gates, immutable lineage,
and original range/angle bounds. Missing shoulders are never measured clear air.
ORIGINAL_LEFT_DEG/ORIGINAL_RIGHT_DEG/BEAM_PROXY_DEG retain the original angular
reference separately from a tail's local boundaries. One-hop continuation may
reach an acquired neighboring ray within one beam of the original seed ray;
neither that ray nor its accepted tails become new angular references. Boundary
drift stays within one original beam; barriers across the entire intervening
corridor veto the link. Native spacing remains an explicit proxy when antenna
beam width is unavailable.

Unanchored `RV2_DISCONTINUOUS_CANDIDATE_MASK` keeps geometric nominations,
including missing-flank isolation. `RV2_DISCONTINUOUS_MASK` now additionally
requires bilateral **measured** >=6 dB contrast, corroborated in 20 km and 60 km
windows (>=1 km centre support and >=75% measured bilateral support over actual
centre observations in each window), and boundary spread <=one native beam over
the whole object. Windows are clipped to protected/geometry-safe corridors.
Only locally qualified actual gates are actions. Inadequate or missing flank
observations stay diagnostic; radial alignment alone never becomes an action.

Existing matched upper-elevation/neighbor weather support vetoes these paths.
RV2_INDEPENDENT_WEATHER_AVAILABLE_MASK separately records its availability;
absence is unknown, never evidence of non-weather. Source of this support remains
the existing matched context contract; this change does not invent neighbor
observations or extrapolate across unmatched beam geometry. Audit is inert.

`discontinuous_tracks_enabled` requires `residual_objects_enabled`. Partition
native geometry at angular gaps and range corridors at weather/conflict/plateau
barriers. Associate actual DBZH fragments >=1 km across bounded <=60 km gaps,
using physical range spacing and one/two beam-width flank searches. Require
>=4 fragments, >=8 km measured support, >=80 km span in >=4 20 km blocks,
span/maximum beam-inclusive width >=12 and <=3 degrees between outer flanks.
Each accepted gate has bilateral >=6 dB measured contrast, or two acquired
unobserved rays on each missing side. Missing flanks remain unknown; they are
geometric isolation evidence only. No source or recursively extended anchor is
fabricated. Broad objects, angular seams and protected intersections cannot
bridge fragments. No missing/no-rain gate is filled or quarantined.

RV2_DISCONTINUOUS_{MASK,OBJECT_ID,LEFT_DEG,RIGHT_DEG,SUPPORT_M,SPAN_M} stores
the separate geometry and object evidence. These are research thresholds, not
independent truth labels. Existing receiver source matches retain their SNR and
moment requirements. Qualified direct/isolated/group/residual morphology may
propose quarantine despite receiver weakness, recorded separately in
RV2_GEOMETRY_ACTION_MASK. Weak source-only hypotheses remain diagnostic;
audit mode and downstream weather protection still apply. This repairs the
former global receiver-weak veto on independent morphology. Raw stays immutable.

## v3 isolated missing-flank line policy

Explicit `fragment-line-v3` + `isolated_quarantine_enabled: true` permits
quarantine from isolated raw-observation geometry, separately from measured
edge/source evidence. Missing flanks remain unknown; no missing DBZH is set to
zero. Both outer sides must have two geometrically valid acquired rays without
DBZH observations at that gate. Azimuth gaps, invalid rays, sector boundaries,
weather/conflict/plateau barriers and precipitation intersections cannot count
as empty flanks. Raw echo width <=3 rays /3 degrees, Hough radial alignment,
>=60 km span, >=20 km measured support in three distance blocks, >=0.4 support
fraction and length/maximum beam-inclusive width >=8 are required together.
Only actual isolated line gates >=the lowest configured contour are acted on;
gaps and precipitation intersections remain untouched.
Same-ray Hough segments are unioned before qualification. Isolated fragments
may be associated across at most30 km (`isolated_link_gap_m`), independently
of the Hough detection gap. The interval is not added to the action mask.

Optional `RV2_LINE_ISOLATED_MASK` is the qualified isolated-line action evidence;
`RV2_LINE_EMPTY_FLANK_MASK` stores its raw geometric support. Neither means
measured clear air. Reasons and proposal validation distinguish this route from
physical source matches and measured-edge contrast. Audit mode still no-ops.

## v2 direct morphology quarantine (opt-in)

`version: fragment-line-v2` and `morphology_quarantine_enabled: true` allow
an independent morphology disposition. Accepted raw Hough lines must also have
two **measured** outer flanks at least 6 dB below the target, >=20 km measured
support spanning >=40 km and at least three 20 km distance blocks. Only actual
line gates satisfying that bilateral contrast are isolated. Missing flanks are
unknown, not zero or clear air. Weather/conflict/plateau barriers and audit mode
remain effective. `RV2_LINE_MORPH_MASK` records this path separately from source
matching; it can quarantine weak-SNR lines without pretending a source match.
`RV2_LINE_EDGE_DB` records measured minimum bilateral contrast (NaN if unknown).
The action excludes gates from usable reflectivity/QPE, never edits raw data.

Opt-in `generalization.broad_source.source_review.radial_revision.fragment_line`.
Default is absent (unchanged legacy output). Configuration hash identifies the
new run; raw observations remain immutable.

Multi-level raw DBZH contours nominate narrow azimuth runs on native geometry.
scikit-image probabilistic Hough detects radial runs in the azimuth/range image;
the range axis may be reduced for detection only. Native coordinates then verify
radial straightness with RANSAC. Association may cross a bounded distance gap,
but nomination is restricted to measured contour gates, never the whole span.
Unknown flanks are morphological support only, never measured clear-air evidence.

`RV2_LINE_MASK` (uint8, optional native ray/gate binary array) adds a morphology
candidate, not an action. It must contain only observed, unbarred gates. It is
unioned with existing topology/bundle candidates **before** held-out segmented
source evaluation. Existing legacy or strict segmented physical source evidence
is still required per gate. Weak/incomplete source families remain diagnostic.
Weather/conflict/plateau barriers, audit mode and outer action policy still apply.
No new confirmed-deletion class or missing/clear-air conversion is introduced.

For strong narrow coherent interference a second physical path is available:
held-out 20 km target blocks plus adjacent guard blocks are excluded from raw
reference selection. At least three other blocks spanning 60 km with 20 km
measured support must agree between alternating reference groups. SNR >=20 dB
must be constant within 1 dB, circular PHIDP within 1 degree, ZDR within 0.25 dB,
and measured RHOHV >=0.98. Every target must independently match all these
criteria and the line nomination. `RV2_LINE_SOURCE_MASK` records that path;
float32 `RV2_LINE_REFERENCE_SPAN_M` records reference extent and uint32
`RV2_LINE_FOLD_ID` records excluded target/guard folds. This is experimental
quarantine only, not a claim that constant polar moments prove interference.
Weak SNR, absent moments, protected weather and conflicting gates cannot use it.

Counters report detection, geometric acceptance and candidate gates; action counts
must be compared against the step3 baseline, not against unfiltered raw gates.
Resource exhaustion abstains for this optional branch while retaining completed
baseline evidence. Geometry or input errors are not swallowed.

## Opt-in sparse isolated association

`sparse_isolated_enabled` (default false, requires v3 isolation) adds raw
0 dBZ isolated support independently of Hough continuity. At least three
observed fragments >=500 m each must span >=120 km across >=4 distance
blocks, with >=10 km observed support, <=100 km between fragments and
length/beam-inclusive width >=12. Actual isolated gates alone are nominated
and quarantined through the existing isolated evidence path. Missing intervals,
weather/conflict barriers, invalid rays and broad echoes remain unchanged.
These research bounds are versioned by configuration and are not truth labels.

### Source-anchored outer edge

Sparse isolation may also inspect <=5-ray/5-degree raw objects. Only an edge
gate immediately adjacent to an independently source-matched, unbarred core
>=20 dB stronger may qualify, with no observed gate on its other side and two
empty acquired rays outside the whole object. Require >=10 km measured support
spanning120 km in4 blocks, bounded100 km association and span/width >=8.
No recursive propagation or whole-object deletion. Existing raw isolation
diagnostics include this object's empty outer flanks; weather barriers remain.

## Grouped radial strips (sparse opt-in)

The sparse branch also labels multilevel raw strip components after bounded
3 km range closing (identification only). Components must span >=100 km,
occupy <=12 native rays /12 degrees, have >=20 km observed support on a ray
and cover >=4 distance blocks. Geometry gaps partition components. Only actual
unbarred gates enter RV2_GROUP_MASK; closing never fills the output.
RV2_GROUP_POLAR_MASK additionally requires measured SNR >=10 dB and RHOHV
in [0,1], and either RHOHV <0.7 or (RHOHV <0.85 and abs(ZDR)>3 dB).
Missing moments cannot qualify. This independent morphology-plus-polar route
quarantines, does not assert physical-source matching or confirmed pollution.
Reason bit32768 identifies it; audit remains inert. Research thresholds require
case validation and are not a false-positive guarantee.

## Independent strong group morphology (opt-in)

`group_morphology_enabled` requires sparse isolation. In addition to group
candidate rules, require >=150 km span, >=30 km observed ray support,
beam-inclusive width <=8 degrees, span/maximum physical width >=5, and
component column-centre spread <=2 degrees (10th–90th percentile). Both outer
acquired rays must be geometrically valid without an azimuth gap, unbarred,
and either have missing DBZH or measured DBZH >=6 dB below each action gate.
Missing is unknown, not measured clear air. Only actual gates satisfying this
bilateral morphology qualify. RV2_GROUP_MORPH_MASK identifies this independent
path, separate from RV2_GROUP_POLAR_MASK. Reason bit32768 means grouped-strip
disposition; the separate masks distinguish its basis. No physical-source match
is fabricated, raw observations remain unchanged, weather barriers and audit
mode remain effective. This is experimental quarantine, not a truth label.

### Range-block radial tracks

With group morphology enabled, additionally scan each native azimuth using
independently chosen flanks at1..4 rays on each side (<=8 degrees total). Interior measured gates must
occupy >=60% of the strip and remain within6 dB below the centre; both outer
flanks must be missing or >=6 dB weaker, acquired, unbarred and contiguous in
angular geometry. Track only those actual centre gates across <=60 km gaps.
Require >=30 km measured support, >=150 km span, at least6 occupied20 km
blocks, support fraction>=0.15 and span/maximum physical width>=5. Candidates
and actions use these measured gates only, never crossing precipitation bridges
or filling range gaps. This avoids rejecting a whole strip because a short
cross-ray bridge merged its global connected component into a broad object.

Asymmetric flank distances allow action on off-centre strip edges. The same
interior occupancy, contrast, observed support, geometry and weather barriers
apply; this does not increase the maximum total angular width.

## Window-track-v1 experiment

`window_tracks_enabled` requires group morphology and defaults false. Physical
20/40/80 km windows use actual range spacing; 1/2/4 degree left and right
search distances use native angles, not fixed ray counts. Within each window
require >=35% centre observations, >=60% interior relative support averaged
on observed centre gates, <=20% competing flank fraction on either side and
>=75% window extent. Target gates themselves must pass bilateral6 dB contrast
or missing flanks, geometry continuity, weather/conflict barriers and >=50 km
range. Window length / maximum beam-inclusive physical width must be >=4.
Missing DBZH is never zero-filled. Only observed target gates are quarantined.
This is a new opt-in candidate model, not independent validation or operational
acceptance. Development08:42 data cannot be used as a held-out evaluation set.

### Variable-width continuation under window-track experiment

Store the narrowest locally supported left/right angular boundaries at each
observed target gate. Link consecutive supports only across <=10 km range gaps
and <=1.5 degree changes on each boundary. Qualify tracks with >=60 km span,
>=20 km observed support in >=3 distance blocks, centre-angle spread<=2 degrees
and span/maximum physical width>=5. Each target must independently pass
bilateral contrast, interior occupancy and geometry/weather barriers. Only
actual supports enter the group morphology mask, never connecting intervals.
Enabled only by window_tracks_enabled; not a claim of cross-event validation.

### Completion of physical multiscale contract

Window targets begin at20 km.20 km windows additionally require70% centre
occupancy,85% interior support, <=5% competing shoulders and aspect>=6;
40/80 km windows retain35%/60%/20% and aspect>=4. Thresholds are research
settings, no independent event validation claimed. Angular search includes
configured antenna_beam_width_deg and twice that width; when absent, native
angular spacing is explicitly a proxy, not measured antenna beamwidth.
Physical width is at least the supplied beamwidth.1e-8 degree numerical search
tolerance avoids skipping an exactly adjacent ray after degree conversion.

Optional float32 RV2_WINDOW_{LEFT_DEG,RIGHT_DEG,SCALE_M,
LEFT_MISSING_FRACTION,RIGHT_MISSING_FRACTION,BEAM_PROXY_DEG} retain accepted
window evidence (NaN outside that branch). RV2_TRACK_LEFT_DEG and
RV2_TRACK_RIGHT_DEG retain variable-width continuation boundaries. Missing
fractions describe geometry-valid samples with absent DBZH, not clear air.
Multiple accepted windows store the last deterministic accepted window.
The original raw values remain immutable. These diagnostics accompany group
morphology proposals; audit mode still has zero actions.

## Opt-in power-fan morphology (2026-09-19 v1)

`fragment_line.power_fan_enabled` requires group morphology and defaults to false.
The child profile `radial-power-fan-20260919.yaml` enables it with its own profile
identity; the pipeline remains on the required frozen 7.3.6 review base.

On native polar DBZH, 20 km block medians of `DBZH - 20 log10(r/50 km)`
nominate bounded 2–90 degree fans. At least eight measured blocks, 80 km measured
support and 160 km span are required. The 90th percentile block residual is at
most 2.5 dB and alternating-block median disagreement at most 1.5 dB. Model
support may bridge up to four degrees for nomination only. Shoulders are sought
within four degrees, without crossing invalid rays or geometry discontinuities;
no more than 20% of usable samples may be within 6 dB of the target. Missing
flanks remain unknown, not measured zero reflectivity. No gap is filled.

Only observed, unprotected gates within 6 dB of the per-ray model are proposed.
`RV2_POWER_FAN_MASK` (uint8) and `RV2_POWER_FAN_RESIDUAL_DB` (float32, NaN outside
the mask) record the cause. The mask must be a subset of `RV2_GROUP_MORPH_MASK`
and passes through the existing group-morphology proposal, quarantine and numeric
eligibility path. Audit mode still prohibits actions. Raw reflectivity is immutable.
This is morphological evidence with a range-law constraint, not calibrated
receiver-power proof. A single-case replay does not establish generalization.

### RAW fragment families (2026-10-01, opt-in nomination only)

`raw_fragment_families_enabled` preserves every observed, locally bounded RAW
fragment, including sub-kilometre fragments. `RV2_RAW_FAMILY_MASK` is an independent
diagnostic nomination, NOT an action, legacy source claim, or a new segmented-fit
training/target input. Families are frozen from RAW before any derived remnant.
Every gate retains fragment length, object identity and extent, physical width,
angular boundaries, measured bilateral support at 20/60 km, and a hold-reason bit
set. Missing flanks never supply measured contrast. Original weather/conflict,
plateau, geometry and acquired-ray-gap barriers remain effective even in gaps.

Association uses the original first fragment as an immutable reference, at most
one native-beam angular hop, 60 km range gaps and 180 km total range extent. New
members do not extend the angular search. Fragment lengths/support count only
actual observations; no gap is filled. Beam metadata absence is recorded as a
resolution proxy. Resource exhaustion abandons only this diagnostic path.

All fields are native-shape, `RV2_RAW_FAMILY_MASK`/`WINDOW_BITS`/`BEAM_PROXY_MASK`
uint8, `ID` uint32, `HOLD_REASON` uint16 and remaining evidence float32 (finite
exactly on nominees). Reasons: no independent evidence=1, insufficient support=2,
insufficient span=4, unstable boundary=8, missing bilateral window evidence=16.
These are review reasons, not a calibrated interference probability.

### Family joint qualification (B step, experimental opt-in)

`family_joint_enabled` requires `raw_fragment_families_enabled`. Existing source
fits continue to receive their original candidate set. Family qualification runs
only after those fits, using frozen RAW families and original independent source
seeds; newly associated remnants are never seeds. Qualification is experimental
quarantine, not a confirmed receiver-source declaration or trusted-weather repair.

An anchored family needs >=10km of actual original source support, stable
boundaries, an existing original object ID, and each target within 120km of an
original source gate. A family never expands beyond its original RAW extent.
Unanchored families need >=8km actual support, >=80km span, aspect >=12, stable
boundaries, both measured 20/60km windows and reliable local polar evidence:
SNR>=10dB plus RHOHV<0.7 or RHOHV<0.85 with |ZDR|>3dB (existing polar policy).
Neither SNR, polar missingness nor phase variance alone authorizes deletion.
Positive weather/conflict/geometry/plateau barriers veto either branch.

`RV2_FAMILY_JOINT_*` saves candidate/qualification/source-seed/polar-availability
masks, frozen parent identity/distance, per-object hold reasons, and measured
RHOHV texture and circular PHIDP variance with actual-sample counts. Texture and
phase variance remain diagnostics until independently accepted thresholds exist.
Missing measurements remain NaN, never zero evidence. No future scan/context is
used. Audit mode records qualification but emits no action proposals.
# Complete original source ledger (research, 2026-10-01)

## Windowed original-source continuation (research, 2026-10-01)

`source_window_tracks_enabled` requires the complete-source ledger. It operates
after the unchanged original source fit. It freezes RAW tracks on original source
rays, then permits at most one native beam of angular association from those rays.
It never seeds from a new action. Narrow bands use physical20/60km windows, actual
target sample support, pooled internal occupancy and separately recorded measured
flank availability; unobserved flanks confer no measured-clear-air vote. At most20%
strong flank contamination and at least60% internal support may nominate a window.
Both windows require at least3 original observed gates and500m actual support.
Protected/missing-geometry range barriers clip windows and split tracks, including
barriers in empty gaps. Missing range gates never enter support or actions.

An original source must supply at least10km actual bounded support across at least
3 distinct20km blocks and60km span within its frozen RAW track. Boundary jumps are
limited by native beam width per20km, capped at2 beams across a gap; centre offset
is limited to one original beam. Width remains at most8 degrees. Targets remain
within the frozen RAW extent,120km of original supported seeds, and within one
beam of the original source ray. Candidate gates with an observed strong flank
at that gate remain excluded. Multiple competing original parents always hold.

`RV2_SOURCE_WINDOW_*` preserves candidates, qualification, original seed and parent
IDs, physical bounds/support, window statistics, availability, hold reason and
original distance. Only the qualified path can join experimental geometry
quarantine; audit cannot act. It never claims confirmed receiver interference.
Wide fans beyond8 degrees and unanchored fragments require their separate models
and independent evidence; this path does not grant them narrow-source lineage.

`source_ledger_enabled` requires RAW-family diagnostics. It does not add candidates,
source-fit inputs, qualification, or actions. `RV2_SOURCE_LEDGER_*` records **all**
original independently accepted source gates, not their intersection with the
180 km RAW-family tiles. `SEED_ID` is a frozen original-ray/range-track identity;
it is not a confirmed interferer identity. `KIND` is a bit set: original reliable
receiver=1, original line source=2, line morphology=4, isolated line=8, grouped
morphology=16. Missing observation, weather/conflict/plateau and geometry barriers
cannot enter seeds or links.

All original source tracks retain start/end/actual radial support, including short,
wide or unstable ones. Range gaps over60km split tracks. `SOURCE_HOLD` records short
support=1, absent narrow boundary=2, unstable boundary=4. Only existing sources with
at least10km actual support and stable narrow RAW boundaries produce geometric
links. RAW range is frozen once using original seeds and original RAW corridors,
with120km maximum distance to an original seed. It is independent of nominated or
newly linked fragments. Links only nominate geometric lineage: no new polar/source
vote, no action, no filling, no recursive radial/angular growth.

`CANDIDATE_MASK` is the RAW nomination mask. `LINK_MASK`, `LINK_PARENT_ID` and
`LINK_DISTANCE_M` retain unique bounded geometric matches. Competing original
parents cause permanent ambiguity=16, even if a third source reaches the same
target. No parent=8. Original seeds cannot be relabeled as new links.
`RAW_PARENT_ID` records unambiguous original-ray RAW ownership; it does not imply
that every observed gate in this object is nonmeteorological. Wide sources remain
in the ledger with a hold, pending a separate wide-source model.

Persisted validation checks original source membership, original support/bounds,
parent existence, frozen RAW extent and nearest-original-seed distance. Full
geometry/model classification and independent context are still necessary before
these diagnostic links may be used for actions.


### Original-source footprint research (2026-10-01, offline only)

`RV2_SOURCE_FOOTPRINT_*` is not yet a persisted production eligibility contract.
The offline qualifier requires a frozen RAW fan parent with original independent
source ledger gates. Targets cannot become seeds. Targets remain inside the
original source range and angular stencil; each target and adjacent 20km range
blocks are excluded from boundary training. At least three other original
source blocks have adjacent source rays, >=10km original reference support and
>=60km span. Boundary drift is <=two native spacings, width <=12 degrees, and
target distance to original references <=120km. A known weather/conflict barrier
anywhere in the original angular stencil splits the range. Missing target DBZH
is never filled. Available current rho>=0.95 at SNR>=10dB vetoes this geometric
path as conservative retention, not independent weather truth.

Outputs carry candidate/qualified masks, frozen RAW parent ID, hold reason and
original boundary/range/reference support proof. Qualification is a bounded
association proposal, not a new confirmed-RFI source or an already-published QC
action. Production promotion still requires full source/writer recomputation
validation, preserved weather and exclusion gates, holdouts, and the complete
QC/Hybrid/composite/image pipeline.

### Source-footprint engine and serialized evidence (2026-10-01)

`fragment_line.source_footprint_enabled` defaults to false and requires RAW fan
families (and their original source ledger). Qualification participates in the
geometry proposal only in `experiment_quarantine`; audit has no actions and it
never adds confirmed source gates. It uses the existing group morphology reason.
The original source ledger is frozen before footprint qualification.

Writer validation reconstructs native ordering, range, azimuth, good/gap masks
and measured RHOHV/SNR availability from `RV2_SOURCE_FOOTPRINT_NATIVE_*` and
`MEASURED_*` fields, binds range/azimuth to original ledger evidence where present,
and reruns qualification from original parent/seed IDs and external observation
and barrier masks. All masks, holds, identities and scalar proofs must match the
replay exactly. Stored reference counts or qualification masks alone do not
permit action. Native evidence is ray/gate shaped for restore and shard writing;
no geometry interpolation or missing-data filling is performed.

This is an experimental implementation, not acceptance of the source rule on
independent weather cases or a deployment promotion. The read-only review script
supports `--source-footprint`; its source-stage projections are not published QC,
Hybrid Scan, composite, or Web products.

Source footprint decision tracing adds `RV2_SOURCE_FOOTPRINT_REJECTION_CODE`
(uint8). Codes are meaningful only where CANDIDATE_MASK=1: 0 qualified; 1 no
original source; 2 insufficient original support/rows; 3 native angular gap;
4 outside original extent; 5 original stencil barrier; 6 guard-excluded reference
support; 7 insufficient adjacent source boundary blocks; 8 unstable original
boundary; 9 outside reference boundary/distance; 10 current measured polar veto.
Writer replay validates the trace alongside eligibility. This does not modify
action criteria. The audit script's measured outer-SNR indicators are diagnostic
only and do not establish weather absence or contamination truth.

Source-footprint v2 freezes each contiguous ORIGINAL source-row bundle within
the same RAW parent separately. Each bundle must independently meet the existing
original support, guarded reference-block/span, native geometry, boundary stability
and polar retention rules. Disconnected bundles cannot lend each other support;
their angular gap is outside both footprints. Range limits and barrier splitting
are recomputed from that bundle's original gates only. Weak accepted tails never
extend a bundle or become reference gates. Serialized writer validation reruns
this partition from original parent/seed evidence; the feature remains default-off.
