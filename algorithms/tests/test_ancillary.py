import json
import zipfile
from pathlib import Path

import pytest

from rainpulse_algo.radar.ancillary import (
    AncillaryError,
    _download_lock,
    _safe_extract_selected_coastline,
    build_plan,
    iter_dem_tiles,
    load_source,
    sha256_file,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONFIG = REPOSITORY_ROOT / "configs" / "ancillary" / "fujian-taiwan-v1.yaml"


def test_copernicus_tile_plan_covers_the_closed_source_domain() -> None:
    source = load_source(SOURCE_CONFIG)
    tiles = iter_dem_tiles(source)

    assert len(tiles) == 104
    assert tiles[0].tile_id == "Copernicus_DSM_COG_10_N21_00_E114_00_DEM"
    assert tiles[-1].tile_id == "Copernicus_DSM_COG_10_N28_00_E126_00_DEM"
    assert tiles[0].relative_path.as_posix().startswith(
        "ancillary/dem/copernicus-dem-glo30-2022-v1/tiles/"
    )
    assert build_plan(source)["bounds"] == {
        "west": 114,
        "east": 127,
        "south": 21,
        "north": 29,
    }


def test_source_loader_rejects_mismatched_tile_count(tmp_path: Path) -> None:
    source = SOURCE_CONFIG.read_text().replace(
        "planned_tile_count: 104", "planned_tile_count: 103"
    )
    path = tmp_path / "invalid.yaml"
    path.write_text(source)

    with pytest.raises(AncillaryError, match="tile count mismatch"):
        load_source(path)


def test_selected_coastline_extraction_is_safe_and_minimal(tmp_path: Path) -> None:
    archive = tmp_path / "coast.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for resolution in ("f", "h"):
            prefix = f"GSHHS_shp/{resolution}/GSHHS_{resolution}_L1"
            bundle.writestr(f"{prefix}.shp", b"shape")
            bundle.writestr(f"{prefix}.shx", b"index")
            bundle.writestr(f"{prefix}.dbf", b"table")
        bundle.writestr("GSHHS_shp/l/GSHHS_l_L1.shp", b"not-selected")
        bundle.writestr("README.TXT", b"documentation")

    extracted = _safe_extract_selected_coastline(archive, tmp_path / "output")
    relative = {path.relative_to(tmp_path / "output").as_posix() for path in extracted}
    assert "GSHHS_shp/f/GSHHS_f_L1.shp" in relative
    assert "GSHHS_shp/h/GSHHS_h_L1.shp" in relative
    assert "GSHHS_shp/l/GSHHS_l_L1.shp" not in relative
    assert "README.TXT" in relative


def test_sha256_is_stable_for_runtime_manifests(tmp_path: Path) -> None:
    path = tmp_path / "asset.bin"
    path.write_bytes(b"rainpulse-ancillary")
    assert sha256_file(path) == "a0df0dab47c3ac56ebec05e3ffbecf4c0673e5a14659c9fe707c3af975717950"


def test_download_lock_rejects_concurrent_writers(tmp_path: Path) -> None:
    with _download_lock(tmp_path):
        with pytest.raises(AncillaryError, match="another ancillary download"):
            with _download_lock(tmp_path):
                pass


def test_plan_json_is_serializable() -> None:
    result = build_plan(load_source(SOURCE_CONFIG))
    assert json.loads(json.dumps(result))["dem_planned_tile_count"] == 104
