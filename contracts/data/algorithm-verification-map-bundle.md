# AlgorithmVerificationMapBundle contract

`AlgorithmVerificationMapBundle contract_version=1.0` is an optional,
presentation-only artifact attached to an offline algorithm-verification run.
It never participates in deterministic scoring and may not be used as a
scientific input.

For every completed issue the verifier renders one north-up, georeferenced PNG
for observed truth and one for each deterministic forecast model at every
scored lead. The current RP016 profile contains 12 observed ten-minute leads
and four layers per lead: truth, LK, persistence and whole-field translation.

## Grid and state semantics

- Arrays are regular `lat × lon`, with latitude south-to-north and longitude
  west-to-east. PNG rows are flipped north-up.
- The manifest records `EPSG:4326` pixel-edge bounds. The browser must never
  infer bounds from filenames, image dimensions or case names.
- Missing and no-coverage cells remain invalid and have alpha 0.
- Valid no-rain cells remain visible as a low-alpha coverage film. They must not
  become indistinguishable from missing data.
- Rain cells use the versioned RainPulse rain-rate palette. Truth and every
  forecast use the same palette and opacity.
- Every layer records valid, no-rain, rain and missing cell counts, SHA-256,
  byte size, dimensions, model role, lead and valid time.

## Motion evidence

The issue manifest may include at most 200 decimated LK motion vectors. Vectors
are presentation diagnostics in grid cells per five-minute algorithm step, not
calibrated wind observations. A motion fallback records its reason and may have
no vectors.

## Layout and atomic publication

```text
maps/
├── index.json
└── {case_id}/{issue_time_compact}/
    ├── manifest.json
    └── layers/{asset_id}.png
```

Each issue is written below a temporary directory, validated, and renamed into
place. `manifest.json` is written last inside the issue. The run-level
`index.json` is written only after all completed issue bundles are available.
Incomplete temporary directories are never exposed by the API.

Go serves only asset IDs listed in a validated issue manifest. It rejects path
traversal, files larger than the verification-image limit, SHA or PNG signature
drift, and identity mismatches. React receives manifests and image URLs only;
it never receives full meteorological arrays.
