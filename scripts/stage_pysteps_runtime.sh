#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 TARGET_SITE_PACKAGES" >&2
  exit 2
fi

TARGET_SITE_PACKAGES=$1
PYSTEPS_SOURCE_DIR=$(uv run --project algorithms python -c \
  'from importlib.util import find_spec; from pathlib import Path; print(Path(next(iter(find_spec("pysteps").submodule_search_locations))))')

if [[ ! -f "${PYSTEPS_SOURCE_DIR}/motion/lucaskanade.py" ]] || \
   [[ ! -f "${PYSTEPS_SOURCE_DIR}/extrapolation/semilagrangian.py" ]]; then
  echo "the locked pySTEPS runtime is incomplete" >&2
  exit 1
fi

mkdir -p "${TARGET_SITE_PACKAGES}"
rm -rf "${TARGET_SITE_PACKAGES}/pysteps"
cp -R "${PYSTEPS_SOURCE_DIR}" "${TARGET_SITE_PACKAGES}/pysteps"
find "${TARGET_SITE_PACKAGES}/pysteps" -type f \( -name '*.so' -o -name '*.dylib' \) -delete
