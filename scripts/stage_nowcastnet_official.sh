#!/usr/bin/env bash
set -euo pipefail

EXPECTED_ARCHIVE_SHA256=3607858ca1fe0cd4a22b0c5ef51dc91f76ca156c182c348a08eecf41dbd66821
EXPECTED_WEIGHTS_SHA256=5faee618c4532dff0eec27cb79c29bd7109396a968f9b173a906f8592a2059a5
EXPECTED_PATCH_SHA256=7a42637adacb6d37ffec1b559d6a31ba05c45338e01fb2d054448f3c0dfe7f32

if [[ $# -ne 2 ]]; then
  echo "usage: $0 CAPSULE_ZIP DESTINATION" >&2
  exit 2
fi

archive_path=$1
destination_path=$2
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repository_root=$(cd "$script_dir/.." && pwd)
compatibility_patch="$repository_root/packaging/nowcastnet/official-v1-device-compat.patch"

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

if [[ ! -f "$archive_path" ]]; then
  echo "capsule archive is missing: $archive_path" >&2
  exit 1
fi
if [[ -e "$destination_path" ]]; then
  echo "destination already exists: $destination_path" >&2
  exit 1
fi
if [[ $(sha256_file "$archive_path") != "$EXPECTED_ARCHIVE_SHA256" ]]; then
  echo "official capsule SHA-256 mismatch" >&2
  exit 1
fi
if [[ $(sha256_file "$compatibility_patch") != "$EXPECTED_PATCH_SHA256" ]]; then
  echo "RainPulse compatibility patch SHA-256 mismatch" >&2
  exit 1
fi

unsafe_entry=$(zipinfo -1 "$archive_path" | awk '/^\// || /(^|\/)\.\.($|\/)/ {print; exit}')
if [[ -n "$unsafe_entry" ]]; then
  echo "unsafe capsule archive entry: $unsafe_entry" >&2
  exit 1
fi

stage_tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/rainpulse-nowcastnet.XXXXXX")
trap 'rm -rf -- "$stage_tmp_dir"' EXIT
extracted_root="$stage_tmp_dir/capsule"
mkdir -p "$extracted_root"
unzip -q "$archive_path" -d "$extracted_root"

weights_path="$extracted_root/data/checkpoints/mrms_model.ckpt"
if [[ $(sha256_file "$weights_path") != "$EXPECTED_WEIGHTS_SHA256" ]]; then
  echo "official NowcastNet weights SHA-256 mismatch" >&2
  exit 1
fi

patch --no-backup-if-mismatch --fuzz=0 -p1 -d "$extracted_root" < "$compatibility_patch"
if find "$extracted_root" -name '*.orig' -print -quit | grep -q .; then
  echo "compatibility patch unexpectedly left backup files" >&2
  exit 1
fi

printf '%s\n' "$EXPECTED_ARCHIVE_SHA256" > "$extracted_root/RAINPULSE_CAPSULE_SHA256"
printf '%s\n' "$EXPECTED_PATCH_SHA256" > "$extracted_root/RAINPULSE_COMPATIBILITY_PATCH_SHA256"
mkdir -p "$(dirname "$destination_path")"
mv "$extracted_root" "$destination_path"
printf 'staged reviewed NowcastNet capsule at %s\n' "$destination_path"
