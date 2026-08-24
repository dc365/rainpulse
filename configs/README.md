# Runtime configuration

Versioned radar, grid, source, model, product, threshold, and quality-control
configuration belongs here. Region-specific values must not be hard-coded in
service or worker code.

- `schemas/radar-config.schema.json` is the source of truth for per-radar inventory.
- `radars/radar-inventory-template.yaml` records unknown values explicitly and
  cannot become operational until it satisfies all `ready` requirements.
- `qc/flag-definitions.yaml` freezes the uint32 QC flag bit assignments.
- `qc/profiles/` will hold versioned coastal, mountain, and strong-weather
  parameters after representative data and thresholds are verified.

Real station coordinates, formats, scan strategies, field mappings, QC
thresholds, DEM versions, and target grids must be supplied and verified; they
must never be inferred from the template.
