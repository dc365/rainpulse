# ZF701 radial QC investigation, 2026-09-29

Status: narrow-source candidate deployed and verified; broader/discontinuous residuals unresolved.

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
