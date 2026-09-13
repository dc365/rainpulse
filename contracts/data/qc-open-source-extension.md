# QC radar volume: open-source engine extension v1

The existing `rainpulse.qc-radar-volume` geometry encoding and base contract 1.0 remain readable. New products declare `qc_engine=open_source`, `qc_engine_contract_version=1.0`, `flag_definition_version=qc-flags-v2`, `operational_eligible=false`, the library versions and `qc_parameters_sha256`. Their additional fields are mandatory for this engine and validated by `qc_engine.validation.validate_sweep`.

| Field | dtype | Meaning |
| --- | --- | --- |
| QC_ACTION | uint8 | 0 KEEP; 1 DOWNWEIGHT; 2 REJECT; 3 original MISSING |
| QC_DECISION_REASON | uint16 | Versioned flags from `DecisionReason`, not `QC_FLAGS` bits |
| METEO_SCORE | float32 | Uncalibrated wradlib meteorological membership; NaN where unavailable |
| METEO_SCORE_AVAILABLE_MASK | uint8 | Exact availability, not derived from a substituted zero |
| RFI_CANDIDATE_MASK | uint8 | Local RFI structural candidate, not a final rejection |
| REFLECTIVITY_TRUST_MASK | uint8 | Original valid observation AND not rejected |
| QPE_ELIGIBLE_MASK | uint8 | Trusted AND finite quality above absolute threshold, before later geometry gates |
| DBZH_USABLE | float32 | Original trusted/eligible DBZH; NaN elsewhere; no interpolation repair |
| {moment}_RAW | float32 | Original optional moment, unmodified |
| {moment}_TRUST_MASK | uint8 | Per-field support for PHIDP, RHOHV, ZDR, VR, SW, SNR |
| WEATHER_SUPPORTED_MASK | uint8 | Weather support can coexist with rejected local measurement |
| KDP_OS | float32 | Vulpiani KDP on trusted segments only |
| KDP_OS_AVAILABLE_MASK | uint8 | No gap or rejected segment bridging |

`VALID_MASK` continues to describe original finite, in-range reflectivity observations, not final eligibility. All rejected gates must carry `NON_METEOROLOGICAL` bit 15; more specific independently supported reasons can also be present. Existing bits 0–14 are immutable. Unknown flag-definition versions fail explicitly.

Native coordinates/indices/cuts remain unchanged in serialized products. Algorithms may use a sorted private view but restore original order. Evidence provenance includes function, actual library version, explicit parameters and input fields, availability/candidate counts and score semantics. Wall-clock performance is recorded in worker observability or review reports, not inserted into immutable QC products, so duplicate tasks produce identical bytes. The task event's `occurred_at` supplies the deterministic creation time.

New-engine `radar-qc-requested` payloads require `qc_profile_sha256`, checked against the exact mounted YAML bytes. Job identity includes the input URI, config hash and ordered context manifest; object paths include the resulting job identity. Worker content fingerprints additionally include input/context content, assets and dependency versions. New-engine results never silently masquerade as legacy success.

RadarGrid propagates engine, parameter digest and library versions. New-engine mosaic requires equal pipeline/parameter/library identities; missing or mixed identities are rejected. Therefore old and new QC, or fusion and experimental-RFI candidates, cannot silently coexist in the same analysis. Candidate operational eligibility remains false through Hybrid/mosaic/QPE.

## RFI object engine v2 (explicit candidate only)

`qc-opensource-2.0.0` uses `rfi-objects-v2`. The additive per-gate fields and reason
bits are specified in `docs/radar-qc-opensource/RFI_OBJECTS_V2.md`.
`RFI_QUARANTINE_MASK=1` requires `QC_ACTION=DOWNWEIGHT`, forbids confirmed
`NON_METEOROLOGICAL` solely for quarantine, and forces all measurement-trust and
QPE-eligibility masks to zero. The v2 trust invariant is therefore
`observed AND NOT (REJECT OR quarantine)`, while v1 retains its previous invariant.
Missing is never converted to quarantine or no-rain. Review reports distinguish
confirmed rejection, quarantine and quantitative coverage using the SAME original
observations/labels. Temporal votes have per-gate counts; unavailable context is not
negative evidence and duplicate physical scans must not supply multiple votes.
Object IDs are local, deterministic within one native sweep, not persistent tracks.
