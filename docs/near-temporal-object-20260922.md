# Near temporal object audit (2026-09-22)

`near-temporal-object-20260922-v1` is an audit-only, two-prior-scan native-object
rule. It requires the same radar and matched elevation, weak reflectivity,
RHOHV/SNR bounds, weather barriers, bounded object size, and recurrence in two
strictly prior scans. It does not alter QC, CR eligibility, QPE, or published
imagery.

The replay script is read-only and reports sweep evidence, elapsed time, peak
RSS, and latest CR winner coverage. It loads only the selected CR receipt files
from a diagnostic bundle.

## BJT 2026-08-28 09:00 Z9598 replay

Inputs were the current QC volume and the two prior QC volumes around BJT
08:40 and 08:51. The latest near-object CR diagnostic job
`0fd8bdec-3668-40c9-9073-b999b6336936` supplied winner provenance.

- Elapsed: 5.592 s total; 1.206 s load, 4.225 s evaluation.
- Peak RSS: 916.8 MiB for this standalone three-volume replay.
- Native evidence: 28,698 candidate gates, 10,211 support gates, 7,203 object
  gates in 415 objects.
- Latest weak CR winners: 1,265 pixels in 0-75 km; only 40 were covered by an
  accepted temporal object. Coverage in 10-50 km was 35/1,019.
- Lowering the object recurrence threshold did not materially improve winner
  coverage (0.5: 40; 0.3: 47; 0.2: 47; 0.1: 47), so the residual winners are
  not merely being blocked by an overly strict fraction threshold.

Conclusion: computation cost is bounded and acceptable for an offline/audit
path, but this first two-scan temporal-recurrence rule does not sufficiently
cover the visible residual clutter. Do not enable it as a production removal
rule. The next evidence family should be evaluated before changing admission.
