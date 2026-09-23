#!/bin/bash
# 下载完整全球 EGM2008 格网（2.5′，PROJ 官方 CDN，~80 MB）
# 用法：bash scripts/download_full_grid.sh [目标目录，默认 data/]
set -euo pipefail
DIR="${1:-data}"
mkdir -p "$DIR"
curl -fL --retry 3 -o "$DIR/us_nga_egm08_25.tif" \
  "https://cdn.proj.org/us_nga_egm08_25.tif"
echo "下载完成。校验 SHA256："
sha256sum "$DIR/us_nga_egm08_25.tif"
echo "如需 1′ 分辨率版本，可改用 GeographicLib："
echo "  https://downloads.sourceforge.net/project/geographiclib/geoids-distrib/egm2008-1.zip"
