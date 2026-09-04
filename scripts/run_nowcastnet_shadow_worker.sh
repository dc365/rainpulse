#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$script_dir/.." && pwd)

# Keep the frozen public-weight model independent from the baseline worker.
# The underlying launcher supplies NATS/MinIO credentials from deploy/.env.
export RAINPULSE_WORKER_PROFILE=nowcastnet-shadow
export RAINPULSE_WORKER_ID="${RAINPULSE_WORKER_ID:-fujian-nowcastnet-shadow-gpu0}"
export RAINPULSE_WORKER_HEALTH_ADDR="${RAINPULSE_WORKER_HEALTH_ADDR:-127.0.0.1:8097}"
export RAINPULSE_NOWCASTNET_SHADOW_TASK_CONFIG="${RAINPULSE_NOWCASTNET_SHADOW_TASK_CONFIG:-$repository_root/configs/nowcast/fujian-nowcastnet-shadow-v2.yaml}"
export RAINPULSE_NOWCASTNET_CONFIG="${RAINPULSE_NOWCASTNET_CONFIG:-$repository_root/configs/nowcast/rp026-nowcastnet-offline-v1.yaml}"
export RAINPULSE_NOWCASTNET_TILE_ATLAS_CONFIG="${RAINPULSE_NOWCASTNET_TILE_ATLAS_CONFIG:-$repository_root/configs/nowcast/fujian-nowcastnet-tile-atlas-v1.yaml}"
export RAINPULSE_GRID_CONFIG="${RAINPULSE_GRID_CONFIG:-$repository_root/configs/grids/fuzhou-0p01deg-v1.yaml}"
export RAINPULSE_NOWCASTNET_PRODUCT_CONFIG="${RAINPULSE_NOWCASTNET_PRODUCT_CONFIG:-$repository_root/configs/products/rp015-application-products-v1.yaml}"
export RAINPULSE_NOWCASTNET_CAPSULE_ROOT="${RAINPULSE_NOWCASTNET_CAPSULE_ROOT:-$repository_root/runtime/nowcastnet/official-v1}"
export RAINPULSE_NOWCASTNET_DEVICE="${RAINPULSE_NOWCASTNET_DEVICE:-cuda:0}"

exec "$script_dir/run_nowcastnet_offline_worker.sh"
