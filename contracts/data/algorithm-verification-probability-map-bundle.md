# Algorithm verification probability map bundle

Status: RP-033 offline engineering evidence contract.

The bundle exposes threshold-exceedance probability fields derived from the frozen
RP-026 ensemble members. It is separate from the RP-032 ensemble-mean rain-rate
bundle so that rain rate (`mm/h`) and probability (`%`) cannot share a palette or be
mistaken for one another.

## Layout

```text
probability-maps/
  index.json
  <case_id>/<YYYYMMDDThhmmssZ>/
    manifest.json
    layers/lead-<minutes>-threshold-<mm_h>-{truth|nowcastnet|steps}.png
```

Every lead and threshold has exactly three north-up EPSG:4326 PNG layers:

- observed threshold exceedance, encoded as 0% or 100%;
- raw NowcastNet ensemble-member exceedance frequency;
- raw STEPS ensemble-member exceedance frequency.

Valid zero probability uses the configured pale no-event color. Missing truth or
any missing member in a forecast ensemble is transparent and is never interpreted
as 0%.

## Frozen boundary

- `calibration_status` is always
  `raw_ensemble_relative_frequency_uncalibrated`;
- `operational_eligible` is always `false`;
- `product_publication_enabled` is always `false`;
- probability rendering does not modify RP-026 score arrays or RP-032 rain-rate
  maps;
- MRMS evidence does not establish Fujian radar-QC, QPE, mosaic, or operational
  readiness.

The manifest binds case, issue, lead, threshold, grid, palette, layer identity,
dimensions, byte size, and SHA-256. Staging verifies the complete immutable bundle
before atomically exposing a run to the control plane.
