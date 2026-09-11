# Workspace spatial verification v1

POST `/api/v1/workspace/verification` accepts only `cycle_id`, `algorithm`
(`lk`, `steps`, `nowcastnet`) and integer `lead_minutes` (5..120, multiple of 5).
Go resolves current immutable sources. Only native forecast and matching observed
QPE frames are eligible; derived frames and T0 references are rejected. Python in
the existing product worker calculates metrics, no GPU rerun or array REST transfer.

Response echoes the three identifiers, `status` (`ready`/`unavailable`), `reason`
when unavailable, and `metrics` when ready. Metrics contain valid/total cell counts,
coverage, MAE, RMSE, and rows for thresholds [1,5,10,20,50] mm/h and requested
neighborhood widths [1,5,10,20,40] km. Events use strict `rate > threshold`.
CSI uses common valid cells. FSS compares event fractions in rectangular windows
with complete common-valid support, without padding boundaries as dry. Neighborhood
CSI applies the same event-any window operator to both fields, then computes CSI;
this is explicitly not a one-to-one displacement-matching score. Each row reports
actual odd window shape, approximate physical width in x/y, eligible center count,
hits/misses/false alarms and scores. No events or no eligible support gives null,
never an invented perfect score. Geographic km conversion uses the grid midpoint
latitude; displayed physical widths expose discretization. Missing never becomes dry.

STEPS verifies its per-time member median only on complete member support;
NowcastNet verifies the retained mean. This is deterministic radar-QPE comparison,
not probabilistic verification, independent-gauge truth, or model promotion evidence.
PSD is not included. Results are ephemeral, bounded to 64 small JSON entries for
10 minutes, keyed by full source identities and calculation version. No persistent
product copies. HTTP Cache-Control is no-store. Errors remain explicit and retryable.
