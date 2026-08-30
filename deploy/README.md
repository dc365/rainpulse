# RainPulse Compose environment

RP-002 runs PostgreSQL 17, NATS JetStream, and an S3-compatible MinIO server
alongside the API and Web gateway. Infrastructure ports bind to loopback by
default; API and Web remain reachable on the deployment host network.

Create the untracked runtime environment before starting Compose:

```bash
cp deploy/.env.example deploy/.env
# Fill the five required secret values in deploy/.env.
make dev-up
make infrastructure-smoke
make control-plane-smoke
make worker-smoke
make smoke
```

Use hexadecimal secret values so the MinIO client endpoint URL remains
unambiguous. `deploy/.env` is ignored by Git and must have restrictive file
permissions on shared hosts.

`RAINPULSE_ALGORITHM_VERIFICATION_HOST_ROOT` points at the host directory whose
children follow `{profile_version}/{run_id}/{summary.json,metrics.csv}`. Compose
mounts it read-only into the API. Leaving the default local path in place is
safe when no reports exist; the Web algorithm-verification view then shows an
empty state.

`make dev-down` stops containers without deleting the named volumes. Intentional
data deletion requires an explicit `docker compose down --volumes` operation and
is not part of normal lifecycle commands.

The first migration creates the Phase 1 metadata model. Migrations are applied
in filename order and recorded in `schema_migrations`; rerunning the migration
container is safe.

For a Linux/amd64 target whose Docker daemon cannot reach Docker Hub, export the
external runtime images through the workstation proxy and import them before
deployment:

```bash
HTTPS_PROXY=http://127.0.0.1:7897 make export-postgres-image
HTTPS_PROXY=http://127.0.0.1:7897 make export-python-image
# Transfer both generated image tars to the target.
docker load --input .build/postgres-17.11-alpine3.24-linux-amd64.tar
docker load --input .build/python-3.13.12-slim-bookworm-linux-amd64.tar
```

NATS, MinIO, and the MinIO client are built from the pinned source versions in
the Makefile and packaged locally; they do not require a registry pull.

RP-003 adds the long-lived `orchestrator` service. It publishes transactional
outbox records to `RAINPULSE_JOBS` and consumes terminal results through the
durable `rainpulse-orchestrator-results-v2` consumer. The stream covers job
subjects plus the immutable RP-015 `rainpulse.products.published` notification.

RP-004 adds `simulation-worker`, a long-lived Python pull consumer with a
dedicated MinIO application user. It publishes small completion/failure events,
uses `_SUCCESS.json` as the atomic object-prefix commit marker, and exposes an
internal health endpoint. `make worker-smoke` verifies both result paths and
request-redelivery idempotency. These outputs remain simulations and are not
meteorological forecasts.

RP-026 keeps the official NowcastNet GPU process outside the Phase-1 Compose
stack. The reviewed capsule is exposed at
`/opt/rainpulse/nowcastnet/official-v1`, and the optional systemd unit in
`deploy/systemd` runs the long-lived `nowcastnet-offline` consumer from the
dedicated CUDA/PyTorch environment. Install the unit under
`/etc/systemd/system`. The launcher reads
the existing ignored `deploy/.env` and maps its worker-only NATS/MinIO values;
it does not create a second secret file.

The service is deliberately not enabled by the normal deployment target. On
the shared 105 GPU, start it only inside an approved offline inference window
after enough GPU memory has been released, and stop it before restoring the
displaced model service. It consumes no realtime subject and cannot publish
application products.
