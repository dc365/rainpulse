# S-band bounded radial deployment, 2026-10-01

Algorithm commit: `5f639fcd39feb1a1e3cce7d3261246cd98ef54f6` (main and origin/main).
105 image: `rainpulse-cpu-worker:s-bounded-radial-20261001-v3`.
Both primary S QC workers healthy; X workers were not restarted.
Active QC SHA256: `eeaf33514309ac00645722f77040220d7aa120347a7cb210aba076cc37404810`.
The existing planner/worker QC path now contains the committed versioned child profile.
The original active profile and source archive are retained under `.build/s-bounded-radial-20261001-release/` on 105.

Background refresh: `scripts/refresh_s_bounded_radial.py`.
Exact Web-selected scans for four S stations at 08:18, 08:36, 08:42, 09:48,
10:18, 10:24, 10:42, 11:24 Beijing time, 2026-08-28.
Sequence: QC rebuild, grid, mosaic, QPE, diagnostics.
Live state/log: `.build/s-bounded-radial-20261001-refresh/{state.json,run.log}`.
Launch confirmed with the first QC job RUNNING under the new profile.
This is not confirmation that all Web images have been regenerated.

Enabled: discontinuous tracks, source envelopes, original fragment families,
source ledger, raw fan families, bounded original-source footprint v2.
Signal fitting and other unvalidated trial paths remain disabled.
Same-scan read-only verification of Z9598 08:18: footprint v2 retained all prior
admissions and added 424 visible low-sweep gates compared with v1. The SW long
residual remains unresolved. Experimental eligibility remains false.

Targeted algorithm tests passed. Repository-wide GitHub CI for this commit failed
on legacy parameter-hash expectations and fusion equivalence checks; it is not a
fully green release. See the workflow run 36864181136 for the complete evidence.

## Completion publication fix

The first v3 job completed QC calculation/array validation but failed in completion
metadata: the inherited profile label plus suffix exceeded the 512-character
identity contract. No v3 refreshed image was published. v4 uses a short explicit
label with a digest of the complete parent settings. The profile parser now rejects
unpublishable labels before reading radar arrays; the refresh driver resumes live
jobs and reuses succeeded stages under the exact profile hash.

## Downstream lineage correction

The initial grid receipts were old SUCCEEDED jobs, not fresh grids: the grid
identity omitted the current QC URI. All four scans remained QC_READY and the
mosaic planner correctly rejected zero ready contributors. Grid identity/output
now include the immutable QC URI digest; mosaic identity includes the sorted
selected grids and acquisition times. Regression tests failed on the old code
for both aliasing paths and passed after the fix. The resumable refresh driver
uses the rebuilt orchestrator and reruns downstream stages while reusing the
four completed v4 QC jobs. Prior claims of fresh grid completion are superseded
by this evidence. No direct database edits or input-time changes are used.
