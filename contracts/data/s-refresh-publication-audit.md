# S background refresh publication audit

Read-only verification of an existing frozen `refresh_s_all_current.py` run.
It never submits jobs, changes services, deletes assets, or loads radar arrays.

Bind the exact plan bytes, refresh receipt, auditor source and accepted QC
profile digest. A scan passes only when its successful QC request produced the
recorded QC URI, the latest committed scan still names that URI, and its
successful grid request consumed that exact URI and produced the recorded grid.

A completed slot additionally requires exact station/scan/grid/QC membership
in mosaic and diagnostics requests, mosaic → QPE → diagnostics input lineage,
and the public cycle's current analysis and T0 frames to name that successful
diagnostics job. Check all published S raw/QC sweeps as paired frames, including
the same scan, sweep and timestamp; a planned missing site must remain missing.
Composite reflectivity and QPE T0 frames must also use the same diagnostics job.

Fetch each new public PNG with bounded bytes and verify PNG structure, chunk
checksums, image dimensions and bounded decompression. Record its SHA256; this
proves delivery of the named generation, not meteorological accuracy or visual
effectiveness. Previously verified immutable job URLs need not be fetched every
minute; recheck their public frame identities and every scan's database lineage.

Only `DONE` plus every planned scan and slot passing can yield `VERIFIED`.
Observation failures are retryable and never cause resubmission. Explicit
identity/lineage violations fail closed. State is saved atomically, guarded by
a separate nonblocking lock; the original refresh controller remains untouched.

Optional causal dependency scheduling adds no new scans or QC rules. Freeze a
positive maximum age and, for each slot/site, the latest registered native scan
whose volume end is no later than that slot and whose age is within that limit.
Future or stale volumes cannot substitute for a missing causal observation;
equal-time competing scans fail closed. Re-derive membership from the frozen
native inventory during plan validation. These prerequisites run before the
slot, including an already published slot, while existing per-scan receipt reuse
prevents duplicate QC/grid work. Existing Web frame membership is unchanged;
causal scheduling alone does not certify those legacy frames as causal.
Changing this scheduling requires a fresh plan and a documented handover that
preserves successful and in-flight job receipts; never edit the old frozen plan
or cancel running QC to change queue order.

## Supplemental station images

Default verification remains strict about the original diagnostics job. An
explicit `--allow-station-supplement <radar_id>` permits a replacement generation
only when its successful normal diagnostics job retains the original run,
analysis, rendering configuration, QPE input and every original contributor's
scan and QC URI. Its output has a separate, nonzero station-supplement revision.
Extra sites must be unique, registered S stations with current native QC under
the frozen profile, bound to the successful QC job and immutable normalized
input. Their volume end must precede the analysis by no more than 720 seconds.

Verify paired RAW/QC images at every published extra sweep and require all
selected frames to use the same generation. Original contributor, composite
and QPE PNG receipts must remain byte-identical to their baseline generation.
Record both generations, the supplemental input lineage and image receipts;
extra images do not change the frozen scan/slot completion totals. This does
not certify a supplemental station as a composite or QPE contributor.

Changing auditor source or allowed stations requires a new output identity and
observer. Retain the earlier failure receipt and leave the recompute controller
and in-flight jobs untouched. For example, from the deployed project root:

```sh
python3 scripts/audit_s_refresh_publication.py \
  --refresh <existing-frozen-refresh-directory> \
  --output <new-audit-directory>/publication-audit.json \
  --allow-station-supplement z9595 --once
```

Create the output directory first. Once the one-shot check passes, omit
`--once` to observe until complete or an explicit lineage violation. A
transient observation failure retries without recomputing products.
