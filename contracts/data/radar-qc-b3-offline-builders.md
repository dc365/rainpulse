# Radar QC B3 offline builders

Run with Python 3.11+ from the repository root. No command uploads data or changes
active profiles. Outputs refuse to overwrite existing artifacts. All `passed`
results describe engineering comparisons only (`operational_eligible=false`).

```bash
python scripts/radar_qc_b3.py labels --input label-input.json --output labels.json \
  --frozen-config-sha256 HASH --frozen-code-revision REVISION
python scripts/radar_qc_b3.py clutter --input clutter-input.json --output clutter-artifact
python scripts/radar_qc_b3.py promotion --input metrics.json --label-manifest labels.json \
  --profile configs/verification/fujian-qc-promotion-v1.yaml --output promotion.json
```

## Labels

Input JSON has an `entries` array. Each entry contains the existing label-manifest
scan metadata: process_id, partition (development or holdout), case_category,
radar_id, scan_id, volume_end_time_utc, input_uri, annotator, label_source,
label_version, review_status, and optional temporal_context/cross_radar_context.
Replace in-memory `label_values` with `label_npz` and `label_sha256`. NPZ must
contain `label_values` with values 0 (meteorological), 1 (nonmeteorological), or
-1 (unknown). Paths resolve against the input JSON. SHA-256 is verified before
reading arrays; output retains the label artifact URI/hash and input JSON hash.

All scans of a process must use one partition. Every context scan must also be
represented in this manifest with the same partition and matching radar/input
identity. Context-only scans can use unknown labels; they are not invented truth.
A holdout requires frozen configuration and code references. This reference does
not prove an external reviewer approved the split; that remains independent
acceptance evidence.

## Static clutter

Input JSON has a `samples` array. Each sample supplies radar_id, sweep_name,
elevation_deg, case_category=`clear_sky`, observed_at_utc (timezone required),
source_uri identifying independent clear-sky evidence, npz_path, and sha256.
NPZ has dbzh [ray, gate], azimuth_deg [ray], range_m [gate]. A build accepts one
radar only; each sweep has one elevation and consistent original geometry.
Duplicate radar/sweep/normalized UTC observation timestamps are rejected, so
repeating the same scan cannot inflate support. The prior remains unavailable until 20 clear-sky days and 200 valid gate
observations. It never produces a hard clutter flag.

Output directory is atomically published with prior.npz (legacy ancillary field
keys), support.npz (sweep keys), and manifest.json including station, elevation,
geometry coordinate hashes/counts, day counts, source hashes, support thresholds,
and both output NPZ hashes. Only prior.npz is a candidate ancillary input; no CLI
installs it into active QC profiles.

## Promotion

Input JSON has frozen_config_sha256, frozen_code_revision, and `rows`. A row has
process_id, partition, case_category, subset, and paired candidate/reference
metrics: anomaly_precision, anomaly_recall, meteorological_retention, qpe_rmse
(suffix `_candidate`/`_reference`). These must be independently computed from the
frozen labels and paired observations; CLI does not infer missing metrics.
Input freeze references must match the label manifest; each row must match a
process/partition/category represented by labels. Only holdout enters gates;
development is excluded and counted separately. A process cannot cross splits.
`overall`, every required category, and `strong_echo_ge_40_dbz` are reported even
if empty. Missing evidence gives insufficient_data. Critical subsets require at
least three independent processes. Report retains metric, label-manifest and
promotion-profile SHA-256, exact frozen code/config references, process counts,
confidence intervals and thresholds. Passing these proposed engineering gates
cannot replace representative real-data and business approval.

Exit codes: 0 completed artifact (or passed engineering comparison), 2 invalid
input/configuration or existing output, 3 insufficient promotion evidence,
4 failed promotion gates. Label/clutter outputs may legitimately contain
insufficient-data or unavailable gates; inspect the artifact before use.
