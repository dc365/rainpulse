# X radial residual repair: acceptance contract

## Evidence encoding

When native QC evidence exceeds its configured JSON byte budget, homogeneous
record lists may use lossless `xqc-record-table-v1` encoding: `columns` is the
ordered key list and `rows` contains values in that order. Nested values retain
JSON types. No record, precision, cause or model is dropped. Module summary
keys remain readable. Legacy lists remain supported. If the lossless encoding
still exceeds the budget, explicit abstention remains mandatory.

## Source acceptance

Frozen failure cases: ZF701 2026-08-28 UTC 00:07:21 cuts 0/1, UTC 00:49:25 cut 0;
ZF702 UTC 00:08:25 cut 0. Raw arrays stay immutable. Evaluate native radial
continuity and fan residuals, all elevations and other times, not just job success
or total removed gates. Protected weather, missing SNR, unknown angular coverage,
range-varying rain and independent rain cores are regression controls. No fixed
station, azimuth, time or elevation exclusion is permitted. Candidate results
remain ineligible for trusted fusion/QPE/forecast.

## Implemented corrections

- Preserve the original evidence byte cap with reversible record tables; retain
  explicit abstention when the compact form still does not fit.
- Fit primary (median) and upper (90th percentile) response modes separately.
  Both exclude the target and guard blocks; receiver stationarity, range leverage,
  measured shoulders, response slope and protected-weather rules are unchanged.
- A below-floor block median is not an empty shoulder when independently fitted
  source gates occupy that same block. Unknown coverage remains a hard barrier.
- Admit an independently fitted corridor when at least the existing minimum
  fraction of measured above-floor gates is explained across the minimum range
  span and reference block count. Missing neighbour REF is not a veto.
- Associate short interior gaps between original source anchors only. Both SNR
  and range-normalized REF must stay below the neighbouring source envelope plus
  the existing spread limit. No iterative growth, end extrapolation, missing-gate
  creation or protected-weather override is allowed.

## Verification before deployment

Regression failures were observed before the corresponding fixes: secondary
response mode 9% detection; sparse fan corridor 0%; receiver dropout 0%; whole-cut
record overflow removed all proposed actions. Paired weather-core tests remain
negative. The complete XQC/multiband suite passes 160 tests at this stage.
Real controls: ZF101 828 strong distant gates untouched, no new source detections;
ZF701 UTC03:26 all nine cuts have zero source detections. These are controls,
not independent precipitation truth. Raw data and screenshots stay in ignored
`.build`; synthetic regressions are committed.

Real samples now EVALUATED including ZF702 previously rejected by the 4 MiB
record cap. Remaining native >=15 dBZ gates beyond15 km (not an annotated error
metric): ZF701 next cuts0/1 691/613 ->312/201; 00:49 cut0 1390->279;
ZF702 32799->1187. Visual and deployment acceptance are separately required.

Review found and corrected an association boundary bug before release: a
protected gate, unavailable receiver measurement, or measured below-floor quiet
gate anywhere between the two anchors now blocks completion. Missing REF with
continuous measured SNR may be crossed, but the missing REF gate is never acted
on. Missing-SNR/protected regressions were observed red before this correction.

## Expanded acceptance fixes

Neighbouring ZF702 UTC00:02:15 exposed three larger evidence records still above
4 MiB after tables. A second lossless `xqc-record-table-zlib-v1` table encoding
uses zlib/base64, exact decoded length and SHA256. The reader bounds each decoded
table to64 MiB and rejects trailing/truncated streams. Module status/counts stay
plain JSON; no scientific records are truncated. The deployed byte cap is unchanged.

An additional long source ray (61.84 degrees, cut6) was fully vetoed by the local
CF weather proxy (657 remaining gates), despite stationary measured receiver
power along15–150km. New opt-in `radial_source_local_policy=joint_evidence` permits
independently fitted source evidence to resolve that endogenous proxy; default
`protect` remains unchanged. Independent WEATHER_PROTECTED/MIXED masks still
veto and cross-cut weather conflicts retain their existing policy. Conflict gate
counts are recorded. This is not a declaration of verified precipitation truth.

The same cut proposed87940/128665 gates (68.348%) before noise censor, including
86786 receiver-source gates. A station-specific candidate budget0.70 for ZF702
passed replay for cuts2/4/5/6 with joint evidence: all EVALUATED; the global default and budget mechanism are unchanged. This budget accommodates the reviewed candidate scene, not a claim of false-positive safety.

Final local regression: 165 XQC/multiband tests passed. Default-protect, independent hard protection and joint-policy range-varying weather controls passed. Joint-policy real controls preserve all828 ZF101 strong gates and produce zero radial-source detections across all9 ZF701 UTC03:26 cuts. First deployed acceptance covered7 volumes/63 cuts, revealing3 evidence abstentions and1 budget abstention; the follow-up release must recheck these before completion.

## 105 release acceptance (2026-09-29)

Code `4ed11c3` committed/pushed before deployment. Worker images
`rainpulse-cpu-worker:x-residual-4ed11c3-mb` / `-qc`, network release
`sx-xqc-residual-4ed11c3`, network SHA256
`6ab65a7217caa682568a1a505bec2be97bd743ff61551a8052e2ea64eab61208`.
Two multiband workers healthy; S worker images unchanged. Initial candidate
network validation correctly rejected an overlength release name before service
mutation; shortened version validated and deployed successfully.

Runs `d8f09b09-06c4-4dbc-8cce-7aff36106591`,
`050365ee-0b53-4783-ab0a-f328a7700588`,
`e965eed3-c196-429e-826e-d04d32f1902c`: all7 tasks SUCCEEDED.
All63 native cuts EVALUATED, all raw DBZH SHA256 checks equal original,
all source masks applied as rejected/NaN and QPE eligibility remains false.
Earlier3 evidence and1 action-budget abstentions are resolved. Local-proxy
conflicts and continuous/block/fan kind counts remain available per cut.

Real browser verified both ZF70108:07 cuts0/1, ZF70108:49 cut0,
ZF70208:08 cut0, and ZF70208:02 cut6 (4.29 degrees). Reported strong southern
residuals, NW spokes, broad southern fan and high-cut NE long ray were removed;
near-site and separate compact echoes remain. This is bounded sample acceptance,
not proof that every historical scene is error-free.

Latest-result links (omit an old pinned result ID):
- [ZF70108:07](http://192.168.28.105:4173/?preset=qc&band=X&mode=single&date=2026-08-28&station=zf701&scan=93d2d253-7dc8-55fd-b428-c1d8b76ad8ef&sweep=1)
- [ZF70208:08](http://192.168.28.105:4173/?preset=qc&band=X&mode=single&date=2026-08-28&station=zf702&scan=d42663f7-67de-5f8a-901a-7c9163a1d1d2&sweep=0)

Scoped165 tests passed; touched algorithm/test modules passed targeted lint.
Repository-wide CI36541524613 is not green: existing broad lint/contract and
legacy S parameter-hash checks fail; do not represent the whole repository as
passing. No unrelated UI dirt was committed.

MinIO actual volume available133699678208 bytes (~124.5GiB),101811522 inodes
at acceptance. With120GiB guard, no whole-day backfill or composite regeneration
was launched. Historical un-recomputed scans/composites retain old immutable
versions. Next mainline step is capacity-safe wider replay and downstream
candidate composite regeneration, retaining precipitation controls. Hourly
follow-up remains paused and trusted fusion/QPE/forecast are not enabled.

## Wider replay follow-up (2026-09-29)

User authorized clearing only `/home/yons/hwapp/dis/rainpulse-pip-cache-relocated` pip download cache. Its known http/http-v2/selfcheck/wheels children were removed; directory retained. Freed15342678016 bytes, post-cleanup available148859305984 bytes. No radar assets or model files touched.

Wider runs32762e58-e162-4ac3-8b9d-9c2863622e91,25086666-384b-466e-b3ca-34fc37a222c7,8e401680-93fc-45de-80a4-cc091afdde4a completed6 volumes54 cuts near UTC01/02/03. All EVALUATED, raw hashes unchanged, source masks applied, QPE false. Total checked13 volumes117 cuts. ZF70108:57 and ZF70209:59 weak-echo browser controls retain near-site echoes.

Full-network UTC00:12 composite run7806b948-b29c-4b2f-8a5d-37d930ec0cc5 has25 frozen available sources; ZF402 absent/expired, ZF703/ZF801 excluded. Completed in1676865ms (~28min), output6088072 bytes. Numerical audit passed full1077x1238 shape, SHA256, S+X==fmax(S,X) including NaNs, valid source indices and nonnegative ages (maximum587.572s). Valid echo cells S53632/X16965/S+X61119. Browser verified S-only, X-only and S/S+X comparison at08:12. This is pipeline and sampled QC acceptance, not a claim that all other stations are artifact-free. Hourly automation remains paused.

### New unresolved full-network finding

Browser X/S+X maps reveal southwest spokes dominated by ZF505, traced through WINNER_SOURCE/WINNER_SWEEP_NUMBER. Diagnostic normal x_qc run a10b2832-9d0d-4d28-9bc6-0ef975e41eb0, scan0b5c41f5-978a-5594-8a3b-88f3f70e746d (UTC00:06:31):8 cuts EVALUATED, cut4 ACTION_BUDGET_ABSTAINED with26270 source gates not applied. Raw hashes unchanged. Thus numerical composite consistency passes but artifact-removal acceptance is NOT complete. Next fix must review this cut's proposed exclusion fraction and precipitation controls, and prevent unexecuted QC from being mistaken for accepted clean composite input; do not blindly raise all budgets. Evidence .build/residual-zf505-audit.jsonl and .build/trace-composite-residual.jsonl.
