# Coherent receiver-domain extension v1

Base: bf9a4157d8de13c2ecc2023c83f2d030e2b2f63b. Optional `volume_review.receiver_domain`.
Absent configuration is omitted from serialized parameters. Parent P3 must export evidence;
any action requires its experimental mode. This is NOT a confirmed-pollution classifier.
No change to clutter, historical background, previous source families or raw decoding.

## Independent measurement domains

SNR reference uses SNR availability, including where DBZH is absent. The paired processing
relation uses measured DBZH/SNR only; polarization uses measured RHOHV/ZDR/PHIDP only.
Native availability is authoritative; no missing-as-clear-air or fabricated DBZH.
The portable research reader applies the actual normalized field's reserved-code metadata.
Production uses `NativeSweep.field_available`; it never hardcodes a vendor code table.

For each 20 km target block, exclude target and one guard block on either side from ALL
reference membership, measured-side search, model statistics and content hashes. No
whole-ray stationarity prefilter may condition the existence of a fold on target values.
SNR references need >=5 supported blocks and >=80 km span. Paired references need >=40
samples, >=2 blocks and >=40 km span. At least 10 samples per retained block. The coherent
polar reference needs >=30 samples across >=2 supported blocks. These are research settings.

Both shoulders must contain actual SNR observations across the reference span. Search
stops at geometric gaps/bad rays. SNR stationarity, angular contrast, processing relation
and coherent polarization are compatibility tests, not independent probability votes.
Target power and every available polarization moment can veto. Numeric ZDR tails do not
become precise polarization or a partial-mode bypass. Measured target shoulders that no
longer exhibit sufficient contrast mark a mixed/ambiguous interval, not a clean source.

## Joint states and actions

`RDR_STATE`: 0 missing; 1 no supported source; 2 source hypothesis; 3 partial target;
4 mixed/conflicted; 5 independent/unattributed protection. A high local rho/flat phase
with explicit VOR provenance can be jointly reviewed with a strong source model, only
under `local_policy=source_joint_review`. Spatial/external weather, specific NP weather,
NP mixed/small-strong protections, and unknown origins remain protected. They are not
silently reclassified. Cross-layer source confirmation is not mandatory for this branch.

- `audit`: diagnostics only; EVERY parent measurement/disposition is unchanged.
- `cr_only`: withhold qualified full source targets from trusted CR; QPE/trust unchanged.
- `quarantine`: qualified full targets may become DOWNWEIGHT with LOW_QUALITY; remove
  trust/QPE/CR eligibility and invalidate dependent trusted phase/KDP/attenuation segments.
- `partial_policy=cr_withhold`: optionally withhold unprotected partial targets from CR
  ONLY. Partial targets never cause QPE quarantine. Numeric tails and conflicting available
  moments never obtain this route. Default is diagnostic_only.

No newly withheld target becomes a reference/anchor; no gap value is generated; no mask
is copied along an entire ray. No old reject/quarantine/CR failure is revived. No fresh
RFI confirmation or ground-clutter labels are produced. No dBZ corrections/interpolation.
`CR_QUALIFICATION_REASON` bit1024 records a new receiver withholding; clear admission bit1.

## Reversible validation and resource limits

`RDR_BEFORE_*` stores exact mutable fields; restore this view and invoke the unchanged
VOR/NMR/NP/OC1/V7 validator FIRST. Then recompute the receiver disposition and compare all
fields and dtypes. RDR masks must be observed-only, full and partial disjoint, models
indexed exactly, residuals within contract and protections respected. All reference
records include acquisition-order rays, actual reference intervals and digests.

Resource exhaustion discards ALL partial sweep work and emits zero-action records for
every sweep; preserve the parent product. Invalid input/config identity fails explicitly.
Review budgets never silently undo withholding, but require review of coverage loss.

## Integration and CR

Adapter runs after parent VOR and optional NMR, within the existing QC worker. Raw native
source identity is checked; raw arrays stay immutable. Root attributes bind version,
configuration and digest; P0 snapshots retain diagnostics, excluding full before-state
arrays which remain in the full QC Zarr. `qc/volume_review/receiver_domain.json` contains
reference/protection/disposition receipts. Existing configured pipeline_version is retained;
child profile version/parameters SHA identify the opt-in algorithm. Do not reuse old jobs.

All participating CR roots must use the same receiver configuration digest. Reject any
RDR-withheld gate with CR eligibility. Persist `CR_RECEIVER_SOURCE`, `CR_RECEIVER_PARTIAL`,
`CR_RECEIVER_MIXED`, `CR_RECEIVER_WITHHELD` as risk values; these are not extra weather
measurements. Winner/runner-up provenance remains original gate based. A replacement
maximum must itself be eligible. No trusted contributor means unavailable, not zero rain.

## Verification boundary

Focused tests include synthetic parent adapter/validator/actual compositor execution,
not the complete production QC worker. Supplied 09:00/09:12 packages contain frozen raw
volumes but no full current QC output. Raw evidence replay is NOT current-pipeline net
isolation or a meteorological accuracy result. Model thresholds were developed on these
cases; they are not independent holdouts. Full same-input worker/QC/Zarr/four-radar CR
replay and independent weak-weather/convective controls are required before promotion.
