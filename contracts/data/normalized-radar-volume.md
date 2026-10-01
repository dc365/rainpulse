# NormalizedRadarVolume Zarr contract

`NormalizedRadarVolume` is the decoder output for one physical radar volume.
It preserves the original polar sampling geometry and canonicalizes field
names, units, scale, missing values, and timestamps. It does not perform
meteorological QC.

## Logical dimensions and coordinates

The logical dimensions are `sweep`, `ray`, and `gate`. Rays from all sweeps are
stored on one `ray` dimension; sweep start/end indices identify each contiguous
subset without padding or silently resampling the source scan.

| Coordinate | Dimensions | Dtype | Requirements |
|---|---|---|---|
| `azimuth` | `[ray]` | float32 | Degrees clockwise from true north, normalized to `[0, 360)` |
| `elevation` | `[ray]` | float32 | Degrees above the local horizontal |
| `ray_time` | `[ray]` | datetime64[ns] | UTC and non-decreasing within each sweep |
| `range` | `[gate]` | float32 | Gate-centre slant range in metres, strictly increasing |
| `sweep_number` | `[sweep]` | int16 | Unique and increasing |
| `sweep_start_ray_index` | `[sweep]` | int32 | Inclusive ray index |
| `sweep_end_ray_index` | `[sweep]` | int32 | Inclusive ray index, not before start |

If source sweeps use incompatible gate spacing or gate counts, the adapter must
preserve them in separate versioned Zarr sweep groups rather than invent a
common geometry. The selected encoding is recorded in `geometry_encoding`.

RP-006 uses Zarr v2 with `geometry_encoding=sweep_groups_v1`. Root arrays hold
the conceptual sweep number and inclusive flattened ray boundaries. Each
`sweep_NNN` group contains its own `azimuth`, `elevation`, `ray_time`, `range`
and available canonical `[ray, gate]` fields. This preserves the Z9598 VCP21D
11-cut layout, including the separate 0.5° and 1.5° waveform/moment cuts and
the different gate counts at higher elevations.

## Canonical fields

Each available moment is `[ray, gate]` `float32` with `NaN` for source missing
values. A matching `[ray, gate]` `uint32` `<FIELD>_RAW_CODE` array preserves
every source integer gate code before scale/offset decoding. Only fields present
and verified in the radar configuration are written.

| Variable | Canonical unit | Required |
|---|---|---|
| `DBZH` | dBZ | yes for a ready Phase 1 radar |
| `ZDR` | dB | optional |
| `RHOHV` | 1 | optional |
| `PHIDP` | degree | optional |
| `VR` | m s-1 | optional |
| `SW` | m s-1 | optional |
| `SNR` | dB | optional |

No decoder may synthesize an absent optional field. Source codes `0..4` are
preserved losslessly in `<FIELD>_RAW_CODE`; they still decode to `NaN` until a
vendor-verified semantic table is registered. `4294967295` is reserved only for
a moment absent from an entire source radial and is declared as
`absent_moment_code`. Scale, offset, source bin length, reserved-code status,
and source units remain recorded in field metadata. This keeps unknown/no-echo/
folded/reserved states distinguishable for later QC without inventing their
meaning.

## Required attributes

`contract_name=rainpulse.normalized-radar-volume`, `contract_version=1.1`,
`asset_id`, `radar_id`, `radar_config_version`, `decoder_id`,
`decoder_version`, `source_format`, `source_format_version`,
`field_mapping_version`, `geometry_encoding`, station longitude/latitude/
altitude and altitude datum, radar band, scan strategy, volume start/end UTC,
and the input SHA-256.

For RSTM generic type 16, `source_generic_type=16` and each sweep records the
source transmit beam index and receive beam widths from its cut table. The
original site frequency number is `frequency_header_raw`; `frequency_mhz` is
null until the device-family unit is verified. Draft X assets may therefore be
decoded and previewed but are ineligible for spatial fusion or operational QC.
Generic type 1 continues to use the existing site frequency in MHz.

## Validation and publication

- Geometry, units, field ranges, sweep boundaries, and time coverage are
  validated before publication.
- Source missing values become `NaN`, never zero.
- The publisher writes content-addressed objects below `_objects/{sha256}`,
  validates the complete Zarr hierarchy, and conditionally creates
  `_SUCCESS.json` last. Concurrent duplicate workers reuse the first committed
  marker instead of overwriting its artifact.
- Output is stored at
  `radar/normalized/{radar_id}/{yyyy}/{mm}/{dd}/{scan_time}/volume.zarr`.
# Native beam evidence

Optional root attribute `clutter_context_contract` may carry a
`native-site-beam-v1` record: `beam_width_deg`, `beam_source`, `input_sha256`,
and `radar_config_version`. For regular FMT this is the finite vertical beam
width in the checked site header, bounded to (0,3] degrees and tied to the
archived raw input hash. A missing or malformed header does not inherit a
draft hardware width. Phased-array receiver/transmitter widths do not imply
an equivalent site-wide contract. Legacy objects without this attribute remain
readable but provide no action-grade upper-beam verification through it.

## Native cut sampling evidence

Each newly decoded `sweep_NNN` may carry `native_cut_sampling` with version
`native-cut-sampling-v1`. It preserves the source cut number, process-mode and
waveform integer codes, PRF1/PRF2 in Hz, log/Doppler gate spacing in metres,
and source Nyquist velocity in m/s. The record also binds the raw input SHA-256
and decoder configuration version. Negative sentinel values remain recorded;
opaque waveform codes are not interpreted as a verified Doppler scheme.

The record has `semantic_verification=false`. It does not assert calibration,
Doppler action eligibility, weather classification or source contamination.
Adapters retain these per-cut records in `Volume.metadata.native_cut_sampling`,
keyed by the normalized sweep number, in eager and streaming paths alike.
Present records with mismatched raw/config/cut identity are rejected. Legacy
assets without the record stay readable and never inherit invented PRFs or a
root-level waveform as a replacement for missing per-cut source parameters.

For generic type 1, the same optional record also retains
`dealiasing_mode_code`, `sample_count1`, `sample_count2`, `phase_mode_code`, and
`atmospheric_loss_db_per_km` from the packed cut header (QX/T 653—2022, table 6).
The first four are opaque integers; the last is a finite source measurement.
Sentinels and out-of-standard enum values are preserved for diagnosis, without
being promoted to known processing modes. Adapters reject malformed present
values. Legacy sampling records may omit this extension. Type 16 does not
inherit type 1 byte offsets or synthesize these fields without verified layout.
