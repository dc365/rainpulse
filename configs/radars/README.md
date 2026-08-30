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

`z9598.yaml` is the first real-sample configuration. Values copied from the
RSTM 2.0 site/task/cut headers are populated, while unverified station datum,
hardware model, calibration and ancillary/QC versions remain explicit nulls.
It intentionally stays `draft`: it is valid for RP-006 replay/golden-sample
decoding but not eligible for operational ingest.

The NAS directory currently replays historical payloads beneath newer
filenames. The RSTM task/radial UTC timestamps and compressed-byte SHA-256 are
authoritative; filename time is discovery metadata only.

`fujian-20260828/` freezes the four RSTM 2.0 headers found in the 2026-08-28
Fujian test inventory for Z9591, Z9593, Z9598 and Z9599. These configurations
remain `draft`. They support an explicitly historical engineering replay, but
must not activate continuous ingest or make the resulting analysis
operationally eligible. The paired `DPCTEST` files are excluded from the first
replay until the data provider confirms whether they are derived duplicates or
a distinct authoritative stream.
