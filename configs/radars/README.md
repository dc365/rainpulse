# Radar inventory and configuration

Copy `radar-inventory-template.yaml` once for each physical radar and replace
the placeholder `radar_id` with its registered identifier. Keep the file in
`draft` while any site, format, geometry, field, unit, ancillary-data, or QC
value is unknown. `draft` and `disabled` files are inventory records only and
must never trigger operational ingest.

Change `lifecycle` to `ready` only after the configuration validates against
`../schemas/radar-config.schema.json` and its values have been verified against
radar documentation plus a representative full-volume sample. The ready-state
rules require site geometry, radar band, beam width, scan geometry, a DBZH
mapping, source format and delivery, UTC timestamps, DEM/coastline versions,
and versioned QC configuration. RainPulse does not infer any of these values.

Field mappings use canonical names (`DBZH`, `ZDR`, `RHOHV`, `PHIDP`, `VR`,
`SW`, `SNR`) while retaining the source name, unit, missing value, scale, and
offset. A missing optional field is omitted from the mapping; it is not
synthesized by the decoder or QC pipeline.
