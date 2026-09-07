# Radar QC label manifest contract

This manifest freezes the local polar-label inventory used for radar QC
engineering comparisons. It is a metadata contract for labelled scans, not a
replacement for the QC volume contract and not a substitute for MRMS holdout
selection.

## Partition rules

Scans are assigned by weather process into `development` or `holdout`.
The same weather process, including earlier or later scans and any saved
context references, must remain in exactly one partition.
The holdout partition is read only after configuration and code freeze.

## Label vocabulary

Gate labels use a frozen ternary vocabulary:

- 0 = confirmed meteorological
- 1 = confirmed non-meteorological
- -1 = uncertain or not evaluated

Each scan entry records the annotator, label source, label version and review
status so later promotion reports can be traced to the exact evidence base.

## Required metadata

Each manifest entry must include:

- process identifier and case category
- radar identifier and scan identifier
- volume end time and immutable input URI
- aggregate counts for meteorological, non-meteorological and uncertain labels
- temporal context references and cross-radar context references

Case categories are reported by independent process count. Repeated scans from
the same process do not increase category sufficiency. A category remains
`insufficient_data` until at least three independent processes are available.