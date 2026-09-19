# Fragment line evidence v1

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
