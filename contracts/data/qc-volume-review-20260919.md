# Native volume object review / bound evidence / CR qualification v1

Base: `c3df57c570594891c0cae796382420c4d385679e`. Extension: `volume-object-review-20260919-v1`.
Opt-in top-level `volume_review` in a non-operational QC 7.3.6 profile. It is absent by default and omitted from old parameter hashes. No scheduler, publication default or frontend is switched by installing this code.

## P0: immutable evidence

QC writer emits `qc/volume_review/evidence.json`; large object, reference and link records NEVER enter task/event summary payloads. With `export_evidence=true`, it also emits a manifest and deterministic per-sweep NPZ snapshots. Snapshots retain original acquisition row order, actual geometry, masks and numeric values; original raw data is not edited. Flag definitions and configuration identity accompany the data. Hashes are content receipts, not signatures or meteorological truth.

For every scalar polar layer actually rendered, the renderer binds the actual PNG to the source numerical snapshot, effective render-valid mask and actual polar RGBA. `native-footprint-v2` reproduction must match all RGBA pixels, not merely approximate alpha coverage. Mismatch raises before publication. Palette evaluation remains the existing scalar renderer's responsibility; its numeric input, output RGBA and palette version are retained. This is not recognition from screenshot colors.

Use an explicit child diagnostic profile with `polar_render.sweep_selection=all_dbzh_sweeps` for all-layer PNG export. Installing the extension does not override a frozen lowest-layer diagnostic profile. The profile generator can create this child from the real parent.

## P1: capability and objects

`Sweep` accepts finite per-ray coordinates, increasing uniform range, original measured moment arrays and explicit availability. Missing polar fields remain missing. A declared valid no-echo mask must not overlap detected DBZH; absent code semantics are never inferred as clear air. Duplicate rays and acquisition gaps must be explicit. Only finite relative or UTC-based seconds within the same volume may support timed links.

5/10/20/40 km candidate windows are experimental defaults, capability-routed independently per sweep. Multi-threshold `label`/`regionprops` describe actual measured components, area (`r dr dtheta`), width and boundary variation. Objects may be radial, annular or patch/fan; these are shapes, not weather labels. Higher contours form a hierarchy, not extra independent votes. Seam connectivity requires a real closed scan; acquisition gaps are barriers. No closing-filled cell acquires an observation or action.

Capability states distinguish `NOT_APPLICABLE_NO_DBZH`, `NOT_APPLICABLE_INSUFFICIENT_SPAN`, `NO_CANDIDATE`, `CANDIDATES`; reference status separately distinguishes unavailable moments, insufficient reference and evaluation. Counts include all nominated objects, including possible weather.

## P2: evidence and gate state

Source reference statistics use original same-ray measurements outside the target range block AND adjacent guard blocks. State membership is derived from training SNR only. A paired DBZH/SNR processing relation, strict residual and disjoint-block checks describe an uncalibrated source hypothesis. Coherent/noisy families require actual original polar measurements; incomplete weak data is diagnostic-only.

Source-coordinate corroboration requires independently qualified target and donor gates, real azimuth footprints, compatible slant range, different elevations and comparable acquisition time. It is same-system corroboration, not independent weather truth. Physical-space weather comparison uses actual relative `(x,y,h)` and positive observations only. No missing upper layer can refute shallow rain. Associations do not retrain references, propagate deletion or convert missing gates into values. Raw object links may be recorded without times, explicitly non-actionable.

Gate state: 0 missing, 1 untouched, 2 unresolved, 3 weather-supported, 4 source-supported/no weather counterevidence, 5 mixed. `VOR_REASON` uint16 bits: raw object1, target source2, donor corroboration4, local weather8, spatial weather16, external weather32, mixed64, insufficient128, experimental proposal256, resource abstention512. Scores are not probabilities.

`VOR_PROPOSAL_MASK` may be nonzero only in phase>=2 `experiment_quarantine`. New isolation is its intersection with ORIGINAL trusted observed gates. It never adds a confirmed RFI label. `VOR_QUARANTINE_MASK` is separate from older RFI and nonprecip quarantine; action becomes DOWNWEIGHT, all moment trust/QPE eligibility clear, usable DBZH becomes NaN, QI is capped. Whole preexisting trusted phase segments touched by new isolation lose derived KDP/phase/attenuation availability until recomputed with the actual environment; raw reflectivity and adjacent raw trust are unchanged.

Exact before-values are stored under `VOR_BEFORE_*`. Legacy validators execute against an exact pre-extension view before the new delta is validated. Never weaken old V7/OC1/NP validations to accept a later modification. Budget overflow retains qualified isolation and sets review-required; it is not an operational promotion.

Donor array VALUES are original acquisition ray indices, not just arrays restored to original row order. Donor sweep values index the sidecar's `sweep_order`. Source model sidecars retain both original ray and native-sorted ray. Raw digests explicitly identify the native-sorted view.

## P3: independent CR eligibility and winner sources

`REFLECTIVITY_ELIGIBLE_FOR_CR` uint8 starts from pre-volume reflection TRUST, not QPE eligibility. Old rejects and old quarantine are never revived. Source-supported gates are withheld. Unresolved/mixed gates follow explicit `unknown_cr_policy`: withhold (default) or retain-with-risk. This first implementation does not train a hydrometeor classifier or recover originally rejected hail.

`CR_UNCERTAIN_MASK` remains separate. `CR_QUALIFICATION_REASON` uint16: admitted1, old untrusted2, source-supported4, unresolved/mixed8, missing16. No available CR gate is not valid zero rain.

P3 produces numeric `CR_RAW`, `CR_TRUSTED`, `CR_UNCERTAIN`, `CR_RUNNER_UP`, valid/uncertain coverage, winner/runner-up source+ray+gate, winner reason and height. Source table resolves actual radar/sweep/scan/asset/config and numeric digest. Equal maxima retain first source in recorded input order. Heights use 4/3 effective Earth and are ABOVE EACH SOURCE RADAR, not an unverified common altitude datum. Original observed maxima remain maxima; no dBZ averaging, median substitution, smoothing or high-layer blanket deletion.

Mixed P3 configuration generations are refused. Audit saves candidate NPZ+receipt but leaves the existing composite image unchanged. Experiment selects trusted candidate; resource abstention restores parent inputs for the displayed baseline while keeping candidate uncertainty diagnostics. Publication remains `operational_eligible=false`.

## Research boundary

The legacy multisweep NPZ bundle is not a bound business input. Its finite `DBZH_QC` is not a qualification mask. Raw-only replay reports `UNBOUND_INCOMPLETE_LEGACY_EXPORT`, no scientific accuracy/removal metric and no real CR reconstruction. End-to-end Zarr/Worker/API checks and independent weather holdouts are required before any deployment or operational admission.
