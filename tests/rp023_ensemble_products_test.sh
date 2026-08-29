#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

required_files=(
  "configs/schemas/ensemble-application-product-profile.schema.json"
  "configs/products/rp023-ensemble-application-products-v1.yaml"
  "contracts/data/ensemble-application-product-bundle.md"
  "algorithms/rainpulse_algo/products/ensemble_builder.py"
  "algorithms/rainpulse_algo/products/ensemble_profile.py"
  "algorithms/tests/test_ensemble_products.py"
  "services/control/internal/ensembleproducts/store.go"
  "scripts/profile_steps_full_grid.py"
)

for relative_path in "${required_files[@]}"; do
  if [[ ! -s "$repo_root/$relative_path" ]]; then
    printf 'missing or empty RP-023 artifact: %s\n' "$relative_path" >&2
    exit 1
  fi
done

grep -q 'RAINPULSE_ENSEMBLE_PRODUCT_ROOT' "$repo_root/deploy/docker-compose.yaml"
grep -q '/ensemble-products/latest' "$repo_root/contracts/openapi.yaml"
grep -q 'operational_eligible=false' \
  "$repo_root/contracts/data/ensemble-application-product-bundle.md"

printf 'RP-023 offline ensemble product artifacts are present.\n'
