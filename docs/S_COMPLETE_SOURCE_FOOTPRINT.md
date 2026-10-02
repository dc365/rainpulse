# Complete original-source footprint references

Local RAW fan tiles must not truncate independently frozen source IDs. A tile
selects its touching original IDs and original angular row islands; reference
gates then come from the complete distance history of those same IDs, restricted
to those original rows. Candidate membership stays inside the original tile.
Qualified residuals never become sources, and unrelated IDs cannot supply a
boundary. This rule uses native geometry and has no station, time or ROI rule.

The target and adjacent 20 km windows remain excluded from boundary training.
Original angular gaps, range protection barriers, minimum reference support,
adjacent source rows, boundary stability and current measured polar weather
retention remain mandatory. Complete evidence can withdraw a previous local
qualification when it exposes unstable boundaries; retaining that qualification
would preserve the truncation bug. Serialized proofs replay the full ledger.

The report version is `original-source-footprint-v3-complete-ledger`. The v7
profile changes the v6 profile identity only; it keeps experimental eligibility
and all numerical thresholds unchanged. Deployment includes the verified
nearest measured flank fixes in source envelope v3 and source ledger v2.

Reproduce the comparison without modifying or publishing radar data:

```sh
PYTHONPATH=algorithms python scripts/audit_s_complete_source.py SNAPSHOT.npz \
  --baseline-ref b232ab9 --published PUBLISHED_RECEIPT.json --output NEW_DIRECTORY
python scripts/render_s_qc_review.py SNAPSHOT.npz PUBLISHED_RECEIPT.json \
  --footprint-report NEW_DIRECTORY/report.json --output NEW_IMAGE.png
```

Multiple snapshots and repeated `--published` receipts are supported. The audit
binds scan, native measurements, coordinates and input/output hashes. The image
shows actual published residuals with red offline recovered qualifications;
these are not a claim of new published removals. Private RAW, receipts and plots
stay outside commits.

The eight frozen S cases recover 0/9/0/220/156/3148/339/5375 footprint gates.
The last case also withdraws 446: 304 fail complete-history boundary stability
and 142 fall outside its inferred boundary. None of those withdrawals intersect
the audited Web residuals. Exact audited residual overlaps are 122/2094 at
Z9598 08:18 and 18/728 at 08:42. Counts describe this path's qualification,
not final engine actions or independent weather truth. Peripheral unseeded
rows and most isolated fragments remain unresolved.

Regression checks cover complete same-ID references, unrelated IDs, protected
range breaks, frozen angular rows, held-out targets, measured weather retention,
unstable full histories and serialized far-reference tampering. Render checks
cover RAW/published/diagnostic binding, invalid measurements and count changes.
