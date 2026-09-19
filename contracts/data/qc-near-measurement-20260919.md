# Near-station measurement review v1

Base: fbfa0d5a0fe09a9bca2ed87f164bbe3856f9195f. Nested opt-in
`volume_review.near_measurement`; absent means byte-equivalent historical config
serialization and unchanged behaviour. No production promotion or defaults changed.

## Evidence

Original ray/gate geometry, RAW DBZH/SNR/ZDR/RHOHV/PHIDP plus explicit per-moment
availability when supplied by NativeSweep. The replay fallback uses only finite,
physically bounded RAW values and explicitly reports the unavailable original
per-moment masks. Missing is not no-rain. Targets >=30 dBZ, valid no-rain <-10 dBZ,
previous weather/mixed and current weather-compatible seeds are protected. A
geometry stencil that intersects angular gaps, duplicate/bad rays or range ends
abstains. Same 2–75 km slant-range domain is used at ALL measured elevations; it
is not a fixed geographic deletion mask. No background or future observations
are used by this stage. Parameters are development hypotheses, not standards.

NMR_NONMET_CANDIDATE_MASK: reliable target polarization + DR >= -12 dB,
at least six actual polar samples with >=60% compatible neighbours in a
3-ray x ~2km window. Neighbourhood agreement is not independent evidence.
NMR_LOW_SNR_UNCERTAIN_MASK: weak observed target, SNR below selected 3/6/8/10 dB
threshold and limited weather-compatible neighbourhood. This is NOT a clutter
label. Missing SNR does not satisfy a numeric low-SNR test. |ZDR| >=7.5 dB is
withheld from DR evidence, not asserted to be a proven vendor saturation code.

## Separate dispositions

Modes: audit / experiment. Nonmet: diagnostic_only / cr_withhold / quarantine.
Uncertainty: diagnostic_only / cr_withhold. Default mode audit. No confirmations.
Quarantine is limited to nonmet candidates with baseline reflectivity trust;
uses a separate NMR_QUARANTINE_MASK, DOWNWEIGHT, LOW_QUALITY, capped QI, all moment
trust masks off and QPE ineligible; original DBZH and VALID are immutable. Derived
phase/KDP/attenuation fields over the original affected trusted run are invalidated.
Low-SNR uncertainty never changes QPE, QC_ACTION, raw data or moment trust.
All CR candidates act BEFORE maximum selection; baseline CR may only decrease.
CR_UNCERTAIN_MASK retains distinct low-reliability/nonmet masks and original risk.
Separate counts report candidates, effective CR loss, effective QPE loss, no-rain
changes (zero), and nonmet confirmation (zero). Budget overflow retains isolation
and demands manual review, never claims acceptance.

NMR_BEFORE_* preserve the exact pre-stage values for every changed array. Validation
restores this view and runs the unchanged VOR/NP/OC1/V7 validators FIRST, then checks
exact delta against evidence/config. No weakening of original validators. Serialized
configuration, digest, library backend/version, raw content digest, and actual
per-moment availability are written with QC evidence. Repeat application is rejected.

## Libraries and replay

wradlib.dp.depolarization(ZDR_dB, RHOHV) is the production backend (2.9.5).
An explicitly selected numpy_reference backend implements the same formula for
offline comparison; it has a DIFFERENT configuration identity. No silent fallback.
Optional Py-ART 2.2.5 GateFilter check uses the actual native radar and
exclude_gates(..., op='or') to prove final CR exclusion equals the mask policy.
It is NOT a new meteorological classifier. Existing Py-ART/wradlib QC remains intact.
Selected missing or wrong-version libraries fail with a clear error.

Sites with mixed NMR mode/config or missing evidence fail CR composition. The P3
maximum keeps actual winner/runner-up provenance. The strict near policy does not
reinterpret QPE zeros or make unavailable coverage transparent without risk output.
Tests and local replays are not independent truth or a full operational rerun.
