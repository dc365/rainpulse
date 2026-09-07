#!/usr/bin/env bash

set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_root"

required_files=(
  configs/qc/rp008-basic-v1.yaml
  configs/qc/rp040-fujian-radial-v3.yaml
  configs/qc/rp042-fujian-evidence-v1.yaml
  configs/schemas/radar-qc.schema.json
  configs/schemas/radar-qc-replay-manifest.schema.json
  configs/schemas/radar-phase-processing-profile.schema.json
  configs/schemas/radar-attenuation-profile.schema.json
  configs/schemas/radar-relative-bias-profile.schema.json
  configs/schemas/radar-calibration-profile.schema.json
  configs/schemas/radar-calibration-reference-manifest.schema.json
  configs/schemas/radar-attenuation-coefficient-table.schema.json
  contracts/data/radar-qc-replay-manifest.md
  algorithms/rainpulse_algo/radar/qc.py
  "algorithms/rainpulse_algo/radar/attenuation.py"
  "algorithms/rainpulse_algo/radar/attenuation_audit.py"
  "algorithms/rainpulse_algo/radar/calibration.py"
  "algorithms/rainpulse_algo/radar/calibration_audit.py"
  algorithms/rainpulse_algo/radar/qc_acceptance.py
  "algorithms/rainpulse_algo/radar/phase_processing.py"
  "algorithms/rainpulse_algo/radar/qc_context.py"
  "algorithms/rainpulse_algo/radar/qc_decision.py"
  "algorithms/rainpulse_algo/radar/qc_geometry.py"
  "algorithms/rainpulse_algo/radar/qc_labels.py"
  "algorithms/rainpulse_algo/radar/qc_clutter.py"
  "algorithms/rainpulse_algo/radar/qc_promotion.py"
  "algorithms/rainpulse_algo/radar/relative_bias.py"
  "algorithms/rainpulse_algo/radar/relative_bias_audit.py"
  algorithms/rainpulse_algo/radar/qc_metrics.py
  "algorithms/rainpulse_algo/radar/radial_audit.py"
  algorithms/rainpulse_algo/radar/qc_worker.py
  algorithms/rainpulse_algo/radar/qc_zarr.py
  configs/schemas/radar-qc-label-manifest.schema.json
  configs/schemas/radar-qc-promotion-profile.schema.json
  configs/verification/fujian-phidp-kdp-shadow-v1.yaml
  configs/verification/fujian-kdp-attenuation-shadow-v1.yaml
  configs/verification/fujian-radar-relative-bias-shadow-v1.yaml
  configs/verification/fujian-radar-calibration-shadow-v1.yaml
  configs/verification/fujian-qc-promotion-v1.yaml
  deploy/postgres/migrations/0007_radar_qc.sql
  algorithms/tests/test_radar_qc.py
  "algorithms/tests/test_radar_phase_processing.py"
  "algorithms/tests/test_radar_attenuation.py"
  "algorithms/tests/test_radar_qc_texture.py"
  "algorithms/tests/test_radar_qc_metrics.py"
  "algorithms/tests/test_radar_qc_decision.py"
  "algorithms/tests/test_radar_qc_geometry.py"
  "algorithms/tests/test_radar_qc_b3.py"
  "algorithms/tests/test_radial_audit.py"
  "algorithms/tests/test_benchmark_radar_qc.py"
  "algorithms/tests/test_attenuation_audit.py"
  "algorithms/tests/test_relative_bias_audit.py"
  "algorithms/tests/test_calibration.py"
  "algorithms/tests/test_calibration_audit.py"
  "scripts/benchmark_radar_qc.py"
  scripts/radar_qc_b3.py
  algorithms/rainpulse_algo/radar/qc_b3_cli.py
  algorithms/rainpulse_algo/radar/attenuation_environment.py
  algorithms/tests/test_radar_qc_b3_cli.py
  configs/schemas/radar-attenuation-environment-manifest.schema.json
  scripts/radar_qc_smoke_test.sh
)

for path in "${required_files[@]}"; do
  [[ -f "$path" ]] || {
    printf 'missing RP-008 file: %s\n' "$path" >&2
    exit 1
  }
done

rg --quiet 'rainpulse.qc-radar-volume' algorithms/rainpulse_algo/radar/qc_zarr.py
rg --quiet 'radar-qc-basic' algorithms/rainpulse_algo/worker/handlers.py
rg --quiet '^  radar-qc-worker:' deploy/docker-compose.yaml
rg --quiet 'RAINPULSE_RADAR_QC_CONFIG: /opt/rainpulse/configs/qc/rp040-fujian-radial-v3.yaml' deploy/docker-compose.yaml
rg --quiet 'CREATE TABLE radar_qc_metrics' deploy/postgres/migrations/0007_radar_qc.sql
rg --quiet 'radar.qc.requested.v1' services/control/internal/orchestration/events.go
rg --quiet '^test-radar-qc:' Makefile
rg --quiet '^benchmark-radar-qc:' Makefile
rg --quiet 'test_benchmark_radar_qc.py' Makefile
rg --quiet 'test_radar_qc_b3.py' Makefile
rg --quiet 'test_radar_phase_processing.py' Makefile
rg --quiet 'test_radar_attenuation.py' Makefile
rg --quiet 'test_attenuation_audit.py' Makefile
rg --quiet 'test_relative_bias_audit.py' Makefile
rg --quiet 'test_calibration.py' Makefile
rg --quiet 'test_calibration_audit.py' Makefile
rg --quiet 'RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE' deploy/docker-compose.realtime-shadow.yaml
rg --quiet 'RAINPULSE_RADAR_ATTENUATION_PROFILE' deploy/docker-compose.realtime-shadow.yaml
rg --quiet '^RAINPULSE_RADAR_PHASE_PROCESSING_PROFILE=' deploy/README.md
rg --quiet '^RAINPULSE_RADAR_ATTENUATION_PROFILE=' deploy/README.md

printf 'RP-008 basic polar QC structure checks passed\n'
