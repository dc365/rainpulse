# Original-source radial object morphology

User clarification: a residual on the same radial as an identified contamination
source, with radial morphology of its own, can be quarantined as the same object.
It need not independently satisfy a constant-power receiver model or rediscover
three other weak tails. Mere polar-coordinate alignment remains insufficient.

`anchored_radial_shape.py` implements the qualification independently of engine
actions. It is wired into the existing opt-in footprint qualification path; the new package is not yet deployed. Original ledger IDs are frozen
source clues, not independent weather truth or newly confirmed pollution.

Qualification requires an original source and its frozen range. Same-native-ray
association remains available; neighboring rays additionally require the same
frozen RAW fan parent within two measured native beam spacings. Original ancestry
is selected by nearest angular source before qualification; a failed nearer
source cannot be replaced by a farther convenient fit. Equal-distance competing
identities abstain. Cross-row support cannot cross gaps or protection anywhere
between the source and the target shoulders.

Other requirements include an
unbarred target three-ray stencil, at least 10 km original reference support in three
distance windows spanning 60 km after excluding the target and adjacent 20 km
windows. The RAW weak-member partition is made once from the original ledger;
it is never recomputed from an evolving QC result. Side observations establish
sampling coverage, not no rain. A thin or strongly concentrated foreground
pattern must have valid observation coverage on both sides.

Two shape paths apply:

- A weak radial chain has three independent shape-reference windows spanning
  80 km and physical radial aspect at least eight.
- A local radial fragment can instead inherit the long original-source support
  (100 km span, aspect eight), provided it contains four consecutive actual
  native gates with at least 1 km support. It does not train from other tails.

Explicit current polar weather and external protection are retained. Protected
distance gaps split support/action intervals. Missing DBZH is never filled.
Competing original source identities produce permanent ambiguity abstention,
not a convenient winner. Unanchored points cannot qualify, and output members
cannot become new sources or extend the frozen angular/range footprint.

## Reproduce and assess

```sh
PYTHONPATH=algorithms .build/xqc-zf702-investigation/venv/bin/python \
  scripts/audit_s_original_boundary.py --method anchored-shape \
  --source-report .build/s-discontinuous-20261001/complete-source-extent-20261002-v1/report.json \
  --published .build/s-discontinuous-20261001/published-v6-z9598-0818-fan-v1.json \
  --published .build/s-discontinuous-20261001/published-v6-z9598-0842-fan-v1.json \
  --output FRESH_OUTPUT_DIRECTORY
```

Current authoritative receipt: private `anchored-integration-20261002-v2/report.json`
under the same evidence root. Only the exact stored-visible overlap is an old-Web
residual comparison; total proposals are not production removals. Earlier v8
results are superseded by the counterexample repair documented below.

Targeted regressions cover nonconstant-power recovery, broad weather, missing
sides, gaps, frozen extent, held-out training, current weather, competing
ancestry, local fragments, native transverse contrast and serialized replay
tampering. The focused anchored/source-footprint suite passes 47 tests in the
current shared checkout. Installed-image and independent weather validation
remain pending.

## Frozen narrow radial bands

Several adjacent contaminated beams may form one narrow original object. The
band is frozen from original source-bearing rows sharing the same RAW parent;
new weak members cannot nominate new boundary rows. A distance-local parent
clue retrieves the complete immutable source ID before measuring support.
Each band row must independently retain held-out original support. The full
pre-clear RAW band, including strong original members, supplies geometry;
previous clearing cannot fabricate holes. Width enters the physical aspect
ratio and protection/gaps cover both outer shoulders and the entire band.

An unrelated short source present only in the target window remains an actual
shoulder return. Removing all seed-marked shoulder gates would fabricate
isolation; the regression explicitly rejects that case. These conservative
checks reduce other unbound proposals and retain 08:18's overlapping fan
residuals. They do not establish that those fans are weather.

The reusable renderer now accepts `--anchored-report REPORT_JSON`. It checks
snapshot, published receipt, diagnostic hashes, mask ownership/ambiguity and
count agreement, and labels proposals as **not published QC**. The current 08:18 integration-v2
review image highlights exactly 16 newly qualified old visible residual gates;
gray background is RAW context, not a full QC field. The earlier v8 08:42 image
is superseded and must not be used as current acceptance evidence.


## Integration and withdrawal of unsafe v8 proposals

The earlier v8 proposal gain (147/728 at 08:42) is **withdrawn as a deployment
claim**. Engine integration exposed a uniform-weather counterexample: excluding
original marked shoulders could fabricate isolation in an otherwise broad RAW
field. The fixed outward neighbour now remains actual foreground evidence,
with measured coverage, gap and protection checks. v9 retains only 6/728 of
those old published targets. Do not deploy or present the earlier 147 as proven
weather-safe removal.

The new native transverse-contrast path uses actual DBZH on the target and both
adjacent rays with interpolation by their actual angles. A stripe may be a ridge
or groove, but needs measured pairs, median contrast of at least 6 dB and 80%
per-gate sign support above 3 dB in a window. Its target/adjacent windows cannot
train: at least three other windows spanning 100 km must retain consistent
polarity (80%), plus the original-source support and physical aspect checks.
Magnitude may vary; this is not a constant receiver-power fit. Linear angular
background, alternating polarity, target-only contrast, unanchored returns and
explicit current weather are retained by regressions.

`source_footprint.py` v4 merges only replayable accepted proposals. It persists
actual native DBZH and availability, original IDs, policy/added masks and full
shape evidence. Restored native order is replayed before masks can support an
action. Existing opt-in footprint policy and audit/no-action mode remain in
control. Historical artifacts without the marker replay their original v3 path.

Final integration receipt (private):
`anchored-integration-20261002-v2/report.json`, baseline commit `8426ddb`.
Its eight input snapshots and both source modules are bound by SHA. The previous
456/457 scoped current-checkout tests are not a clean production deployment or
independent all-weather generalization proof. The old Web-bound 08:18 residual
has **16/2094 newly qualified** over v7; 08:42 has **0 newly qualified** over v7.
Other proposals are not all new stored-visible gates. The principal fan residual
is still unresolved; installed-image replay, live publication and wider weather
controls are required before claiming the requested end state.


## Current residual attribution: source association versus morphology

`anchored-integration-20261002-v2/ancestry-limits-v2.json` examines only exact
stored renderer-visible targets. Its bins are mutually exclusive and sum to the
published count; this is a coarse ancestry inventory, not an explanation of
every classifier rejection or pollution truth. `ancestry-limits.json` is
superseded because it included 53 non-visible 08:42 targets.

| Ancestry inventory | 08:18 | 08:42 |
| --- | ---: | ---: |
| Newly qualified over v7 | 16 | 0 |
| Existing protection | 0 | 45 |
| No RAW parent | 231 | 182 |
| Parent has no original source association | 766 | 30 |
| Original association exists only beyond two beams | 14 | 40 |
| Nearby original association covers target range | 1067 | 431 |
| Nearby association exists but range excludes target | 0 | 0 |
| Total old visible targets | 2094 | 728 |

This contradicts treating angular search width as the principal remaining
08:18 problem: widening it alone could address at most the 14 distant-source
cases under this inventory, while 1067 already have a nearby source/range
association. Their geometry, measured flanks, held-out support, native gaps and
ancestry competition must be examined as complete original objects. Another
997 lack a RAW parent or linked original source; merely extending accepted
fragments would manufacture their ancestry. These populations need separate
full-object association and sparse-band morphology work.

Reproduce the bound comparison image with the committed renderer interface:

```sh
PYTHONPATH=algorithms .build/xqc-zf702-investigation/venv/bin/python \
 scripts/render_s_qc_review.py \
 .build/s-discontinuous-20261001/live-web-aligned-components-v2/z9598_0818_sweep_000.npz \
 .build/s-discontinuous-20261001/published-v6-z9598-0818-fan-v1.json \
 --footprint-report .build/s-discontinuous-20261001/anchored-integration-20261002-v2/report.json \
 --output FRESH_REVIEW_IMAGE.png
```

The inspected image receipt binds 2094 selected/visible gates, 16 new proposals,
input/report/script/published/image SHA, and no product writes. It is not a
current 105 Web refresh. New algorithm deployment remains pending.


The later complete-fan integration supersedes the narrow-only v2 replay as the
current candidate: see `S_COMPLETE_FAN_SHAPE_20261002.md` for policy2/native
weather-island handling and 559/66 old-visible incremental proposals. Earlier
narrow-only numbers remain historical comparisons, not the deployed end state.
