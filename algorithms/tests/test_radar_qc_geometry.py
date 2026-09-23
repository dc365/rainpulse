from __future__ import annotations

import numpy as np
import pytest
import zarr
from zarr.storage import MemoryStore

from rainpulse_algo.radar.qc import QCInputError, _nearest_azimuth_indices
from rainpulse_algo.radar.qc_geometry import (
    CrossRadarSupportReference,
    RadarBeamContext,
    build_trusted_cross_radar_support,
    build_vertical_consistency_diagnostics,
)


class FlatTerrain:
    def sample(self, longitude: np.ndarray, latitude: np.ndarray) -> np.ndarray:
        return np.zeros(np.broadcast(longitude, latitude).shape, dtype="float32")


def _reference_root(
    dbzh: np.ndarray,
    *,
    azimuth_deg: np.ndarray,
    range_m: np.ndarray,
    elevation_deg: float,
    radar_id: str,
) -> zarr.Group:
    store = MemoryStore()
    root = zarr.group(store=store, overwrite=True)
    root.attrs.update(
        {
            "contract_name": "rainpulse.normalized-radar-volume",
            "radar_id": radar_id,
            "radar_health": "HEALTHY",
        }
    )
    root.create_dataset("sweep_number", data=np.array([0], dtype="int16"))
    group = root.create_group("sweep_000")
    group.create_dataset("azimuth", data=np.asarray(azimuth_deg, dtype="float32"))
    group.create_dataset(
        "elevation",
        data=np.full(len(azimuth_deg), elevation_deg, dtype="float32"),
    )
    group.create_dataset("range", data=np.asarray(range_m, dtype="float32"))
    group.create_dataset("DBZH", data=np.asarray(dbzh, dtype="float32"))
    return root


def test_nearest_azimuth_indices_ignores_nonfinite_targets() -> None:
    source = np.array([0.0, 20.0, 340.0], dtype="float64")
    target = np.array([np.nan, 10.0, 350.0, np.nan], dtype="float64")

    indices = _nearest_azimuth_indices(source, target)

    np.testing.assert_array_equal(indices, np.array([1, 1, 2], dtype="int64"))


def test_nearest_azimuth_indices_rejects_all_nonfinite_targets() -> None:
    with pytest.raises(QCInputError, match="azimuth"):
        _nearest_azimuth_indices(
            np.array([0.0, 90.0], dtype="float64"),
            np.array([np.nan, np.nan], dtype="float64"),
        )


def test_vertical_consistency_diagnostics_require_verified_datum_in_v2() -> None:
    low = {
        "dbzh": np.array([[30.0, 25.0]], dtype="float32"),
        "azimuth": np.array([0.0], dtype="float32"),
        "range": np.array([10_000.0, 20_000.0], dtype="float32"),
        "elevation": np.array([0.5], dtype="float32"),
    }
    high = {
        "dbzh": np.array([[29.0, 24.0]], dtype="float32"),
        "azimuth": np.array([0.2], dtype="float32"),
        "range": np.array([10_000.0, 20_000.0], dtype="float32"),
        "elevation": np.array([1.5], dtype="float32"),
    }

    diagnostics = build_vertical_consistency_diagnostics(
        (low, high),
        minimum_dbzh=10.0,
        support_tolerance_db=15.0,
        maximum_range_m=150_000.0,
        strict_observability=True,
        maximum_azimuth_offset_deg=0.75,
        radar_beam_context=RadarBeamContext(
            radar_id="z9999",
            longitude_deg=117.0,
            latitude_deg=27.0,
            antenna_altitude_m=100.0,
            beam_width_vertical_deg=1.0,
            altitude_datum_status="unverified_engineering",
        ),
    )

    assert np.isnan(diagnostics.probabilities[0]).all()
    assert np.count_nonzero(diagnostics.available_masks[0]) == 0
    assert np.isnan(diagnostics.height_differences_m[0]).all()
    assert diagnostics.metrics["verified_vertical_datum"] == 0.0


def test_vertical_consistency_diagnostics_record_height_difference_when_beams_overlap() -> None:
    low = {
        "dbzh": np.array([[30.0, 25.0]], dtype="float32"),
        "azimuth": np.array([0.0], dtype="float32"),
        "range": np.array([10_000.0, 20_000.0], dtype="float32"),
        "elevation": np.array([0.5], dtype="float32"),
    }
    high = {
        "dbzh": np.array([[28.0, 20.0]], dtype="float32"),
        "azimuth": np.array([0.2], dtype="float32"),
        "range": np.array([10_000.0, 20_000.0], dtype="float32"),
        "elevation": np.array([1.2], dtype="float32"),
    }

    diagnostics = build_vertical_consistency_diagnostics(
        (low, high),
        minimum_dbzh=10.0,
        support_tolerance_db=15.0,
        maximum_range_m=150_000.0,
        strict_observability=True,
        maximum_azimuth_offset_deg=0.75,
        radar_beam_context=RadarBeamContext(
            radar_id="z9999",
            longitude_deg=117.0,
            latitude_deg=27.0,
            antenna_altitude_m=100.0,
            beam_width_vertical_deg=1.0,
            altitude_datum_status="verified_egm2008",
        ),
    )

    assert diagnostics.available_masks[0][0, 0] == 1
    assert diagnostics.available_masks[0][0, 1] == 1
    assert diagnostics.probabilities[0][0, 0] > 0.8
    assert np.nanmax(diagnostics.height_differences_m[0]) < 500.0
    assert diagnostics.metrics["available_gate_count"] == 2.0


@pytest.mark.parametrize("gate_level", [False, True])
def test_trusted_cross_radar_support_excludes_reference_hard_rays(gate_level) -> None:
    current_sweep = {
        "dbzh": np.full((2, 4), 20.0, dtype="float32"),
        "azimuth": np.array([0.0, 180.0], dtype="float32"),
        "range": np.array([5_000.0, 10_000.0, 15_000.0, 20_000.0], dtype="float32"),
        "elevation": np.array([0.5, 0.5], dtype="float32"),
    }
    reference_root = _reference_root(
        np.full((2, 4), 20.0, dtype="float32"),
        azimuth_deg=np.array([0.0, 180.0], dtype="float32"),
        range_m=np.array([5_000.0, 10_000.0, 15_000.0, 20_000.0], dtype="float32"),
        elevation_deg=0.8,
        radar_id="z9593",
    )
    current_beam = RadarBeamContext(
        radar_id="z9598",
        longitude_deg=117.0,
        latitude_deg=27.0,
        antenna_altitude_m=100.0,
        beam_width_vertical_deg=1.0,
        altitude_datum_status="verified_egm2008",
    )
    reference_beam = RadarBeamContext(
        radar_id="z9593",
        longitude_deg=117.0,
        latitude_deg=27.0,
        antenna_altitude_m=100.0,
        beam_width_vertical_deg=1.0,
        altitude_datum_status="verified_egm2008",
    )

    diagnostics = build_trusted_cross_radar_support(
        current_sweep,
        current_beam,
        (
            CrossRadarSupportReference(
                radar_id="z9593",
                root=reference_root,
                beam_context=reference_beam,
                health_available=True,
                dem_compatible=True,
                hard_interference_by_sweep={
                    "sweep_000": (
                        np.array([[True, False, True, False], [False] * 4], dtype=bool)
                        if gate_level
                        else np.array([True, False], dtype=bool)
                    )
                },
            ),
        ),
        terrain=FlatTerrain(),
        echo_threshold_dbzh=10.0,
        minimum_overlap_gates=1,
        valid_range_dbz=(-32.0, 80.0),
    )

    if gate_level:
        # An unavailable edge and a corrupted interior gate do not suppress
        # the two clean observations on the same ray.
        assert diagnostics.available_mask[0, 0] == 0
        assert diagnostics.available_mask[0, 2] == 0
        assert diagnostics.available_mask[0, 1] == 1
        assert diagnostics.available_mask[0, 3] == 1
    else:
        assert np.count_nonzero(diagnostics.available_mask[0]) == 0
    assert np.count_nonzero(diagnostics.available_mask[1]) >= 3
    if not gate_level:
        assert np.isnan(diagnostics.consistency_by_ray[0])
    assert diagnostics.consistency_by_ray[1] == pytest.approx(1.0)
    audit = diagnostics.availability_audit
    assert audit["status"] == "comparable"
    record = audit["references"][0]
    assert record["status"] == "comparable"
    assert record["comparable_gate_count"] == int(diagnostics.available_mask.sum())
    counts = list(record["gate_evaluations"].values())
    assert counts == sorted(counts, reverse=True)
    assert record["gate_evaluations"]["donor_qc"] < record["gate_evaluations"]["terrain"]


def test_cross_support_reports_unverified_datum_without_using_neighbours():
    beam = RadarBeamContext("z9598", 117.0, 27.0, 1740.0, 1.0, "unverified_engineering")
    d = build_trusted_cross_radar_support(
        {"dbzh": np.ones((2, 4))},
        beam,
        (),
        terrain=FlatTerrain(),
        echo_threshold_dbzh=10.0,
        minimum_overlap_gates=1,
    )
    assert d.availability_audit["blocking_reasons"] == ["current_vertical_datum_unverified"]
    assert d.availability_audit["status"] == "blocked_before_matching"
    assert not d.available_mask.any()
    assert np.isnan(d.support_fraction).all()


def test_cross_support_reports_all_missing_prerequisites():
    d = build_trusted_cross_radar_support(
        {"dbzh": np.ones((2, 4))},
        None,
        (),
        terrain=None,
        echo_threshold_dbzh=10.0,
        minimum_overlap_gates=1,
    )
    assert d.availability_audit["blocking_reasons"] == ["current_beam_missing", "terrain_missing"]


def test_confirmed_1985_configs_do_not_claim_egm2008():
    from pathlib import Path
    from rainpulse_algo.radar.config import load_radar_config
    from rainpulse_algo.radar.qc_geometry import radar_beam_context_from_config

    root = Path(__file__).resolve().parents[2] / "configs/radars/fujian-1985-20260915"
    configs = sorted(root.glob("*.yaml"))
    assert len(configs) == 4
    for path in configs:
        cfg = load_radar_config(path)
        assert cfg.site["altitude_datum"] == "EPSG:5737"
        beam = radar_beam_context_from_config(cfg)
        d = build_trusted_cross_radar_support(
            {"dbzh": np.ones((1, 1))},
            beam,
            (),
            terrain=FlatTerrain(),
            echo_threshold_dbzh=10.0,
            minimum_overlap_gates=1,
        )
        assert d.availability_audit["blocking_reasons"] == ["current_vertical_datum_incompatible"]
        assert not d.available_mask.any()


def test_explicit_height_evidence_status_is_fail_closed():
    from rainpulse_algo.radar.qc_geometry import vertical_datum_status

    converted = {
        "altitude_datum": "EPSG:3855",
        "altitude_datum_status": "converted_literature_offset",
        "altitude_sigma_m": 0.1,
        "altitude_evidence": "height-datum-1985-egm2008 v1.0.0",
    }
    assert vertical_datum_status(converted) == "converted_literature_offset"
    missing = dict(converted, altitude_datum_status="verified_egm2008", altitude_evidence=None)
    assert vertical_datum_status(missing) == "verification_evidence_missing"
    verified = dict(converted, altitude_datum_status="verified_egm2008")
    assert vertical_datum_status(verified) == "verified_egm2008"
