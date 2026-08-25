# Runtime configuration

Versioned radar, grid, source, model, product, threshold, and quality-control
configuration belongs here. Region-specific values must not be hard-coded in
service or worker code.

- `schemas/radar-config.schema.json` is the source of truth for per-radar inventory.
- `schemas/grid-config.schema.json` freezes regular target-grid coordinates,
  registration, shape and coordinate digest.
- `grids/fuzhou-0p01deg-v1.yaml` is the Phase 1 EPSG:4326 application grid:
  `118–123°E`, `25–27°N`, `0.01°`, `501 × 201` inclusive point centres.
- `schemas/ancillary-source.schema.json` and
  `ancillary/fujian-taiwan-v1.yaml` freeze DEM/coastline source provenance for
  `114–127°E`, `21–29°N`.
- `schemas/radar-grid-profile.schema.json` and
  `gridding/rp009-hybrid-v1.yaml` freeze beam geometry, DEM blockage, direct
  polar mapping and lowest-usable-elevation Hybrid Scan rules.
- `radars/radar-inventory-template.yaml` records unknown values explicitly and
  cannot become operational until it satisfies all `ready` requirements.
- `qc/flag-definitions.yaml` freezes the uint32 QC flag bit assignments.
- `qc/profiles/` will hold versioned coastal, mountain, and strong-weather
  parameters after representative data and thresholds are verified.

Real station coordinates, formats, scan strategies, field mappings, QC
thresholds, DEM versions, and target grids must be supplied and verified; they
must never be inferred from the template.

Large ancillary files are runtime assets and are not committed. Prepare them
with `make ancillary-plan`, `make ancillary-download`, and
`make ancillary-verify`. The runtime manifest records every planned DEM tile,
source-side ocean absence, file SHA-256, coastline archive identity and final
geospatial acceptance result.
