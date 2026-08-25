# Meteorological data contracts

The v1.1 chain is contract-first and quality control precedes every gridding or
nowcast operation:

1. [`raw-radar-asset.md`](raw-radar-asset.md) registers immutable source bytes.
2. [`normalized-radar-volume.md`](normalized-radar-volume.md) defines decoded
   canonical moments on original polar geometry.
3. [`qc-radar-volume.md`](qc-radar-volume.md) defines polar QC fields, flags,
   quality components, and module provenance.
4. [`radar-grid.md`](radar-grid.md) defines one radar's Hybrid Scan grid.
5. [`radar-analysis.md`](radar-analysis.md) defines the quality-aware multi-radar
   reflectivity/QPE analysis.
6. [`nowcast-input.md`](nowcast-input.md) defines the fixed-step sequence passed
   to model workers.
7. [`forecast-output.md`](forecast-output.md) defines model output consumed by
   product generation and verification.
8. [`application-rainfall-netcdf.md`](application-rainfall-netcdf.md) defines
   the two-dimensional business exchange file derived at the product boundary.
9. [`rendered-rainfall-layer.md`](rendered-rainfall-layer.md) defines the
   transparent PNG and geospatial layer manifest consumed by the frontend.

Every stage preserves valid no-rain, missing, and low-quality states. Full
arrays live only in versioned object-store artifacts. Database, REST, and event
payloads carry identities, summaries, and object URIs.
