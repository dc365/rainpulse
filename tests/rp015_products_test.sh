#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/product-builder-profile.schema.json"
  "configs/products/rp015-application-products-v1.yaml"
  "contracts/data/application-product-bundle.md"
  "contracts/events/product-build-requested.schema.json"
  "algorithms/rainpulse_algo/products/builder.py"
  "algorithms/rainpulse_algo/products/point_index.py"
  "algorithms/rainpulse_algo/products/worker.py"
  "algorithms/tests/test_products.py"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-015 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_WORKER_PROFILE: product-builder' \
  "$repo_root/deploy/docker-compose.yaml"
grep -q 'product.build.requested.v1' \
  "$repo_root/contracts/events/product-build-requested.schema.json"
grep -q 'point_query_index' \
  "$repo_root/algorithms/rainpulse_algo/products/builder.py"

printf 'RP-015 application product artifacts are present.\n'
