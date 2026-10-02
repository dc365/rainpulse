# S-band residual ownership audit

The current complete-source qualification still freezes the angular islands of
original ledger seeds touching each local RAW tile. Recovering the seed's full
distance history does not recover weak peripheral rays or a disconnected RAW
tile with no seed. This is a structural ownership limitation, not evidence that
all rejected echoes are weather or pollution.

`scripts/audit_s_complete_source.py` now separates angular and radial extent
failures on exact Web-consumed native residual gates. It also separates absent
RAW parents, already-seeded gates and blocked gates from candidate rejections.
The zero rejection code outside the candidate mask must never count as accepted.
Original IDs, measured observations, angular islands and geometry exclusions
remain identical to the qualification replay. No algorithm or product action
is changed by this audit.

## Frozen real-data evidence

Eight native snapshots replayed against baseline `b232ab9`; private report:
`.build/s-discontinuous-20261001/complete-source-extent-20261002-v1/report.json`.
Reports bind snapshot, published receipt, module and script SHA256. These are
the previously captured v6 publications, not a claim about completed v7 outputs.

| Published visible residual disposition | Z9598 08:18 | Z9598 08:42 |
|---|---:|---:|
| Total | 2094 | 728 |
| Outside original angular island | 939 | 323 |
| No RAW parent | 231 | 182 |
| Local parent has no original source | 766 | 30 |
| Blocked outside candidate | 0 | 45 |
| Current measured polar retention | 7 | 14 |
| Qualified by footprint replay, not final removal | 122 | 18 |
| Other support/boundary rejection | 29 | 116 |

None of the extent failures in these two receipts is a radial-only failure.
This supports prioritizing complete original angular object ownership over
loosening distance or reflectivity thresholds. Angular ownership failures are
not permission to expand a seed mask by dilation.

Next implementation should freeze complete RAW object history before assigning
weak peripheral members, including observed weather and fork/merge history.
Association must be bounded to that original object; linked fragments cannot
become anchors. Independent measured boundary and pollution evidence must still
qualify actions, and unanchored fragments must retain stricter requirements.
Validate gains against the exact residual gates above and weather counterexamples.

## Reproduce

Use the existing audit with the eight paths in the preceding report and both
exact published receipts. Choose a fresh output directory:

```sh
PYTHONPATH=algorithms .build/xqc-zf702-investigation/venv/bin/python \
  scripts/audit_s_complete_source.py SNAPSHOT_1.npz SNAPSHOT_2.npz \
  --baseline-ref b232ab9 \
  --published .build/s-discontinuous-20261001/published-v6-z9598-0818-fan-v1.json \
  --published .build/s-discontinuous-20261001/published-v6-z9598-0842-fan-v1.json \
  --output FRESH_OUTPUT_DIRECTORY
```

Five attribution regressions cover angular/radial distinction, unrelated-source
range exclusion, off-candidate zero, actual qualification and barriers.
Together with bound-overlay tests: 12 passed. No independent weather truth,
production removal gain or overall completion is claimed.
