# Maintenance boundaries v1

Scope: internal source ownership. This document does not create a new REST/event/data
schema, change stored products, or authorize operational publication.

## Read boundary

`readquery.RunReader` supplies GetRun, LatestRun and ListRuns.
`readquery.AnalysisReader` supplies analysis listing/detail, QPE metrics and diagnostics.
`readquery.AnalysisPageReader` is the optional stable analysis-page capability.
`readquery.Service` validates cancellation and list size (1–200) and delegates typed
reads. SQL, transport response types, auth, environment and storage mutation do not
belong in this package. Existing REST timestamp/UUID cursors are preserved; batch-1
scheduler cursors remain in planning/postgres and must not be replaced with these.

The native composition root constructs one service and passes it to API and Workspace.
API parses HTTP parameters and converts to existing OpenAPI-generated responses.
Workspace converts domain values directly to its existing subset views. A store error
is returned to existing degraded-source handling; an injected-service error must not
silently re-enter HTTP. Legacy constructors without a service explicitly retain their
old adapter for tests/backward compatibility. This is not automatic error fallback.

The completed typed slice covers forecast catalog pages, analysis catalog pages,
analysis detail, QPE summary and diagnostic bundles. Product/ensemble projections and
interval proxy calls remain legacy seams for a separate behavior-characterized move.

## Geometry resource boundary

`qc_resources.GeometryProviders` contains only six I/O/resource factories. The loader
returns (beam, terrain, radar_config_directory, expected_dem_asset_version) and optional
audit evidence. It imports no Worker. Missing/mismatched geometry retains the original
availability and datum semantics. DEM tile validation still belongs to
VerifiedDEMTileStore. No cache, classifier, retry engine or replacement DEM reader is
introduced by this move.

`qc_engine.context.prepare_open_source_inputs` accepts a geometry loader explicitly.
The default uses qc_resources, not qc_worker. The online wrapper keeps old injectable
loader names so existing replay/test dependency overrides remain usable. Resource
existence never implies altitude-datum conversion, weather truth or publication approval.

## Browser boundary

App owns route selection and lazy chunk failure/loading UI only. AdminRoute groups the
existing admin page and inspector; the normal workspace does not eagerly import them.
refreshPolicy owns a pure refresh decision and stable selection identity only.
useWorkspaceData owns timers, SSE, HTTP cancellation and reducer dispatch. The reducer
continues owning the committed displayed cycle and timeline selection.

An SSE event is a change notification, not a replacement data snapshot. A pinned history
view refreshes when its selected asset IDs/capabilities change and also performs bounded
safety revalidation even with unchanged IDs. Do not use freshness_seconds as a content
revision. Hidden pages suppress automatic timer/event refreshes; initial load, explicit
selection and manual refresh remain permitted. Reconnection/visibility refreshes must not
be downgraded by an adjacent ordinary SSE event.

Current defaults: 30 s disconnected catalog/live fallback, 120 s healthy safety poll,
120 s pinned-detail revalidation; a 5 s policy tick may add up to 5 s before issuing the
request. These are client scheduling intervals, NOT promises about server data age,
network latency, background-tab scheduling or cached response freshness.
