import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from rainpulse_algo.datasets.mrms_archive import (
    IEM_SOURCE_ID,
    NOAA_ARCHIVE_START,
    MRMSArchiveError,
    MRMSObject,
    _day_manifest,
    _download_one,
    manifest_path,
    parse_iem_listing,
    parse_listing,
    relative_object_path,
    validate_cadence,
    verify_range,
)


def _listing(*filenames: str) -> bytes:
    contents = "".join(
        f"""
        <Contents>
          <Key>CONUS/PrecipRate_00.00/20210829/{filename}</Key>
          <ETag>&quot;abc123&quot;</ETag>
          <Size>492454</Size>
        </Contents>
        """
        for filename in filenames
    )
    return f"""
    <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
      <IsTruncated>false</IsTruncated>
      {contents}
    </ListBucketResult>
    """.encode()


def test_parse_listing_selects_only_requested_ten_minute_slots() -> None:
    objects = parse_listing(
        _listing(
            "MRMS_PrecipRate_00.00_20210829-120000.grib2.gz",
            "MRMS_PrecipRate_00.00_20210829-120200.grib2.gz",
            "MRMS_PrecipRate_00.00_20210829-121000.grib2.gz",
            "README.txt",
        ),
        10,
    )

    assert [item.valid_time.minute for item in objects] == [0, 10]
    assert objects[0].etag == "abc123"


def test_directory_layout_is_stable_and_allows_later_cadence_additions() -> None:
    item = parse_listing(_listing("MRMS_PrecipRate_00.00_20210829-120000.grib2.gz"), 10)[0]

    assert relative_object_path(item, 10).as_posix() == (
        "raw/noaa-mrms-pds/CONUS/PrecipRate_00.00/10min/2021/08/29/"
        "MRMS_PrecipRate_00.00_20210829-120000.grib2.gz"
    )
    assert manifest_path(Path("/archive"), date(2021, 8, 29), 10).as_posix() == (
        "/archive/manifests/PrecipRate_00.00/10min/2021/08/29.json"
    )


def test_iem_listing_supports_pre_noaa_archive_days() -> None:
    payload = b"""
    <a href="PrecipRate_00.00_20190101-000000.grib2.gz">first</a>
    <a href="PrecipRate_00.00_20190101-000200.grib2.gz">skip</a>
    <a href="PrecipRate_00.00_20190101-001000.grib2.gz">second</a>
    """
    objects = parse_iem_listing(date(2019, 1, 1), payload, 10)

    assert [item.valid_time.minute for item in objects] == [0, 10]
    assert objects[0].source_id == IEM_SOURCE_ID
    assert objects[0].size_bytes is None
    assert (
        relative_object_path(objects[0], 10)
        .as_posix()
        .startswith("raw/iem-mtarchive/CONUS/PrecipRate_00.00/10min/2019/01/01/")
    )


def test_unknown_size_partial_is_resumed_before_becoming_final(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = parse_iem_listing(
        date(2019, 1, 1),
        b'<a href="PrecipRate_00.00_20190101-000000.grib2.gz">file</a>',
        10,
    )[0]
    destination = tmp_path / relative_object_path(item, 10)
    partial = destination.with_name(f"{destination.name}.part")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"part")
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        output = Path(command[command.index("--output") + 1])
        with output.open("ab") as handle:
            handle.write(b"ial")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    asset = _download_one(item, tmp_path, 10, None)

    assert len(calls) == 1
    assert destination.read_bytes() == b"partial"
    assert not partial.exists()
    assert asset["size_bytes"] == 7
    assert asset["source_id"] == IEM_SOURCE_ID


def test_manifest_keeps_source_gaps_explicit() -> None:
    manifest = _day_manifest(date(2019, 1, 1), 10, (), [], [])

    assert NOAA_ARCHIVE_START == date(2020, 10, 14)
    assert manifest["source_supported"] is True
    assert manifest["complete"] is False
    assert len(manifest["missing_source_times"]) == 144


def test_verify_checks_size_and_optional_hash(tmp_path: Path) -> None:
    item = parse_listing(_listing("MRMS_PrecipRate_00.00_20210829-120000.grib2.gz"), 10)[0]
    relative = relative_object_path(item, 10)
    asset = tmp_path / relative
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"abc")
    manifest = _day_manifest(
        date(2021, 8, 29),
        10,
        (MRMSObject(item.key, 3, item.etag, item.valid_time),),
        [
            {
                "etag": item.etag,
                "relative_path": relative.as_posix(),
                "sha256": "wrong",
                "size_bytes": 3,
                "source_key": item.key,
                "source_url": "https://example.invalid/object",
                "status": "downloaded",
                "valid_time": item.valid_time.isoformat().replace("+00:00", "Z"),
            }
        ],
        [],
    )
    path = manifest_path(tmp_path, date(2021, 8, 29), 10)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))

    result = verify_range(
        date(2021, 8, 29),
        date(2021, 8, 29),
        tmp_path,
        cadence_minutes=10,
        full_hash=True,
    )

    assert result["complete"] is False
    assert result["errors"] == [f"sha256 mismatch: {asset}"]


def test_verify_separates_transport_integrity_from_source_completeness(tmp_path: Path) -> None:
    day = date(2021, 8, 10)
    path = manifest_path(tmp_path, day, 10)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "assets": [],
                "complete": False,
                "missing_source_times": ["2021-08-10T13:50:00Z"],
            }
        )
    )

    result = verify_range(
        day,
        day,
        tmp_path,
        cadence_minutes=10,
        full_hash=True,
    )

    assert result["transport_integrity"] is True
    assert result["source_completeness"] is False
    assert result["complete"] is False
    assert result["incomplete_source_days"] == ["2021-08-10"]


@pytest.mark.parametrize("cadence", [1, 3, 7, 11, 61])
def test_invalid_cadence_is_rejected(cadence: int) -> None:
    with pytest.raises(MRMSArchiveError):
        validate_cadence(cadence)


@pytest.mark.parametrize("cadence", [2, 4, 6, 10, 12, 20, 30, 60])
def test_valid_cadence_is_accepted(cadence: int) -> None:
    validate_cadence(cadence)
