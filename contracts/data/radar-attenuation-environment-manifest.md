# Radar attenuation environment manifest 1.0

Offline `attenuation_audit --environment-manifest PATH` accepts a separate manifest
following `configs/schemas/radar-attenuation-environment-manifest.schema.json`.
Each record binds an explicit source URI and NPZ SHA-256 to one radar, scan, and
exact UTC volume end time. Relative NPZ paths resolve against the manifest directory.
The producer must collocate the external temperature and verified DEM blockage
onto this scan; the adapter does not invent weather observations or zeros.

The NPZ contains `temperature_c`, a finite scalar representative temperature in
Celsius explicitly supplied by the source for this scan, and
`<sweep_name>__blockage_fraction`, arrays on the original [ray, gate] geometry.
A scalar temperature is suitable only when the source certifies it represents
the sampled liquid precipitation domain; spatially variable temperature requires
a separately validated gate-level adapter, and is not supported by this contract.
Missing temperature prevents selecting frozen table coefficients. A temperature
outside the frozen applicability interval fails the scan. Missing blockage gives
unavailable correction; NaN and excessive blockage keep gates unavailable.
Source identity/time/hash mismatches fail the scan. No data source is implied by
merely providing a coefficient table. Output artifacts retain environment source,
hash, timestamp, radar/scan identity and supplied temperature.

This is shadow evidence only. The environment manifest does not enable physics
profiles, establish coefficient quality, or grant operational eligibility.
