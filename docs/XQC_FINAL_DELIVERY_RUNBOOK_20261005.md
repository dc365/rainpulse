# X QC final candidate delivery

## Boundary

This delivery closes application shipment of the original-carrier and SNR
candidate paths. It does not expand into further algorithm research. The three
validated pilots are ZF505, ZF701 and ZF702. Their results remain candidates:
operational eligibility, trusted fusion, QPE and forecast activation are disabled.
Unverified waveform/phase extension codes retain their existing interpretation
boundary; ZF703 and ZF801 remain excluded.

## Installed release

The source correction is committed and pushed as `875c6c6`. The installed image
is `rainpulse-cpu-worker:xqc-snr-final-875c6c6-candidate-mb`, with ID
`sha256:b2ba8f4f8f46d36518da7397ca0530719738c8b8f4e16eba61fdae6ddfb99794`.
Only three committed resource-accounting modules differ from its installed
parent; the other 803 Python files are identical. Four multiband workers were
recreated under the guarded pool drain and release CAS procedure. Other service
container identities were checked unchanged.

| Identity | Value |
| --- | --- |
| Release | `fujian-sx-full-20260828-x-snr-final-875c6c6` |
| Network SHA-256 | `96da59107ab4c74aa6aacd1b23b7a0e2cdf2ef7fe377d2394c4cf046b72f7dab` |
| Algorithm fingerprint | `d61b479e64a0a6384d82025c62371ad3ec61834836dab94530814cfd36c4e2cc` |

## Verification and finite closeout

Two resource defects were reproduced before repair: a previous ray's fit cache
survived into the next percentile allowance, and SNR carrier membership was
allocated before its byte refusal. The fixes release the old cache and reserve
membership parts plus concatenation before allocation. The extra membership
census consumes the existing work allowance. No QC threshold or action cap was
raised, and legacy unbounded collection keeps its original traversal.

The frozen complete X QC regression passed 575 tests with seven existing
warnings and no skipped tests. An AST-equivalent line wrap followed; the final
exact source passed 28 related tests and scoped Ruff checks. Four actual native
cuts across the three pilots retained identical exported bytes and immutable
RAW arrays. Their source-bound receipts remain private; receipt membership and
hashes were independently checked, without claiming another independent native
recomputation.

Final normal publication has one frozen endpoint: 22 volumes, 198 native cuts,
covering ZF505/ZF701/ZF702 in counts 12/4/6. The set includes all six reported
source volumes and two previously untouched controls. It is not a full UTC day,
full network or independent weather-truth evaluation. Its manifest SHA-256 is
`e7cdad6101ee2a951611b0c92f11274d232d5807906c2c24f34aecde842ac36f`.

The private `snr-final-normal-v2` controller submits normal versioned tasks,
checks every native field, coordinate and original array against the installed
direct computation, rerenders polar PNG bytes, checks geographic PNG assets,
and verifies the latest public catalog task for each volume. A separate finite
`snr-final-finish-v2` verifier reloads receipt membership, source/task hashes,
the public catalog and product assets. Neither verifier can claim weather truth
or browser acceptance. Completion requires both terminal reports, not a started
process or a successful first task.

At the initial shipment checkpoint this final batch is running; its terminal
receipt will supersede that checkpoint. The hourly follow-up remains paused.

## Operation and recovery

Use the configured deployment target and private evidence directory. Inspect
`snr-final-normal-v2/state.json`, `snr-final-finish-v2/state.json`, their recorded
PIDs, normal task receipts and current worker identities before any restart.
Do not create duplicate controllers, resume superseded controllers, alter task
states directly, overwrite failed evidence or change the frozen manifest.

Audit retries have unique attempt output, stderr and container names. Validation
precedes atomic receipt publication and ledger advancement. A timeout removes
only its exact recorded container; any leftover owned audit blocks admission
until inspected. Actual MinIO byte and inode guards, worker readiness,
diagnostic reservations and memory headroom remain mandatory.

Rollback uses the retained prior network and Compose bytes plus the prior image,
with fresh release/pool revisions and the same drain/readiness/CAS checks. Do not
run an old promotion helper against a changed release revision. S and other
services are outside this deployment's restart scope.

## Reading the result

Choose the release above in the result list. A URL with an older `result=` value
continues to display that immutable older result. Task success means products
were published; `DEGRADED_*`, withheld actions and unknown/source-local excess
remain quality warnings. They must not be relabelled as fully cleaned weather
or bypassed by deleting a complete sector. Independent weather/date/site truth
and unresolved finite/transient strong returns remain stated limitations of
this candidate delivery.

The whole-repository CI on `875c6c6` failed with the same test, TypeScript and
4,898 lint errors as its parent `5e391a1`; the comparison was rechecked. Build
and specialised checks passed. This release does not claim a globally green CI.
Browser observation repeatedly timed out while public product HTTP endpoints
worked; a native fallback was denied. Do not substitute backend or asset checks
for an actual browser screenshot, or bypass the denied fallback.
