#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 WHEELHOUSE TARGET_SITE_PACKAGES" >&2
  exit 2
fi

WHEELHOUSE=$1
TARGET_SITE_PACKAGES=$2
DOWNLOAD_PROXY=${RAINPULSE_DOWNLOAD_PROXY:-}

OPENCV_WHEEL=opencv_python_headless-5.0.0.93-cp37-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl
OPENCV_URL=https://files.pythonhosted.org/packages/2b/97/8170e9819764c47e436c130d3ff6cfb73b58f923eae9d3a03d8982b04aec/${OPENCV_WHEEL}
OPENCV_SHA256=09a872a157c1376ab922a69bbf22f9a95bcc7b658a9d8b436a60212b02b2eeb4

SCIPY_WHEEL=scipy-1.18.1-cp313-cp313-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl
SCIPY_URL=https://files.pythonhosted.org/packages/41/48/6450ed9243315322bbc19ac57b9b70d66a20bf1d38d124c96bc4bf6af9ea/${SCIPY_WHEEL}
SCIPY_SHA256=fdaf5ea890a6183d0565f51a61799d67081bd5b1cf03c5f4b3fd3732108625c9

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

fetch_verified() {
  local url=$1
  local expected_sha256=$2
  local destination=$3
  local curl_args=(--fail --location --retry 8 --retry-all-errors --continue-at - --output "${destination}")

  if [[ -f "${destination}" ]] && [[ "$(sha256_file "${destination}")" == "${expected_sha256}" ]]; then
    return
  fi
  if [[ -n "${DOWNLOAD_PROXY}" ]]; then
    curl_args+=(--proxy "${DOWNLOAD_PROXY}")
  fi
  curl "${curl_args[@]}" "${url}"
  if [[ "$(sha256_file "${destination}")" != "${expected_sha256}" ]]; then
    echo "SHA-256 mismatch: ${destination}" >&2
    exit 1
  fi
}

mkdir -p "${WHEELHOUSE}"
fetch_verified "${OPENCV_URL}" "${OPENCV_SHA256}" "${WHEELHOUSE}/${OPENCV_WHEEL}"
fetch_verified "${SCIPY_URL}" "${SCIPY_SHA256}" "${WHEELHOUSE}/${SCIPY_WHEEL}"

uv pip install \
  --target "${TARGET_SITE_PACKAGES}" \
  --no-deps \
  --python-version 3.13.12 \
  --python-platform x86_64-manylinux_2_28 \
  "${WHEELHOUSE}/${OPENCV_WHEEL}" \
  "${WHEELHOUSE}/${SCIPY_WHEEL}"
