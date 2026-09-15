#!/usr/bin/env bash
# Build a separate experiment image and launch a finite audit/experiment job.
set -euo pipefail
cd "$(dirname "$0")/.."
input=$(realpath "${1:?input directory required}")
output=${2:?new output directory required}
[[ -f "$input/manifest.json" ]] || { echo 'input manifest missing' >&2; exit 1; }
[[ ! -e "$output" ]] || { echo 'output exists; refusing overwrite' >&2; exit 1; }
revision=$(git rev-parse --short HEAD)
image="rainpulse-qc-object-consensus:oc1-$revision"
docker build -f deploy/Dockerfile.qc-object-consensus -t "$image" .
mkdir -p "$output"
output=$(realpath "$output")
docker run -d --name "rainpulse-oc1-$revision-$(date +%s)" --cpus 2 --memory 3g \
  -v "$input:/input:ro" -v "$output:/output" --entrypoint sh "$image" -ec '
python /opt/oc1/scripts/qc_object_consensus.py --input /input --output /output/audit --config /opt/oc1/configs/object-consensus-v1-audit.json
python /opt/oc1/scripts/qc_object_consensus.py --input /input --output /output/experiment --config /opt/oc1/configs/object-consensus-v1-quarantine.json --plots
python /opt/oc1/scripts/verify_object_consensus_cases.py --input /input --output /output/invariants.json
'
