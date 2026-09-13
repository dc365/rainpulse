# RFI V3 extension (candidate)

V3 uses `qc-opensource-3.0.0`, `rfi-multivariate-v3`, frozen flags v2.
The existing V1/V2 parameter identities and algorithms remain unchanged.
All arrays use native [ray, gate], original ray ordering on disk. Neither search
association nor quarantine creates an observation. Default business profiles
are not changed. `operational_eligible=false` is mandatory.

## Measurement versus hypotheses

`RFI_STRUCTURAL_SEED_MASK` marks accepted local structural evidence;
`RFI_CORE_SEED_MASK` marks direct high-confidence *measurement* evidence.
`RFI_SEARCH_MASK` / `RFI_OBJECT_ID>0` mark bounded regions for further inspection,
NOT a deletion mask. `RFI_PERIPHERY_MASK` is the search outside structural seeds.
Search never propagates through missing gates, clear air, native topology gaps,
or the joint weather barrier. Search and confirmation start from their own
original seeds and have separate physical distance/angle limits.

Additional float32 diagnostics are `RFI_PHASE_NOISE_FRACTION`,
`RFI_PHASE_CURVATURE_DEG`, and `RFI_ZDR_OUTLIER_FRACTION`. Unavailable is NaN,
not zero. Phase curvature uses wrapped second differences on a fully measured
continuous stencil, not the unwrapped absolute phase or raw branch-cut jumps.
Raw moment, phase stencil and 2D texture availability remain distinct.

V3 mask fields are binary uint8; `RFI_V3_EVIDENCE_BITS` and
`RFI_V3_BLOCKER_BITS` are uint16. `RFI_V3_DECISION_PATH` is uint8. Their enums
are in `qc_engine/multivariate.py` and `qc_engine/refinement.py`; these are
versioned algorithm diagnostics, NOT a new reinterpretation of QC cause flags.
`TEMPORAL_RFI_VOTE_COUNT` uint8 records exact reconstructed vote counts. V3
rejects inconsistent count/fraction inputs; V2 decimal threshold remains frozen.

High RHOHV does not unconditionally protect a measurement. Conversely neither
high geometry score nor low RHOHV alone is a hard-rejection rule. Reliable SNR
and raw moments plus type-specific corroboration are required. Phase-only,
ZDR-only, missing SNR and ordinary uncorroborated candidates cannot become
confirmed RFI. High-risk unresolved measurements are quarantined and reported
separately from confirmed rejections. Weather support cannot resurrect confirmed
pollution, and no estimated reflection value replaces a native measurement.

Final QC action/trust/QPE/phase invariants remain those of the V2 extension.
The audit domain is fixed by original observations (or an explicit frozen ROI),
never by the candidate's retained mask. Counts of blocked rules overlap and
must not be summed as exclusive classes. No label-free report claims accuracy.

V3 decision paths: 0 outside search, 1 structure only, 2 core low-rho,
3 core joint phase/ZDR, 4 temporal corroboration, 5 bounded periphery,
6 unresolved quarantine, 7 joint weather barrier, 8 rejected by another QC
cause. Path 8 is not a confirmed RFI detection. Event-time and immutable
source-task lineage are preserved by explicit offline candidate experiments.
