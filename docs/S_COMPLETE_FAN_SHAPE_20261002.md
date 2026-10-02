# Complete original fan morphology

Narrow-ray detection mistakes an adjacent member of the same original fan for
an outside shoulder. The complete-fan branch measures immutable pre-clear RAW
and uses held-out original source identities to qualify weak members inside a
fixed object. It is integrated into the opt-in source-footprint policy and its
native evidence replay. It is not yet deployed; remaining residuals and wider
weather validation prevent a completion or generalization claim.

## Current rules

- Physical 20 km windows, both measured outer shoulders, native angular order
  and no angular gaps. At most two internal sparse native rows can join an
  already bounded original band; outputs never become new source IDs.
- At least three native rows, opening at most 90 degrees, unprotected interior
  foreground at least 60%, actual outside foreground at most 20%, and observed
  coverage at least 80% on every ray. The original denominator is retained.
  Protected/weather islands contribute no interior source proof; they remain
  actual foreground on shoulders and cannot be silently turned into dry air.
- Target and adjacent windows train neither edges nor source. Five other windows
  spanning 150 km independently reproduce the same fixed angular boundaries.
  Three original source-bearing rays each retain 10 km support in three windows
  spanning 100 km. Source proof records exact original ID/ray/reference gates.
- Parent membership and range are original-source-derived. Each target traces
  the nearest original source ray, never an accepted residual, with no crossing
  of protected/weather islands in its transverse bridge. Unknown/unassociated
  parents and ambiguous identities abstain. An ID spanning several rays retains
  separate native-ray references; it must not collapse to its last row.
- RAW remains unchanged. Missing, current reliable polar weather and external
  protection remain unavailable to this branch. No receiver-power fit is needed.

`source_footprint.py` v5 persists actual DBZH, original-fan masks, original source
ownership and the source-reference mask. Policy marker2 replays the complete
fan and narrow-object paths; marker1 retains the previous narrow-only path,
and absent markers retain historical v3 geometry. Audit and disabled policy
cannot introduce action. Altered saved masks/source proof fail replay.

## Current evidence

Private authoritative receipt: `complete-fan-integration-20261002-v2/report.json`
under the existing discontinuous-S evidence root. It binds eight native input
snapshots, both child modules, footprint module/driver, original published
receipts and output masks by SHA, against baseline commit `8426ddb` (v7).

| Exact old stored Web residual | Visible gates | New qualification over v7 | Withdrawn |
| --- | ---: | ---: | ---: |
| 08:18 | 2094 | 559 | 0 |
| 08:42 | 728 | 66 | 0 |

These are offline qualification gains, not current production deletions or
independent weather truth. The 08:18 result includes the existing narrow path;
the full-fan branch alone previously qualified545 of the audited targets.
Actual RAW equality and zero external-protection overlap pass all eight cases.
The initial full-column-exclusion branch (v3 receipt) reached only0/57 at the
same two times; it is superseded. Its large angular stencil discarded too many
otherwise measured distances because of isolated weather/protection cells.
The failed earlier serialization replay remains historical evidence only.

15 fan-specific tests cover sparse members, missing sides, broad weather,
weather shoulders, native gaps, target-only support, unrelated parents,
protected bridges/references, shared source IDs on multiple rays and proof
alteration. Engine/action, writer serialization, restored native order,
historical policy and audit/no-action tests pass. The scoped current shared
checkout suite passes484 tests; 20 overlay cases cover narrow and fan reports.
This is not a clean installed-image or all-weather production proof.

The 08:18 left edge, scattered internal members and unrelated isolated objects
remain partly unqualified. Many lack original parent/source association;
qualifying a sibling cannot create that missing lineage. Full-object association
and independent weather controls remain required before the end state is met.
The inspected current0818 image shows559 red offline proposals, with gray RAW
context and the exact old stored-visible selection. It is not a full current QC
field or a105 Web refresh.

## Reusable replay and images

```sh
PYTHONPATH=algorithms .build/xqc-zf702-investigation/venv/bin/python \
 scripts/audit_s_original_boundary.py --method original-fan \
 --source-report .build/s-discontinuous-20261001/complete-source-extent-20261002-v1/report.json \
 --published .build/s-discontinuous-20261001/published-v6-z9598-0818-fan-v1.json \
 --published .build/s-discontinuous-20261001/published-v6-z9598-0842-fan-v1.json \
 --output FRESH_REPORT_DIRECTORY

PYTHONPATH=algorithms .build/xqc-zf702-investigation/venv/bin/python \
 scripts/render_s_qc_review.py \
 .build/s-discontinuous-20261001/live-web-aligned-components-v2/z9598_0818_sweep_000.npz \
 .build/s-discontinuous-20261001/published-v6-z9598-0818-fan-v1.json \
 --footprint-report .build/s-discontinuous-20261001/complete-fan-integration-20261002-v2/report.json \
 --output FRESH_REVIEW_IMAGE.png
```

The audit driver saves proposal evidence; the renderer checks input/published/
diagnostic ownership and hashes, and cannot present proposals as deletions.
