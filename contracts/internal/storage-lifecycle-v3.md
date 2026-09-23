# Operations storage lifecycle v3

Source of truth: `operations-openapi.json`, new release/storage endpoints.

No meteorological payload or event schema changes. No second workflow engine.

- Version channel = managed kind + chosen exact fingerprint, CAS revision and reason. Not a deployment command.
- Filesystem report = authenticated host statvfs sample. Unsupported inode counters remain null. No remote shell.
- Pin = entire managed run; only affects retention eligibility.
- Retention grouping = kind + actual scan/analysis identity. Keep latest successful candidates and explicit defaults; all dependency protection precedes retirement.
- Plan = immutable exact run/task/attempt prefixes, endpoint, digest, expiry. Preview does not delete.
- Begin = logical retirement after real workload drain and a final reference check. Shared storage-control row locks serialize new management admissions. Does not delete S3 objects.
- Receipt = exact prefix coverage, deleted-version count, empty flag and per-prefix errors. Repeated prefix or missing prefix rejected. Authenticated host script must re-list all S3 versions; API does not pretend it inspected a host filesystem.
- Scope = `managed_candidates_only`. Never raw, normalized, analysis, products, metadata history or arbitrary bucket paths.
- `Run.storage_state` / `Task.storage_state`: AVAILABLE (not retired, not a fresh integrity assertion), RETIRED (unreadable by managed APIs, bytes may remain), DELETED (cleanup receipt says all listed versioned prefixes are empty).
- Existing execution status remains historical. SUCCEEDED and DELETED can coexist without representing no-rain or a changed numerical result.
- Physical deletion is irreversible; source-code rollback is unrelated to data restoration. No rollback to code which ignores retirement after activation.

Limits: 1..5 successful versions/group, minimum 24h (failed runs >=168h), 1..20 runs/plan, 15min preview expiry, 2048 attempts/run, 100000 object versions/CLI batch. These are initial operational limits, not performance benchmarks.
