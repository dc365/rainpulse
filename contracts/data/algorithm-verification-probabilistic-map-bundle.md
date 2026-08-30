# Algorithm verification probabilistic map bundle 1.0

This presentation-only bundle adds spatial evidence to a frozen probabilistic
algorithm-verification run. It does not change the score arrays, calibration
status, model gate, or product-publication state of that run.

## Identity and layout

- One immutable bundle is written for each `(profile_version, case_id,
  issue_time_utc)` tuple below `maps/<case_id>/<issue_key>/`.
- `manifest.json` uses the existing algorithm-verification map contract 1.0 and
  every PNG is content-addressed by SHA-256 in that manifest.
- `maps/index.json` lists every committed bundle and is written only after all
  selected issues have completed.
- The grid is EPSG:4326 and carries both centre-fit and pixel-edge bounds. PNG
  rows are north-up; transparent cells mean missing or outside coverage, never
  zero rainfall.

## Rate layers

Each valid time contains the following common-palette rain-rate layers:

1. observed MRMS truth;
2. NowcastNet ensemble-mean rain rate;
3. STEPS ensemble-mean rain rate;
4. LK deterministic forecast;
5. persistence forecast;
6. independent phase-correlation forecast.

NowcastNet and STEPS ensemble means are valid only where every frozen member is
valid. The layer names remain `nowcastnet` and `steps`; the Web presentation
must label both as ensemble means. These layers are not exceedance-probability
maps and must not be described as calibrated probabilities.

## Safety boundary

- `operational_eligible` is always `false`.
- `product_publication_enabled` remains `false` in the parent verification
  summary.
- Rendering must not mutate or reuse an already published immutable run
  directory with different bytes.
- MRMS evidence cannot satisfy Fujian radar/QC/QPE acceptance.
