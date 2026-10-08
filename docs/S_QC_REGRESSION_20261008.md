# S QC regression and shared analysis clock

The strict paired range validator could reject an entire mixed reference population
because a minority of rays failed per-ray validation. Its zero fallback then removed
previous broad-source interference actions. The correction preserves the successful
scalar path exactly and, on failure, tests bounded reference-only processing cohorts.
Each cohort still passes the original strict validator. Unsupported rays cannot borrow
another cohort's calibration. Recompute the original actions from the same RAW and
current weather/conflict context as a retention floor; stored old QC is not a truth label.

The opt-in child is `configs/qc/s-range-components-20261008-v10.yaml`. It changes only
profile identity and `generalization.broad_source.component_range_calibration` from
its v9 parent. RAW, weather vetoes, target/guard exclusion and numeric thresholds remain.
Freeze the parent image and apply only the broad-source patch and new helper when
packaging from the shared checkout; unrelated research changes are excluded.

Single, overlay and composite views share the selected analysis clock and exact
composite S source scan/content identity. Acquisition range is displayed separately.
Native polar diagnostics may use a different downstream grid; that grid cannot veto
an exact scan/content match. A future, expired, wrong-rebuild or unpaired sweep is
explicitly missing. Switching views retains the selected time and series.

Verification in this repair: 15 scoped Python regression checks, 29 targeted UI checks,
206 total Web tests and TypeScript/Vite builds pass. The actual installed-parent replay
reproduces stored source masks: the 10:42 candidate restores 33,432 currently visible
gates without losing current source actions; the successful 14:06 source mask is unchanged.
These are source-stage checks, not independent weather truth or full-QC acceptance.

105 QC child workers and Web clock fix are installed. Original batch controllers and
hourly follow-up remain paused with their handles retained. Two normal QC rebuilds,
paired diagnostic generation and causal five-S composites at 10:48/14:12 are bounded
verification only. Normal jobs/public PNG acceptance are pending capacity; do not resume
the frozen v9 producers under the child profile. Private release/job/cleanup receipts are
under `.build/s-five-station-pipeline-20261008/regression-z9591-1042-20261008` on 105.
