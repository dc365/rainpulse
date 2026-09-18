# QC full-range composite reflectivity

When `grid_render.full_range_reflectivity` is enabled, `grid-dbzh-qc`
is a diagnostic composite: maximum eligible DBZH_QC over elevations and
participating radars, on a WGS84 grid covering their scan footprints.
It is not the QI-weighted Hybrid Scan/QPE input. QPE and forecast grids stay
unchanged. Reference scans excluded from the analysis do not contribute.

The layer publishes its own pixel-edge bounds, resolution, aggregation and
QC input identities. Invalid, rejected and quarantined gates remain absent;
angular/range gaps are not filled. No-data pixels remain transparent.
The frontend uses the layer bounds and its numeric legend for pixel inspection.
The initial implementation targets regional networks not crossing the dateline.

Enable with `configs/diagnostics/qc-full-range-diagnostics-v7.yaml` on both
the control plane (`RAINPULSE_PIPELINE_DIAGNOSTIC_CONFIG`) and diagnostic
worker (the `deploy/docker-compose.diagnostics-full-range.yaml` overlay).
The worker loads site coordinates from `RAINPULSE_RADAR_CONFIG_DIR`, since
existing QC artifacts do not retain normalized-volume site attributes.
Rebuild the diagnostic worker image, rebuild the web frontend, then regenerate
diagnostics from existing QC volumes. QPE/forecast computation is not required.

Validation: `PYTHONPATH=algorithms algorithms/.venv/bin/python -m pytest
algorithms/tests/test_diagnostic_composite.py algorithms/tests/test_diagnostics.py`.
