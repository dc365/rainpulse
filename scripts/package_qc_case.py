#!/usr/bin/env python3
"""Package a desensitized QC investigation bundle for one case date.

Runs on the deployment host and only reads: the service environment, PostgreSQL,
the object store and the configuration files.  It never writes business assets,
never calls the pipeline and never prints credentials.

The bundle contains, per frozen radar volume:
  inputs/<SITE>/<CASE>/normalized/   frozen QC input (scrubbed, optional sweeps)
  outputs/<SITE>/<CASE>/qc/          stored QC evidence volume
  images/<SITE>/<time>/              generated diagnostic PNGs
  configs/                           QC profile, flag definitions, geometry
  metadata/                          case index, metrics, review manifest
  README.md                          scope, desensitization, review questions

Desensitization: radar ids become SITE_A.., case ids become CASE-0001.., site
coordinates, display names, filenames and asset ids are removed from zarr
attributes, every object-store path is dropped and replaced by a relative path.
The pseudonym mapping is written next to the archive, never inside it.

Usage (on the deployment host):
  python3 scripts/package_qc_case.py --date 2026-08-28 --max-gib 2
  python3 scripts/package_qc_case.py --date 2026-08-28 --from 00:00 --to 02:00 \
      --radars z9591,z9598 --sweeps 0,1 --no-qc-volume
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MC = ROOT / ".build/linux-amd64/mc"
ALIAS = "rp"
ANALYSIS_CONFIG = "qc-opensource-mosaic-v1-6m180"
SCRUB_ATTR_KEYS = (
    "asset_id", "filename", "filename_time", "source_filename", "field_mapping_version",
    "antenna_altitude_m", "altitude_m", "radar_id", "site", "longitude_deg", "latitude_deg",
    "display_name", "raw_asset_id", "source_path", "input_path",
)
# Attribute keys are scrubbed by pattern, not only by exact name: the decoder also
# emits site_name / site_latitude_deg / site_longitude_deg / radar_config_version and
# every asset carries *_uri / *_path keys.  A name-only blacklist leaked those, so
# the patterns below are the contract and verify_directory() enforces them.
SCRUB_KEY_PATTERNS = (
    re.compile(r"(?i)^(site|station|radar|antenna)_"),
    re.compile(r"(?i)^(.*_)?(latitude|longitude)(_deg)?$"),
    re.compile(r"(?i)^.*(uri|path|filename|asset).*$"),
    re.compile(r"(?i)^(display_)?name$"),
)
# File kinds that may carry identity: everything else is a binary zarr chunk.
SCRUBBED_SUFFIXES = (
    ".json", ".jsonl", ".zattrs", ".zmetadata", ".zgroup", ".zattributes",
    ".yaml", ".yml", ".txt", ".csv", ".md",
)
LEAK_PATTERNS = (r"s3://", r"/home/yons", r"/opt/rainpulse", r"rainpulse_minio")
# Keys that must survive with a rewritten value: the QC baseline reads
# root.attrs["radar_id"] and rejects a volume whose health summary disagrees.
KEEP_VALUE_KEYS = {"radar_id"}


def sensitive_key(key: str) -> bool:
    """True when an attribute key names a site, a source file or an object path."""
    if key in SCRUB_ATTR_KEYS:
        return True
    return any(pattern.match(key) for pattern in SCRUB_KEY_PATTERNS)


def scrub_file(file: Path) -> bool:
    return file.suffix in SCRUBBED_SUFFIXES or file.name.endswith(SCRUBBED_SUFFIXES)


def log(message: str) -> None:
    print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), message, flush=True)


def service_env() -> dict:
    pid = subprocess.check_output(
        ["systemctl", "show", "rainpulse", "-p", "MainPID", "--value"], text=True
    ).strip()
    if not pid:
        sys.exit("rainpulse is not running")
    env = dict(os.environ)
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            env[key.decode()] = value.decode()
    return env


def deploy_env() -> dict:
    """Read deploy/.env for the object-store root credentials; never logged."""
    values = {}
    path = ROOT / "deploy/.env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


ENV = service_env()
DEPLOY = deploy_env()
DB = {
    "host": ENV.get("RAINPULSE_DATABASE_HOST", "127.0.0.1"),
    "port": ENV.get("RAINPULSE_DATABASE_PORT", "5432"),
    "name": ENV.get("RAINPULSE_DATABASE_NAME", "rainpulse"),
    "password": ENV.get("RAINPULSE_DATABASE_PASSWORD", ""),
}


def host_path(path: str) -> str:
    prefix = "/opt/rainpulse/"
    return str(ROOT / path[len(prefix):]) if path.startswith(prefix) else path


def psql(sql: str) -> list[list[str]]:
    result = subprocess.run(
        ["psql", "-h", DB["host"], "-p", DB["port"], "-U", "rainpulse", "-d", DB["name"],
         "-At", "-F", "\t", "-c", sql],
        env={**ENV, "PGPASSWORD": DB["password"]}, capture_output=True, text=True,
    )
    if result.returncode != 0:
        sys.exit(f"psql failed: {result.stderr.strip()}")
    return [line.split("\t") for line in result.stdout.splitlines()]


def mc(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run([str(MC), *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"mc {' '.join(args[:3])} failed: {result.stderr.strip()[:200]}")
    return result


def s3_path(uri: str) -> str:
    return uri.replace("s3://rainpulse/", "")


def tree_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def artifact_sha256(path: Path) -> str:
    """Same object-set digest the QC review tool verifies against."""
    digest = hashlib.sha256()
    objects = {}
    for file in sorted(path.rglob("*")):
        if not file.is_file() or file.name == "_SUCCESS.json":
            continue
        objects[file.relative_to(path).as_posix()] = file
    for key in sorted(objects):
        data = objects[key].read_bytes()
        key_bytes = key.encode()
        digest.update(len(key_bytes).to_bytes(4, "big"))
        digest.update(key_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def scrub_json(file: Path, replacements: dict, drop_keys: bool = True) -> None:
    try:
        payload = json.loads(file.read_text())
    except (ValueError, UnicodeDecodeError):
        return
    if _scrub_value(payload, replacements, drop_keys):
        file.write_text(json.dumps(payload))


def rewrite(text: str, replacements: dict) -> str:
    """Case-insensitive literal rewrite: the layers carry both Z9591 and radar-z9591."""
    for old, new in replacements.items():
        if old in text or old.lower() in text.lower():
            text = re.sub(re.escape(old), lambda _match, alias=new: alias, text, flags=re.IGNORECASE)
    return text


def _scrub_value(value, replacements: dict, drop_keys: bool = True) -> bool:
    changed = False
    if isinstance(value, dict):
        for key in list(value):
            if drop_keys and sensitive_key(key) and key not in KEEP_VALUE_KEYS:
                del value[key]
                changed = True
                continue
            item = value[key]
            if isinstance(item, str):
                text = rewrite(item, replacements)
                if text != item:
                    value[key] = text
                    changed = True
                if re.search(r"s3://|/home/yons|/opt/rainpulse|rainpulse_minio", text):
                    value[key] = "(removed)"
                    changed = True
            elif _scrub_value(item, replacements, drop_keys):
                changed = True
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                text = rewrite(item, replacements)
                if text != item:
                    value[index] = text
                    changed = True
                if re.search(r"s3://|/home/yons|/opt/rainpulse|rainpulse_minio", text):
                    value[index] = "(removed)"
                    changed = True
            elif _scrub_value(item, replacements, drop_keys):
                changed = True
    return changed


def walk_items(value):
    """Yield every (key, value) pair inside a decoded JSON payload."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from walk_items(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_items(item)


def harvest_site_names(directory: Path) -> dict:
    """Collect literal site/station names before their keys are deleted, so that
    any other occurrence of the same name is rewritten instead of leaking."""
    names = {}
    for file in sorted(directory.rglob("*")):
        if not file.is_file() or not scrub_file(file):
            continue
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError, OSError):
            continue
        for key, value in walk_items(payload):
            if not isinstance(value, str) or len(value) < 5:
                continue
            if re.match(r"(?i)^(site|station|display)?_?name$", key) and re.search(r"[0-9\u4e00-\u9fff]", value):
                names[value] = "(site)"
    return names


def verify_directory(directory: Path, replacements: dict, strict_keys: bool = True) -> list:
    """Re-read the scrubbed artifact and report every residual site identity.

    strict_keys applies to zarr artifacts, where the attribute namespace must be
    free of identity keys.  Service-authored manifests keep their schema keys and
    are only checked for leaked values (strict_keys=False).
    """
    problems = []
    for file in sorted(directory.rglob("*")):
        if not file.is_file() or not scrub_file(file):
            continue
        try:
            text = file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        relative = file.relative_to(directory).as_posix()[-90:]
        for radar in replacements:
            if re.search(re.escape(radar), text, flags=re.IGNORECASE):
                problems.append(f"{relative}: value contains {radar}")
        for pattern in LEAK_PATTERNS:
            if re.search(pattern, text):
                problems.append(f"{relative}: value contains {pattern}")
        if not strict_keys:
            continue
        if not (file.suffix == ".json" or file.name.endswith((".zattrs", ".zmetadata", ".zattributes"))):
            continue
        try:
            payload = json.loads(text)
        except ValueError:
            continue
        for key, _ in walk_items(payload):
            if sensitive_key(key) and key not in KEEP_VALUE_KEYS:
                problems.append(f"{relative}: key {key}")
    return problems


def scrub_volume(directory: Path, replacements: dict, sweeps: list) -> None:
    """Remove site identity from a zarr artifact and keep the selected sweeps."""
    for marker in directory.rglob("_SUCCESS.json"):
        marker.unlink()
    if sweeps:
        keep = {f"sweep_{int(sweep):03d}" for sweep in sweeps}
        for child in sorted(directory.iterdir()):
            if child.is_dir() and child.name.startswith("sweep_") and child.name not in keep:
                shutil.rmtree(child)
        for object_dir in directory.glob("_objects/*"):
            for child in sorted(object_dir.iterdir()):
                if child.is_dir() and child.name.startswith("sweep_") and child.name not in keep:
                    shutil.rmtree(child)
    replacements = {**harvest_site_names(directory), **replacements}
    for file in sorted(directory.rglob("*")):
        if file.is_file() and scrub_file(file):
            scrub_json(file, replacements)
    problems = verify_directory(directory, replacements)
    if problems:
        raise RuntimeError("desensitization check failed: " + "; ".join(sorted(set(problems))[:6]))


def artifact_root(directory: Path) -> Path:
    """Return the directory whose relative keys are the published object keys.

    A published artifact stores its logical objects under _objects/<digest>/ next to
    the _SUCCESS.json marker, so the freeze digest is taken over that inner root.
    """
    snapshots = [child for child in sorted(directory.glob("_objects/*")) if child.is_dir()]
    return snapshots[0] if len(snapshots) == 1 else directory


def mirror(prefix: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    mc("mirror", "--overwrite", f"{ALIAS}/rainpulse/{prefix}", str(destination))


def inventory_radars() -> list:
    """Every radar the deployment knows, so SITE_x means the same radar in any package.

    A scoped package still carries all-radar mosaic layers, so the alias map must
    cover the full inventory rather than only the selected rows.
    """
    return [row[0] for row in psql("SELECT DISTINCT radar_id FROM radar_scans ORDER BY radar_id")]


def case_rows(date: str, radars: list, clock_from: str, clock_to: str, scan_ids: list) -> list:
    filter_sql = ""
    if radars:
        quoted = ",".join(f"'{radar}'" for radar in radars)
        filter_sql += f" AND r.radar_id IN ({quoted})"
    if clock_from:
        filter_sql += f" AND s.volume_end_time::time >= '{clock_from}'"
    if clock_to:
        filter_sql += f" AND s.volume_end_time::time <= '{clock_to}'"
    if scan_ids:
        quoted = ",".join(f"'{scan_id}'::uuid" for scan_id in scan_ids)
        filter_sql += f" AND s.scan_id IN ({quoted})"
    rows = psql(f"""
        SELECT DISTINCT ON (s.scan_id)
               s.scan_id::text, r.radar_id, s.volume_start_time::text, s.volume_end_time::text,
               r.normalized_uri, COALESCE(r.qc_uri, ''), COALESCE(d.bundle_uri, ''),
               COALESCE(m.qc_profile, ''), COALESCE(m.qc_pipeline_version, ''),
               COALESCE(m.flag_definition_version, ''), COALESCE(m.health_state, ''),
               COALESCE(m.mean_quality_index::text, ''), COALESCE(m.valid_gate_count::text, ''),
               COALESCE(m.missing_gate_count::text, ''), COALESCE(m.low_quality_gate_count::text, ''),
               COALESCE(m.no_rain_gate_count::text, ''), COALESCE(m.radial_interference_ray_count::text, ''),
               COALESCE(m.ground_clutter_gate_count::text, ''), COALESCE(m.sea_clutter_gate_count::text, ''),
               COALESCE(m.ap_gate_count::text, ''), COALESCE(m.diagnostics::text, ''),
               COALESCE(to_char(a.analysis_time, 'YYYY-MM-DD"T"HH24:MI:SS"Z"'), '')
        FROM radar_scan_runs r
        JOIN radar_scans s ON s.scan_id = r.scan_id
        LEFT JOIN radar_qc_metrics m ON m.scan_id = r.scan_id
        LEFT JOIN analysis_cycles a ON a.analysis_time =
             to_timestamp(round(extract(epoch from s.volume_end_time) / 360) * 360)
             AND a.config_version = '{ANALYSIS_CONFIG}'
        LEFT JOIN diagnostic_runs d ON d.analysis_id = a.analysis_id
        WHERE s.volume_end_time >= '{date}' AND s.volume_end_time < '{date}'::date + 1
          AND r.normalized_uri IS NOT NULL{filter_sql}
        ORDER BY s.scan_id, s.volume_end_time, r.radar_id
    """)
    keys = ("scan_id", "radar_id", "volume_start", "volume_end", "normalized_uri", "qc_uri",
            "bundle_uri", "qc_profile", "qc_pipeline", "flag_version", "health_state",
            "mean_quality_index", "valid_gates", "missing_gates", "low_quality_gates",
            "no_rain_gates", "rfi_rays", "ground_clutter_gates", "sea_clutter_gates", "ap_gates",
            "diagnostics", "analysis_time")
    return [dict(zip(keys, row)) for row in rows]


def package(args) -> Path:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = args.label or f"qc-case-{args.date}-{stamp}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / f".{name}.work"
    if work.exists():
        shutil.rmtree(work)
    bundle = work / name
    for sub in ("inputs", "outputs", "images", "configs", "metadata"):
        (bundle / sub).mkdir(parents=True, exist_ok=True)

    rows = case_rows(args.date, [r.strip() for r in (args.radars or "").split(",") if r.strip()],
                     args.clock_from, args.clock_to,
                     [scan_id.strip() for scan_id in (args.scan_ids or "").split(",") if scan_id.strip()])
    if not rows:
        sys.exit("no QC-ready volumes matched the requested scope")
    scope_radars = sorted({row["radar_id"] for row in rows})
    inventory = inventory_radars()
    radar_ids = scope_radars + [radar for radar in inventory if radar not in scope_radars]
    radar_alias = {radar: f"SITE_{chr(ord('A') + index)}" for index, radar in enumerate(radar_ids)}
    replacements = dict(radar_alias)
    log(f"scope={len(rows)} volumes radars={len(scope_radars)} ({len(radar_ids)} in inventory) "
        f"budget={args.max_gib}GiB sweeps={args.sweeps or 'all'}")

    # The service environment only carries the worker keys; the root keys live in
    # deploy/.env.  The object store listens on loopback from the host.
    access = DEPLOY.get("RAINPULSE_MINIO_ROOT_USER") or ENV.get("RAINPULSE_OBJECT_STORE_ACCESS_KEY", "")
    secret = DEPLOY.get("RAINPULSE_MINIO_ROOT_PASSWORD") or ENV.get("RAINPULSE_OBJECT_STORE_SECRET_KEY", "")
    endpoint = f"http://127.0.0.1:{DEPLOY.get('RAINPULSE_MINIO_PORT', '9000')}"
    mc("alias", "set", ALIAS, endpoint, access, secret, check=False)

    sweeps = [s.strip() for s in (args.sweeps or "").split(",") if s.strip()]
    budget = int(args.max_gib * 1024 ** 3)
    used = 0
    included = 0
    skipped = []
    mapping = {"radars": radar_alias, "cases": {}}
    images_done = set()
    index_path = bundle / "metadata" / "cases.jsonl"

    with index_path.open("w", encoding="utf-8") as index:
        for number, row in enumerate(rows, start=1):
            case_id = f"CASE-{number:04d}"
            site = radar_alias[row["radar_id"]]
            row["case_id"] = case_id
            row["site"] = site
            case_dir = bundle / "inputs" / site / case_id
            normalized = case_dir / "normalized"
            evidence = bundle / "outputs" / site / case_id / "qc"
            try:
                mirror(s3_path(row["normalized_uri"]), normalized)
                scrub_volume(normalized, replacements, sweeps)
                frozen = artifact_root(normalized)
                size = tree_bytes(normalized)
                if args.qc_volume and row["qc_uri"]:
                    mirror(s3_path(row["qc_uri"]), evidence)
                    scrub_volume(evidence, replacements, sweeps)
                    size += tree_bytes(evidence)
            except Exception as error:  # one bad volume must not stop the run
                skipped.append({"case_id": case_id, "reason": str(error)[:160]})
                log(f"skip {case_id}: {error}")
                shutil.rmtree(case_dir, ignore_errors=True)
                shutil.rmtree(bundle / "outputs" / site / case_id, ignore_errors=True)
                continue
            if used + size > budget and included > 0:
                skipped.append({"case_id": case_id, "reason": "size budget reached"})
                shutil.rmtree(case_dir, ignore_errors=True)
                shutil.rmtree(evidence.parent, ignore_errors=True)
                log(f"budget reached at {case_id} ({used / 1024 ** 2:.0f} MiB used)")
                break
            used += size
            included += 1
            index.write(json.dumps({
                "case_id": case_id, "site": site,
                "volume_start_utc": row["volume_start"], "volume_end_utc": row["volume_end"],
                "analysis_time_utc": row["analysis_time"] or None,
                "normalized_path": str(frozen.relative_to(bundle)),
                "normalized_sha256_scrubbed": artifact_sha256(frozen),
                "qc_path": str(evidence.relative_to(bundle)) if args.qc_volume and row["qc_uri"] else None,
                "qc_profile": row["qc_profile"], "qc_pipeline_version": row["qc_pipeline"],
                "flag_definition_version": row["flag_version"], "health_state": row["health_state"],
                "mean_quality_index": row["mean_quality_index"] or None,
                "gates": {"valid": row["valid_gates"] or None, "missing": row["missing_gates"] or None,
                          "low_quality": row["low_quality_gates"] or None, "no_rain": row["no_rain_gates"] or None},
                "interference": {"radial_rays": row["rfi_rays"] or None,
                                 "ground_clutter_gates": row["ground_clutter_gates"] or None,
                                 "sea_clutter_gates": row["sea_clutter_gates"] or None,
                                 "ap_gates": row["ap_gates"] or None},
                "bytes": size,
            }, ensure_ascii=False) + "\n")
            mapping["cases"][case_id] = {"radar_id": row["radar_id"], "scan_id": row["scan_id"],
                                         "volume_end_utc": row["volume_end"]}
            if args.images and row["bundle_uri"] and row["analysis_time"]:
                key = f"{site}/{row['analysis_time']}"
                if key not in images_done:
                    images_done.add(key)
                    target = bundle / "images" / site / row["analysis_time"].replace(":", "")
                    mirror(s3_path(row["bundle_uri"]) + "/layers", target)
                    mirror(s3_path(row["bundle_uri"]) + "/query", target)
                    manifest = target.parent / "manifest.json"
                    mc("cp", f"{ALIAS}/rainpulse/{s3_path(row['bundle_uri'])}/manifest.json",
                       str(manifest), check=False)
                    for file in list(target.rglob("*.png")):
                        for radar, alias in replacements.items():
                            if radar in file.name:
                                file.rename(file.with_name(file.name.replace(radar, alias)))
                    # manifest.json sits one level above the per-time image directory
                    for file in sorted(target.parent.rglob("*")):
                        if file.is_file() and scrub_file(file):
                            scrub_json(file, replacements, drop_keys=False)
                    problems = verify_directory(target.parent, replacements, strict_keys=False)
                    if problems:
                        log(f"WARNING images {site}/{row['analysis_time']}: "
                            + "; ".join(sorted(set(problems))[:4]))
            if number % 10 == 0:
                log(f"staged {included} volumes, {used / 1024 ** 2:.0f} MiB, last {case_id} {row['volume_end']}")

    write_configs(bundle, replacements)
    (bundle / "metadata" / "summary.json").write_text(json.dumps({
        "date": args.date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {"radar_count": len(scope_radars), "volumes_matched": len(rows),
                  "volumes_included": included, "sweeps": sweeps or "all",
                  "size_budget_gib": args.max_gib, "bytes_included": used,
                  "qc_volume_included": bool(args.qc_volume), "images_included": bool(args.images)},
        "skipped": skipped,
        "analysis_config_version": ANALYSIS_CONFIG,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_review_manifest(bundle)
    write_readme(bundle, args, radar_alias, included, used, skipped)
    for sub in ("configs", "metadata", "images"):
        problems = verify_directory(bundle / sub, radar_alias, strict_keys=False)
        if problems:
            log(f"WARNING {sub}/ residual identity: " + "; ".join(sorted(set(problems))[:4]))
    archive = out_dir / f"{name}.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname=name)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (out_dir / f"{name}.sha256").write_text(f"{digest}  {archive.name}\n")
    (out_dir / f"{name}.mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
    shutil.rmtree(work, ignore_errors=True)
    log(f"archive={archive} bytes={archive.stat().st_size} sha256={digest[:16]}")
    return archive


def write_configs(bundle: Path, replacements: dict) -> None:
    target = bundle / "configs"
    for source in (ENV.get("RAINPULSE_PIPELINE_QC_CONFIG"),
                   ENV.get("RAINPULSE_PIPELINE_GRID_CONFIG"),
                   "/opt/rainpulse/configs/qc/flag-definitions-v2.yaml"):
        if not source:
            continue
        path = Path(host_path(source))
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for radar, alias in replacements.items():
            text = text.replace(radar, alias)
        text = re.sub(r"(?im)^\s*(longitude_deg|latitude_deg|display_name|asset_uri|coastline_asset_uri):.*\n?",
                      "", text)
        (target / path.name).write_text(text, encoding="utf-8")
    radar_config = ENV.get("RAINPULSE_RADAR_CONFIG_DIR", "")
    if radar_config:
        directory = Path(host_path(radar_config))
        if directory.is_dir():
            geometry = {}
            for file in sorted(directory.glob("*.yaml")):
                payload = {}
                for line in file.read_text(encoding="utf-8").splitlines():
                    match = re.match(r"\s*(azimuth|range|elevation|beam_width|gate_size|scan)_?\w*:\s*(.+)$", line)
                    if match:
                        payload[match.group(1)] = match.group(2).strip()
                if payload:
                    geometry[replacements.get(file.stem, file.stem)] = payload
            (target / "radar-geometry-summary.json").write_text(
                json.dumps(geometry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_review_manifest(bundle: Path) -> None:
    index = bundle / "metadata" / "cases.jsonl"
    cases = []
    for line in index.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        cases.append({
            "case_id": record["case_id"],
            "radar_id": record["site"],
            "partition": "development",
            "process_id": record["site"],
            "normalized_zarr": record["normalized_path"],
            "input_sha256": record["normalized_sha256_scrubbed"],
        })
    # The review tool resolves normalized_zarr against the manifest's own directory,
    # so the manifest lives at the bundle root and keeps package-relative paths.
    (bundle / "review-manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "cases": cases}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")


def write_readme(bundle: Path, args, radar_alias: dict, included: int, used: int, skipped: list) -> None:
    lines = [
        "# 雷达质控排查数据包（已脱敏）",
        "",
        f"- 案例日期（UTC）：{args.date}",
        f"- 体扫数量：{included}（时间窗 {args.clock_from or '00:00'}–{args.clock_to or '23:59'} UTC）",
        f"- 仰角：{args.sweeps or '全部'}；QC 证据产物：{'包含' if args.qc_volume else '未包含'}",
        f"- 解压后大小：约 {used / 1024 ** 2:.0f} MiB",
        "",
        "## 目录",
        "",
        "    inputs/<SITE>/<CASE>/normalized/   冻结的 QC 输入体扫（zarr）",
        "    outputs/<SITE>/<CASE>/qc/          线上 QC 输出证据体扫（zarr，可选）",
        "    images/<SITE>/<时间>/              生成图片（原始/质控/标志/质量指数/拼图）",
        "    configs/                           线上 QC 配置、标志定义、雷达几何摘要",
        "    review-manifest.json               可直接喂给 QC 对照命令的清单（路径相对包根）",
        "    metadata/cases.jsonl               每个体扫的指标与相对路径（脱敏后 sha256）",
        "    metadata/summary.json              本次打包范围与跳过原因",
        "",
        "## 脱敏口径",
        "",
        "- 站点标识改为 " + "、".join(sorted(radar_alias.values())) + "；体扫改为 CASE-0001 起编号。",
        "- 体扫属性中的站点坐标、站名、文件名、资产 ID、对象存储路径已删除或改写；",
        "  radar_id 保留键名、值改为站点伪名（QC 基线需要读该属性）。",
        "- _SUCCESS.json 不随包；每个体扫的 zarr 逻辑对象在 normalized/_objects/<digest>/ 下。",
        "- 配置中的站名与经纬度字段已移除，阈值与算法参数保持原样。",
        "- 伪名到真实站号/体扫 ID 的映射在压缩包外的同名 .mapping.json 中，未随包分发。",
        "",
        "## 复现与建议排查方向",
        "",
        "1. 用包根 review-manifest.json 跑 QC 对照（工具在仓库内）：",
        "   python -m rainpulse_algo.radar.qc_engine.review --manifest review-manifest.json",
        "       --output qc-review.json --inspect-ray 120",
        "   清单里的 input_sha256 按脱敏后的逻辑对象集重算（不含 _SUCCESS.json），",
        "   站号已改为伪名，因此可直接通过工具的身份校验。",
        "2. 逐门对比 inputs 与 outputs：关注被隔离/拒绝的门与原始 DBZH/RHOHV/ZDR/PHIDP 的关系，",
        "   判断是否存在真天气被删或污染残留。",
        "3. 结合 images 与指标：radial_interference_ray_count、ground_clutter_gate_count、",
        "   sea_clutter_gate_count、ap_gate_count 偏高而图上仍有径向线或扇形残留时，",
        "   说明证据族定位到了方向但门级判定不足。",
        "4. 需要回答的问题：残留属于 RFI、AP、地物还是真天气？哪些证据族冲突？",
        "   在什么阈值或窗口下会出现误删？给出可复核的门级判据。",
    ]
    if skipped:
        lines += ["", "## 跳过", ""] + [f"- {item['case_id']}: {item['reason']}" for item in skipped]
    (bundle / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default="2026-08-28", help="UTC case date")
    parser.add_argument("--from", dest="clock_from", default="", help="UTC clock lower bound HH:MM")
    parser.add_argument("--to", dest="clock_to", default="", help="UTC clock upper bound HH:MM")
    parser.add_argument("--radars", default="", help="comma list of radar ids; default all")
    parser.add_argument("--scan-ids", default="", help="comma list of exact scan UUIDs; overrides broad time selection")
    parser.add_argument("--sweeps", default="", help="comma list of sweep indexes; default all")
    parser.add_argument("--max-gib", type=float, default=2.0, help="size budget")
    parser.add_argument("--out", default="/home/yons/qc-packages")
    parser.add_argument("--label", default="")
    parser.add_argument("--no-qc-volume", dest="qc_volume", action="store_false")
    parser.add_argument("--no-images", dest="images", action="store_false")
    args = parser.parse_args()
    log(f"done {package(args)}")


if __name__ == "__main__":
    main()
