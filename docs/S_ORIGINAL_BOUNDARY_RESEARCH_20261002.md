# Frozen-original measured RAW boundary experiment

`original_boundary.py` is a research-only qualifier, with no engine hook,
configuration activation, QC action authority or product write. It explores
measured RAW rays outside the original ledger angular island, within a fixed
two-native-beam stencil. It is not a complete wide-fan detector and is not ready
for deployment.

References exclude the target and adjacent 20 km blocks. Boundary fitting uses
at least three independent windows, 10 km measured support and 60 km span;
80% window consistency permits limited local contamination. Unknown observations
are not quiet air. Actual DBZH contrast or valid SNR noise observations are
required on both shoulders. Original IDs stay frozen; references cannot come
from new weak members. Weather, geometry gaps and protected corridors abstain.

The complete RAW history includes failed contrast windows in boundary stability.
A regression exposed a search-fence artifact: if the true RAW body extends past
the fixed stencil, a clipped edge could look stable while contradictory windows
were omitted. The implementation now retains that contradiction and abstains.
This is why ordinary dilation of the seed is not an acceptable next release.

## Evidence and limits

Final frozen eight-case report:
`.build/s-discontinuous-20261001/original-boundary-20261002-v3/report.json`.
Snapshot, published receipt, diagnostic, source report, script and module hashes
are bound. Replay verifies original arrays, observation/protection exclusions
and serialized outputs. Code/input-report changes during replay invalidate the
report. Earlier v1/v2 iterations are superseded; v2 ran while module edits were
in progress and must not be used as final evidence.

The final prototype adds **zero qualification overlap** with the exact published
Z9598 08:18 (2094) and 08:42 (728) residual gates. It does not solve these red-box
cases and must not be promoted because synthetic tests pass. At 08:42 it records
110 complete-object barrier holds, 46 RAW bodies crossing the fixed fence,
42 single-original-row holds and three insufficient complete histories.
These are overlapping parent/island diagnostics, not counts of residual gates.

Nine regression cases cover fringe recovery in a synthetic complete original
object, unavailable/nonquiet/invalid-noise shoulders, weather barriers and gaps,
full-history instability, current weather retention, no recursive growth,
held-out target windows, replay tampering and bounded-work failure. Relevant
combined suite: 411 passed. This does not establish independent weather truth
or production removal gain.

The next detector must construct complete RAW angular envelopes independently
of the small seed stencil, keeping branches, weather history and missingness.
Then bind original contamination provenance to those frozen envelopes. Barriers
must separate reference/action intervals; they must neither be crossed nor be
erased to create a convenient new source. Whole-object range-response evidence
can supplement measured geometry where local shoulders are weak. Evaluate gains
on the exact published native gates, not total proposals or artificial fixtures.

## Reproduce

```sh
PYTHONPATH=algorithms .build/xqc-zf702-investigation/venv/bin/python \
  scripts/audit_s_original_boundary.py \
  --source-report .build/s-discontinuous-20261001/complete-source-extent-20261002-v1/report.json \
  --published .build/s-discontinuous-20261001/published-v6-z9598-0818-fan-v1.json \
  --published .build/s-discontinuous-20261001/published-v6-z9598-0842-fan-v1.json \
  --output FRESH_OUTPUT_DIRECTORY
```
