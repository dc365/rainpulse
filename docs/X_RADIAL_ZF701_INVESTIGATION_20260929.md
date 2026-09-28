# ZF701 radial QC investigation, 2026-09-29

Status: marked-frame intermittent radial correction deployed and visually verified; sparse points and the adjacent strong broad sector remain separate limitations.

## Frozen case and live evidence

- Scan: `07bdd490-9202-5772-8c90-2d529688298d`, sweep 0, UTC 2026-08-28 00:03:32.136–00:06:59.058.
- User-selected result: `2cb2cd37-5d79-46cb-9714-3b7e9d23186f.b2f2b9c6-12ef-4167-a9fa-24ffc704365e`.
- Latest catalog result at inspection: `6472afda-7ceb-496b-b5ea-3be25c59dab6.6d707899-67de-4a87-9378-04494dd58dbb`, finished 2026-09-28 16:32:15Z, run `12b9be68-2ede-4526-bf0c-3311dd599515`.
- Opened both live results. Latest actual map still has obvious long radial spokes. The old result link contributes to confusion but does NOT explain the remaining defect.
- Latest native evidence agrees with the recorded 9,154 rejected gates: 7,183 noise-floor, 579 radial-polar, 857 fragments (cause masks need not be disjoint). These totals do not prove that the visually dominant spokes were removed.
- Surviving >=15 dBZ gates beyond 25 km: ray 321.41° has 578, ray 31.41° has 512, ray 214.30° has 454. Earlier probes at round bearings such as 320°/30° can hit another ray and falsely imply successful removal. Always use actual native ray identity and all-ray ranking.

## Mechanisms confirmed from actual input

1. `xqc_v2/core.py:measured_flanks` requires valid DBZH on the neighboring ray as well as independently available SNR. Around 321.41°, the neighboring receivers have measured SNR around -3 to -5 dB, but no valid REF. Consequently the existing flank test supplies zero confirmations despite a 14 dB median SNR on the target ray. These neighboring samples are NOT verified no-echo: the adapted sweep has zero no-echo gates. Missing REF must never be silently relabeled no-echo.
2. The X radial polar conjunction requires RHOHV <=0.9. The dominant 321.41° spoke has median RHOHV 1.0 and ZDR 6.4375 dB; the 214.30° spoke has median RHOHV .995. High RHOHV alone is not proof of precipitation, but removing its protection alone is not a validated correction either.
3. The spatial phase-jitter diagnostic is NaN over several isolated spokes. The fragment path's same-ray circular-phase evidence is downstream of confirmed anchors and also retains the RHOHV cap; it cannot rescue an unconfirmed high-RHOHV source.
4. The shared coherent receiver model does not cover this sample. An offline-only diagnostic with its minimum SNR lowered to 8 dB still yielded zero models: receiver nonstationarity, paired relation, and noncoherent-reference gates refused the fits. Do not ship this threshold change or assume the S core is sufficient without a matching source family.

## Rejected fix experiments

All experiments used the original frozen cut and temporary in-process function replacement, without modifying the running worker or published artifacts.

- Allowing measured below-floor SNR as a flank without requiring neighbor REF: radial-polar 579→1,470; rejected 9,154→9,762; 615 newly rejected and 7 formerly rejected now retained (fragment nearest-anchor association changes). Of the 355 gates in the inclusive sector 165–210°, SNR>=8 and DBZH>=15, 60 were newly rejected.
- Requiring BOTH immediate neighboring receivers to be quiet: 442 newly rejected; 53 in that same sector control.
- Requiring an uninterrupted corridor at the largest configured radial scale: no change on this cut. This is not an effective fix.
- A unit test reproduced the independent-moment flank failure before the first patch; five flank tests passed afterward. The candidate full X/multiband tests passed (121), but that did not override the real-data control failure. All candidate source/test changes were reverted; forensic patch and replay scripts remain under `.build/`.

The sector control is a historical proxy, NOT independently labeled rainfall truth. Counts alone cannot establish either true rainfall removal or perfect preservation. The earlier project-memory claims of "near clean" and unconditional "zero rain removal" are superseded by this investigation.

## Next acceptance work

- Obtain independent precipitation evidence for the southern sector using contemporaneous S observations and/or rain gauges or reviewed labels. The UTC00:06 S catalog contains two participating S scans; its other two station frames are explicitly later reference observations and must not be treated as simultaneous truth. Beam height and coverage also matter.
- Freeze several ZF701 times and clean/rain controls from other X stations. Report all native rays, actual azimuth, range, missing/observed status and per-gate veto reasons; do not infer success from a few integer-angle probes or aggregate deletion totals.
- Design the missing noncoherent receiver-source evidence path using independent angular/range/temporal/polar evidence, retaining explicit precipitation protection and unavailable-data states. Do not repeatedly relax the present conjunction to match a screenshot.
- Regenerate a new immutable result only after the real-data comparison passes; verify native QC values, geographic previews and the exact live result URL together.
- Separately reconcile deployment/source drift before any whole-module redeployment: live `geographic_sweep_preview` samples y south-to-north while the local committed file samples north-to-south; the shared renderer flips rows. This difference was observed but is not the cause of live residual radial values and must not be overwritten accidentally.

## User clarification and bounded source candidate

The user confirmed that obvious radial morphology is a QC target, including the southern strips. The old southern-sector proxy is therefore not a weather truth label and is not an acceptance veto. This does not establish quantitative rainfall preservation.

Added an opt-in bilateral receiver-corridor detector. Both sides require actually measured below-floor SNR; missing REF is not recoded as no-echo. A corridor must span at least 20 km, have at least 70% supported gates, and be at most the configured angular width. Range-block predictions exclude the target and adjacent blocks. Receiver SNR and the paired reflectivity range response must remain within bounded spread; those two quantities are related and are not counted as independent physical votes. Explicit weather protections, the finalizer and global action budget remain active. Defaults keep the detector disabled.

Current-session evidence (offline, not a deployed result):
- 127 X-QC/multiband tests pass, including high-rho source, missing shoulder SNR, wide echo, variable-range echo, explicit protection, held-out target block, audit/quarantine and global budget regressions.
- With a 5-degree maximum corridor, the frozen first cut has 3,924 source gates and 11,851 total quarantine gates. The narrow source-only check removes 578/578, 492/512 and 546/568 >=15 dBZ gates beyond25 km on the actual rays321.41,31.41,214.30 degrees. These figures are not all-ray success rates.
- All9 cuts evaluate normally; source counts3924/731/708/0/0/0/0/0/0. Adjacent scan93d2d253 firstcut source count0; existing quarantine9490 unchanged. Broader/discontinuous radial forms remain unresolved.
- ZF605 scanabd4e26b firstcut: source0, quarantine0. This is a limited clean control, not proof of zero rainfall loss.
- No new candidate has been deployed at this point. Whole-module deployment must also reconcile the geographic rendering drift noted above.

Recheck before release: current local `geographic_sweep_preview` samples south-to-north, matching the running worker; the earlier drift note is not reproduced on the current source. Deployment will layer only the three changed XQC files on the existing r6 image. ZF605 nonempty controls (cuts3/4/6/7/11/12/38; respectively282/172/219/549/113/744/158 observations) also have zero new source gates.

## Targeted deployment and live acceptance

Source commit1d81cd1 pushed; 105 layered images x-radial-1d81cd1-mb/-qc. OnlyZF701 enabled. Existing S workers and paused full-day jobs untouched. Two-task run d28c261b-63cd-440b-96d0-c18533cc9d83 SUCCEEDED. New first-scan result9c1ed4b8-af5c-4b71-9f9b-2af26c9bdb54.874de744-ae0d-4be7-bc5f-2f329e5efd60 is displayed and inspected in the live browser. Published native arrays match offline counts; source gates all action2 and NaN in QC, QPE eligibilityzero.

Actual >=15dBZ gates beyond25km remaining after all QC:

| Actual bearing | Raw | Remaining |
|---|---:|---:|
|321.41|578|0|
|31.41|512|20|
|214.30|568|10|
|13.36|459|245|
|195.23|363|360|
|110.33|304|304|

This is a partial fix. It does not resolve broad/interrupted radial strips or establish a rainfall false-removal rate. Source gate count3924 overlaps existing causes; total newly rejected is2697 (11851−9154), not3924. Keep the unresolved classes in the acceptance ledger.

## 2026-09-29 repeated-defect correction: intermittent source blocks

The new screenshot explicitly labels the north, east and southern interrupted strips. The earlier correction targeted contiguous narrow sources and was not complete. Failure conditions re-read and measured on the frozen input:

- Per-gate range occupancy rejects a source with valid REF in only40–60% of gates, even when measured SNR and the range response repeat for50km. Missing REF must not be used as a no-source vote.
- A single median envelope over the entire ray mixes near/far power regimes and gate-amplitude modes. It leaves dotted fragments and can veto all distant matches.
- A weak edge ray cannot supply the same absolute shoulder contrast as its angular source peak. The actual southern corridor needs6.22degrees; the old5degree bound excludes it. The reviewed ZF701 configuration uses7degrees, with broad11ray weather preserved by regression.
- The existing35% heuristic budget correctly abstains when the newly recognized source fraction exceeds that configured cap. The detector must not bypass it. The explicit ZF701 candidate configuration uses60%; other stations and S stay unchanged.

The optional block model retains bilateral measured-quiet shoulders, geometry gaps, explicit weather protections and the finalizer budget. It estimates robust block centers, fits the range-response slope, and requires20km reference span,1.75x range leverage and >=70% block support. Constant-reflectivity rain has a -20dB/decade residual slope and is rejected as a source. Each target and adjacent blocks are excluded from training. Robust per-block5/95percentile envelopes handle recurrent amplitude modes without turning one reference outlier into a wide gate envelope. Models record their reference blocks and gate envelopes. No station/bearing/time/screenshot rectangle is encoded in detection.

Regression command: `uv run --project algorithms python -m pytest algorithms/tests/xqc_v2_20260928 algorithms/tests/multiband -q`.135 pass. Red run before implementation: intermittent and changed-power source tests returned0 detections; the repeated-amplitude test also failed before the gate-envelope correction. Controls include broad rain, constant REF, short echo, missing shoulder/sector, explicit protection, held-out rain core, raw preservation and action-budget abstention.

Offline evidence: firstcut8733source /16605quarantine (previous11851); main rays13.36/349.20/195.23/162.21/110.33/321.41 retain2/10/6/1/2/0 >=15dBZ gates beyond25km. All9cuts evaluate normally. Nextscan93d2d253 firstcut5497source/14313quarantine (previous9490), actual195.35ray303→7. ZF605 seven nonemptycuts have0newsource; ZF101 f34ee820 firstcut also0newsource. These are limited controls, not a quantified rainfall false-removal rate. Candidate applies only toZF701; no network-wide promotion, QPE or trusted fusion.

### Live verification for d075740

Deployment105 reused the existing build context; source file hash matches local. Run01af4bba-a6c6-4ad0-bea2-017fa247f3df succeeded2/2. First resultdd9c37cf-5714-4c2c-ae5a-fa7df0d6c3ae.a7a1bde0-92db-4f73-aab4-7ec61da0c707 and nextresulte6019c1e-c90f-41b3-86b3-20fd7f9c76c1.41774257-9a82-4feb-b842-7c803c7026cd were opened in the actual map UI. Source/reject counts match offline8733/16605 and5497/14313. Published source gates are action2/NaN; QPE eligibility iszero. Both exported DBZH_RAW hashes exactly match the frozen normalized cuts.

In approximate bearing sectors corresponding to the three marked areas (north345–20, east100–120, south150–210degrees), >=15dBZ gates beyond25km remaining dropped1022→44,651→13,1693→132. The marked frame no longer shows the previous prominent long strips; sparse points remain. The next frame's strong broad southern wedge remains visible and has not been established as rainfall or interference. Do not call that wedge preserved rainfall truth or claim all XQC is complete. OnlyZF701 enables the block model; S and paused automations are unchanged.


## Strong fan recurrence: multi-elevation acceptance (2026-09-29)

User supplied original scan sweep 3 (3.36 degrees) and adjacent scan sweep 0
(0.47 degrees). These are explicit interference targets, not verified rain.
The original source detector admits only DBZH below 45 and a family narrower
than seven degrees. Ablating only the intensity cap changes neither sample;
angular family exclusion dominates, with the cap also blocking strong cores.
At original sweep 3 ray 195.46, median SNR remains approximately 58 dB from
10 to 60 km, while median REF rises from 59 to 71 dBZ; range-normalized REF
remains approximately 35–36 dB. Independent native values reproduce the issue.

An opt-in `radial_source_fan_model_enabled` candidate now evaluates receiver
families: measured bilateral quiet shoulders, held-out distance blocks,
stationary receiver power, range-response fit and two neighbouring supported
models. It uses the valid instrument REF range, not the weak-spoke cap.
No station, bearing, time or elevation is encoded. Default remains disabled.
Missing fields/gaps, protected gates and action budget remain gates.

Regression red run: three strong fan variants all had zero removal before the
implementation. Merely widening the family misclassified a synthetic
range-varying weather profile; that variant was rejected. Candidate adds an
explicit receiver stationarity condition. Tests cover four weather length
scales, high elevation, strong REF, angular seam, missing angular sector,
missing receiver observations, explicit protection and embedded rain core.
Full XQC/multiband suite: 145 passed. Synthetic weather is a regression control,
not independent precipitation truth or network-wide acceptance.

Current offline evidence, NOT a deployment or completion claim:

| Original cut | Strong gates beyond 25 km (>=35 dBZ) | Rejected |
|---|---:|---:|
|0|2|2|
|1|3|2|
|2|1112|777|
|3|1929|1724|
|4|1243|1167|
|5|429|400|
|6|137|132|
|7|245|185|
|8|0|0|

Adjacent cut 0: 3643/3801; ZF101 control adds zero source gates.
These are broad diagnostic counts, not an annotated interference recall score.
Residuals in cuts 2/3/7 require further gate-level examination; all-time,
all-station efficacy and independent rain-loss acceptance are NOT established.
Do not enable or deploy this candidate as a completed fix yet. Current 105
still runs d075740. Hourly jobs stay paused and S workers are untouched.
Local evidence: `.build/fan-ablation.txt`, `fan-evaluation.txt`, `fan-allcuts.txt`,
`source-fans-red.txt`; raw private exports remain ignored under `.build/`.


### Follow-up correction and release validation

The residual probe found four additional structural conditions, covered by
red/green regressions in `test_source_fans.py`:

1. Changing proportions of two REF response modes moved the old midhinge.
   Fan references use the upper response mode with independently stationary
   measured receiver power; no target/guard block trains its own model.
2. A nonzero accepted range-response slope was not used to predict target
   envelopes. Fan envelopes now use detrended reference residuals and restore
   the fitted trend at each target range. Evidence records this transformation.
3. A strong isolated spoke fell between weak-spoke intensity limits and the
   two-neighbour fan gate. It now uses the same source evidence with local
   bilateral shoulders. Sparse REF rays in a measured fan seek support within
   that family's measured receiver boundary; missing telemetry/angular gaps
   stop traversal rather than counting as quiet shoulders.
4. REF-only dropouts with unchanged modelled receiver power remained as dotted
   spokes. Once the target has a held-out source model, a weaker REF value does
   not make a contaminated receiver measurement clean. Unexpected stronger
   REF or changed receiver power still falls outside the model.

A suspected local weather proxy cause was disproved in the principal residual:
its hard/local masks were both zero. No weather-protection policy was changed.
Independent and local weather protections retain their prior behavior.

Additional frozen evaluation scan: 60b0ff34-de91-5475-a715-7e3cab45a370,
UTC03:26:01.434; all nine cuts added zero source gates. The two user scans are
also evaluated over all nine cuts. Raw arrays remain unchanged. These controls
are not independent rain-gauge truth and do not establish zero rainfall loss.

Adjacent scan cut2 proposed13768/21994 (62.6%) under the previous candidate,
which exceeded the site's60% cap and erased the whole enhancement. Reviewed
ZF701 test deployment raises its explicit cap to65%; the common default,
action-budget enforcement, S configuration and other station profiles remain
unchanged. Native scan/cut identity and per-frame abstention must be checked.
Candidate outputs do not enable trusted fusion/QPE/forecast.

Verification commands:
```
uv run --project algorithms python -m pytest algorithms/tests/xqc_v2_20260928 algorithms/tests/multiband -q
git diff --check
```
Red evidence (ignored local artifacts): fan-residual-red.txt,
strong-spoke-red.txt, fan-sparse-red.txt, fan-dropout-red.txt. Published results
must be new immutable tasks, with source/config hashes and raw-data checks.
The old pinned URLs intentionally remain old results.


### Published verification — fc757e8

- Source committed to main and pushed before105 deployment. Images
  `rainpulse-cpu-worker:x-fan-fc757e8-mb/-qc`; onlyZF701 opt-in profile changed.
- **150 tests passed.70/70 registered scans reprocessed;630/630 native cuts
  evaluated and audited.** Unique scan IDs exactly match the frozen catalog,
  UTC00:03:32.136–06:56:19.288. This is registered coverage, not a24hour claim.
- Every published raw-array hash matches its normalized source. All recognized
  source gates have exclusion action2 and missing QC values; QPE remains off.
  No action-budget abstention;173cuts contain source detections.
- Live map verification covers original3.36degree cut, next0.47degree cut,
  next2.36degree cut formerly blocked by budget, plus08:15 and11:03 times.
  Dominant fan/spoke patterns are removed in the inspected problem views.
  Sparse local residuals remain; task success is not zero-error meteorological
  truth. Other-station operational promotion and independent rain-loss scoring
  are outside this candidate verification; no trustedfusion/QPE/forecast.
- First run dc0c65e1-45f7-46d6-b3ea-04044a72bf67; original result
  cdf17f1f-dd23-4448-8319-f621ac206a0b.68fc6e8a-77ce-498d-af70-f52babbe3f69;
  adjacent result176858fc-dc52-446e-a852-84e8f735d870.d91d9053-24a6-4832-95a8-738874bc4b60.
- Audit SHA256953e38687ac9e34a36ea0de0c2a014d366d64ba635df3583315ebcae6f96f09e.
  Local ignored fan-release-summary.json and fan-audit.jsonl were copied to
  existing105 `.build/x-radial-1d81cd1/`; release-fan-fc757e8.json records
  REGISTERED_SCANS_REPROCESSED_VERIFIED and names rollback files.
- S services/configuration are unchanged. Scheduled follow-up stays PAUSED.
  Old pinned result URLs remain immutable; use the new result identity.
