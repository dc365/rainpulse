"""Date-scoped, replay-only RainPulse database/object export and safe import.

Python standard library only; Docker/psql is used for database access. Never
exports credentials, queues, raw radar, model checkpoints or database volumes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import hmac
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

TABLES = set(
    "config_versions data_sources model_versions input_assets workflow_runs forecast_runs jobs model_runs products product_assets algorithm_runs radars radar_config_versions radar_scans radar_scan_runs analysis_cycles analysis_cycle_radars mosaic_runs qpe_runs diagnostic_runs radar_health_metrics radar_qc_metrics radar_grid_metrics".split()
)
TABLES.add("pipeline_regeneration_requests")
REFERENCE_TABLES = {
    "config_versions",
    "model_versions",
    "radars",
    "radar_config_versions",
    "data_sources",
}
FORMAT = "rainpulse-history/1.0"
MAX_OBJECT = 512 * 1024 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def ident(value):
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError("invalid SQL identifier")
    return '"' + value + '"'


def safe_path(root, relative):
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or any(x in ("", ".", "..") for x in relative.split("/"))
    ):
        raise ValueError("unsafe package path")
    result = root / relative
    if not result.resolve().is_relative_to(root.resolve()):
        raise ValueError("package path escapes root")
    return result


def check_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                re.search(
                    r"(^|_)(password|secret|access_key|api_key|authorization|auth_token|private_key)($|_)",
                    key,
                    re.I,
                )
                and item
            ):
                raise ValueError(
                    "credential-like metadata found; export refused (value withheld)"
                )
            check_secrets(item)
    elif isinstance(value, list):
        for item in value:
            check_secrets(item)
    elif isinstance(value, str):
        if re.search(
            r"[a-z]+://[^/\s]+:[^/@\s]+@|-----BEGIN .*PRIVATE KEY", value, re.I
        ):
            raise ValueError("credential-like URI/key found; export refused")


def validate_replay_rows(rows):
    check_secrets(rows)
    for table, allowed in [
        ("jobs", {"SUCCEEDED", "FAILED", "SKIPPED"}),
        ("pipeline_regeneration_requests", {"SUCCEEDED", "FAILED", "CANCELLED"}),
    ]:
        if any(r["status"] not in allowed for r in rows.get(table, [])):
            raise ValueError(
                "active workflow records cannot be imported as a replay case"
            )


def read_env(path):
    # Compose dotenv is data, never source it as executable shell code.
    values = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class Database:
    def __init__(self, args):
        docker = ["docker"]
        if subprocess.run(
            docker + ["info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ).returncode:
            subprocess.run(["sudo", "-v"], check=True)
            docker = ["sudo", "docker"]
        self.command = docker + [
            "compose",
            "--env-file",
            str(args.env_file),
            "-f",
            str(args.root / "deploy/docker-compose.yaml"),
            "exec",
            "-T",
            "postgres",
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "rainpulse",
            "-d",
            args.database,
        ]
        self.compose = self.command[: self.command.index("exec")]

    def require_stopped_readers(self):
        result = subprocess.run(
            self.compose + ["ps", "--status", "running", "--services"],
            check=True,
            capture_output=True,
            text=True,
        )
        active = result.stdout.split()
        if any(
            s in ("api", "web", "orchestrator") or "worker" in s or "ingest" in s
            for s in active
        ):
            raise ValueError(
                "stop application services/workers before import; keep only database/object infrastructure running"
            )

    def run(self, sql):
        proc = subprocess.run(self.command, input=sql, text=True, capture_output=True)
        if proc.returncode:
            # PostgreSQL DETAIL can contain entire rows including private metadata.
            raise RuntimeError(
                "database command failed; no data committed; inspect local PostgreSQL logs"
            )
        return proc.stdout.strip()

    def query(self, sql):
        return json.loads(
            self.run("SELECT COALESCE(json_agg(q),'[]'::json) FROM (" + sql + ") q;")
        )

    def schema(self):
        columns = self.query(
            "SELECT table_name,column_name FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position"
        )
        fks = self.query("""SELECT conrelid::regclass::text AS child,confrelid::regclass::text AS parent,condeferrable AS deferred,
        ARRAY(SELECT a.attname FROM unnest(conkey) WITH ORDINALITY u(n,i) JOIN pg_attribute a ON a.attrelid=conrelid AND a.attnum=u.n ORDER BY u.i) AS child_keys,
        ARRAY(SELECT a.attname FROM unnest(confkey) WITH ORDINALITY u(n,i) JOIN pg_attribute a ON a.attrelid=confrelid AND a.attnum=u.n ORDER BY u.i) AS parent_keys
        FROM pg_constraint WHERE contype='f' AND connamespace='public'::regnamespace""")
        pk = self.query("""SELECT conrelid::regclass::text AS name,
        ARRAY(SELECT a.attname FROM unnest(conkey) WITH ORDINALITY u(n,i) JOIN pg_attribute a ON a.attrelid=conrelid AND a.attnum=u.n ORDER BY u.i) AS keys
        FROM pg_constraint WHERE contype='p' AND connamespace='public'::regnamespace""")
        result = {}
        for row in columns:
            if row["table_name"] in TABLES:
                result.setdefault(row["table_name"], []).append(row["column_name"])
        return {
            "columns": result,
            "fks": [f for f in fks if f["child"] in TABLES],
            "pk": {r["name"]: r["keys"] for r in pk if r["name"] in TABLES},
        }


class Objects:
    """Small path-style AWS Signature V4 client; keys stay in memory only."""

    def __init__(self, env):
        self.endpoint = env.get(
            "RAINPULSE_OBJECT_STORE_ENDPOINT",
            "http://127.0.0.1:" + env.get("RAINPULSE_MINIO_PORT", "9000"),
        ).rstrip("/")
        self.access = env["RAINPULSE_MINIO_WORKER_ACCESS_KEY"]
        self.secret = env["RAINPULSE_MINIO_WORKER_SECRET_KEY"]

    def request(self, method, key="", query=None, data=b""):
        now = dt.datetime.now(dt.timezone.utc)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        path = "/rainpulse/" + urllib.parse.quote(key, safe="/~")
        query = urllib.parse.urlencode(
            sorted((query or {}).items()), quote_via=urllib.parse.quote, safe="~"
        )
        host = urllib.parse.urlsplit(self.endpoint).netloc
        digest = hashlib.sha256(data).hexdigest()
        headers = {"host": host, "x-amz-content-sha256": digest, "x-amz-date": stamp}
        names = ";".join(sorted(headers))
        canonical = "\n".join(
            [
                method,
                path,
                query,
                "".join(k + ":" + headers[k] + "\n" for k in sorted(headers)),
                names,
                digest,
            ]
        )
        scope = day + "/us-east-1/s3/aws4_request"

        def sign(k, msg):
            return hmac.new(k, msg.encode(), hashlib.sha256).digest()

        key_bytes = ("AWS4" + self.secret).encode()
        for part in [day, "us-east-1", "s3", "aws4_request"]:
            key_bytes = sign(key_bytes, part)
        signature = sign(
            key_bytes,
            "AWS4-HMAC-SHA256\n"
            + stamp
            + "\n"
            + scope
            + "\n"
            + hashlib.sha256(canonical.encode()).hexdigest(),
        ).hex()
        headers["Authorization"] = (
            "AWS4-HMAC-SHA256 Credential="
            + self.access
            + "/"
            + scope
            + ", SignedHeaders="
            + names
            + ", Signature="
            + signature
        )
        req = urllib.request.Request(
            self.endpoint + path + ("?" + query if query else ""),
            data=data if method == "PUT" else None,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=120) as response:
            body = response.read(MAX_OBJECT + 1)
            if len(body) > MAX_OBJECT:
                raise ValueError("individual replay artifact exceeds 512 MiB limit")
            return body

    def keys(self, prefix):
        token = ""
        while True:
            query = {"list-type": "2", "prefix": prefix}
            if token:
                query["continuation-token"] = token
            tree = ET.fromstring(self.request("GET", query=query))
            ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
            for node in tree.findall("s:Contents/s:Key", ns):
                yield node.text
            next_token = tree.findtext("s:NextContinuationToken", "", ns)
            if not next_token:
                break
            if next_token == token:
                raise ValueError("repeated object listing cursor")
            token = next_token


def http_json(base, path):
    request = urllib.request.Request(
        base.rstrip("/") + path, headers={"Cache-Control": "no-cache"}
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.headers.get("Warning"):
            raise ValueError("upstream returned stale catalog; export refused")
        return json.load(response)


def select_day(base, day, offset):
    start = dt.datetime.combine(
        dt.date.fromisoformat(day), dt.time(), dt.timezone(dt.timedelta(hours=offset))
    )
    end = start + dt.timedelta(days=1)
    result, cursor, seen = [], "", set()
    while True:
        page = http_json(
            base,
            "/api/v1/workspace/cycles?limit=200"
            + ("&cursor=" + urllib.parse.quote(cursor, safe="") if cursor else ""),
        )
        if page.get("degraded_sources"):
            raise ValueError(
                "workspace catalog degraded; retry after data sources recover"
            )
        result.extend(
            x
            for x in page["items"]
            if start
            <= dt.datetime.fromisoformat(x["issue_time"].replace("Z", "+00:00"))
            < end
        )
        cursor = page.get("next_cursor")
        if not cursor:
            break
        if cursor in seen:
            raise ValueError("repeated cycle cursor")
        seen.add(cursor)
    if not result:
        raise ValueError("no available historical cycles for the selected local date")
    return result


class Selection:
    def __init__(self, db, schema):
        self.db, self.schema, self.rows = db, schema, {}
        self.selected_keys = set()

    def add(self, table, columns, values):
        values = list(set(tuple(x) for x in values if all(v is not None for v in x)))
        values = [
            v for v in values if (table, tuple(columns), v) not in self.selected_keys
        ]
        self.selected_keys.update((table, tuple(columns), v) for v in values)
        if not values:
            return
        if table not in TABLES:
            raise ValueError("unexpected metadata dependency: " + table)
        keys = self.schema["pk"][table]
        current = self.rows.setdefault(table, {})
        for offset in range(0, len(values), 200):
            predicate = " OR ".join(
                "("
                + " AND ".join(
                    ident(k) + "::text=" + quote(v) for k, v in zip(columns, value)
                )
                + ")"
                for value in values[offset : offset + 200]
            )
            for row in self.db.query(
                "SELECT * FROM " + ident(table) + " WHERE " + predicate
            ):
                current[tuple(row[k] for k in keys)] = row

    def dependencies(self):
        for _ in range(100):
            before = sum(map(len, self.rows.values()))
            for fk in self.schema["fks"]:
                values = [
                    tuple(r[k] for k in fk["child_keys"])
                    for r in self.rows.get(fk["child"], {}).values()
                ]
                self.add(fk["parent"], fk["parent_keys"], values)
            if sum(map(len, self.rows.values())) == before:
                return
        raise ValueError("metadata dependency closure did not converge")


def export_case(args):
    if args.output.exists() or Path(str(args.output) + ".sha256").exists():
        raise ValueError("output already exists; refusing overwrite")
    env = read_env(args.env_file)
    db, objects = Database(args), Objects(env)
    schema = db.schema()
    selected = Selection(db, schema)
    cycles = select_day(args.api_base, args.date, args.utc_offset)
    print("Selected local-date cycles:", len(cycles), flush=True)
    details, product_ids, diagnostic_ids, bundle_ids = [], set(), set(), set()
    for i, cycle in enumerate(cycles):
        detail = http_json(
            args.api_base, "/api/v1/workspace/cycles/" + cycle["cycle_id"]
        )
        if any(w != "analysis-fallback" for w in detail.get("warnings", [])):
            raise ValueError(
                "cycle has unresolved product warnings: " + cycle["cycle_id"]
            )
        details.append(detail)
        for panel in detail.get("panels", []):
            for frame in panel.get("frames", []):
                url = frame.get("image_url", "")
                match = re.match(r"^/api/v1/products/([0-9a-f-]{36})/assets/", url)
                if match:
                    product_ids.add(match[1])
                match = re.match(r"^/api/v1/diagnostics/([0-9a-f-]{36})/layers/", url)
                if match:
                    diagnostic_ids.add(match[1])
        if detail.get("nowcastnet_bundle_id"):
            bundle_ids.add(detail["nowcastnet_bundle_id"])
        if (i + 1) % 10 == 0:
            print("Read cycles:", i + 1, flush=True)
    selected.add("forecast_runs", ["run_id"], [(d.get("run_id"),) for d in details])
    selected.add(
        "analysis_cycles", ["analysis_id"], [(d.get("analysis_id"),) for d in details]
    )
    selected.add("product_assets", ["product_id"], [(x,) for x in product_ids])
    selected.add("diagnostic_runs", ["job_id"], [(x,) for x in diagnostic_ids])
    selected.add("algorithm_runs", ["job_id"], [(x,) for x in bundle_ids])
    selected.dependencies()
    for table in ["analysis_cycle_radars", "qpe_runs", "mosaic_runs"]:
        selected.add(
            table,
            ["analysis_id"],
            [
                (r["analysis_id"],)
                for r in selected.rows.get("analysis_cycles", {}).values()
            ],
        )
    selected.dependencies()
    selected.add(
        "radar_scan_runs",
        ["scan_id"],
        [(r["scan_id"],) for r in selected.rows.get("radar_scans", {}).values()],
    )
    selected.dependencies()
    rows = {t: list(values.values()) for t, values in selected.rows.items()}
    if any(
        r["status"] not in ("SUCCEEDED", "FAILED", "SKIPPED")
        for r in rows.get("jobs", [])
    ):
        raise ValueError("referenced jobs are still active; retry after completion")
    if any(
        r["status"] not in ("SUCCEEDED", "FAILED", "CANCELLED")
        for r in rows.get("pipeline_regeneration_requests", [])
    ):
        raise ValueError(
            "referenced regeneration is still active; retry after completion"
        )
    check_secrets(rows)
    with tempfile.TemporaryDirectory(prefix="rainpulse-history-") as temp:
        stage = Path(temp)
        files = {}

        def save(path, data):
            target = safe_path(stage, path)
            if target.exists():
                if (
                    hashlib.sha256(target.read_bytes()).hexdigest()
                    != hashlib.sha256(data).hexdigest()
                ):
                    raise ValueError("artifact changed during export")
                return
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            files[path] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
            }

        def object_uri(uri, directory=False):
            parsed = urllib.parse.urlsplit(uri)
            if (
                parsed.scheme != "s3"
                or parsed.netloc != "rainpulse"
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("unexpected object URI; export refused")
            key = parsed.path.lstrip("/")
            # Never copy raw/normalized radar, input datasets or model artifacts.
            if not key.startswith(
                ("products/", "diagnostics/", "analysis/diagnostics/")
            ):
                raise ValueError(
                    "non-replay object prefix refused: " + key.split("/")[0]
                )
            keys = list(objects.keys(key.rstrip("/") + "/")) if directory else [key]
            if not keys:
                raise ValueError("published bundle is empty or no longer present")
            for item in keys:
                path = "objects/" + item
                if path not in files:
                    save(path, objects.request("GET", item))

        for asset in rows.get("product_assets", []):
            object_uri(asset["object_uri"])
            key = "objects/" + urllib.parse.urlsplit(asset["object_uri"]).path.lstrip(
                "/"
            )
            if (
                files[key]["sha256"] != asset["sha256"].strip()
                or files[key]["size"] != asset["size_bytes"]
            ):
                raise ValueError(
                    "product bytes differ from published database checksum"
                )
        for row in rows.get("diagnostic_runs", []):
            object_uri(row["bundle_uri"], directory=True)
        formal = set()
        for row in rows.get("algorithm_runs", []):
            if row["algorithm_id"] == "nowcastnet" and row["status"] == "completed":
                object_uri(row["output_uri"], directory=True)
                formal.add(row["job_id"])
        for kind, field, setting, default in [
            (
                "ensemble",
                "ensemble_bundle_id",
                "RAINPULSE_ENSEMBLE_PRODUCT_HOST_ROOT",
                "../runtime/products/ensemble",
            ),
            (
                "nowcastnet",
                "nowcastnet_bundle_id",
                "RAINPULSE_NOWCASTNET_PRODUCT_HOST_ROOT",
                "../runtime/products/nowcastnet",
            ),
        ]:
            root = Path(env.get(setting, default))
            if not root.is_absolute():
                root = args.root / "deploy" / root
            for bundle in sorted(
                {d[field] for d in details if d.get(field)}
                - (formal if kind == "nowcastnet" else set())
            ):
                directory = safe_path(root, bundle)
                manifest = json.loads((directory / "manifest.json").read_text())
                check_secrets(manifest)
                # Copy only files referenced by the validated current manifest.
                paths = {"manifest.json"}

                def collect(value):
                    if isinstance(value, dict):
                        for k, v in value.items():
                            if k == "object_path" and isinstance(v, str):
                                paths.add(v)
                            else:
                                collect(v)
                    elif isinstance(value, list):
                        for v in value:
                            collect(v)

                collect(manifest)
                for path in sorted(paths):
                    save(
                        "files/" + kind + "/" + bundle + "/" + path,
                        safe_path(directory, path).read_bytes(),
                    )
        # Re-check selected current IDs: concurrent recomputation must not silently
        # create a mixed-version case. Object disappearance also fails the export.
        fresh = select_day(args.api_base, args.date, args.utc_offset)
        fields = [
            "cycle_id",
            "analysis_id",
            "run_id",
            "ensemble_bundle_id",
            "nowcastnet_bundle_id",
        ]

        def fingerprint(xs):
            return sorted(tuple(x.get(k, "") for k in fields) for x in xs)

        if fingerprint(cycles) != fingerprint(fresh):
            raise ValueError(
                "current cycle products changed during export; retry when regeneration is idle"
            )
        save("database.json", encoded(rows).encode())
        save("cycles.json", encoded(cycles).encode())
        save("schema.json", encoded(schema).encode())
        save("history_case.py", Path(__file__).read_bytes())
        save(
            "import_history_case.sh",
            (args.root / "packaging/airgap/import_history_case.sh").read_bytes(),
        )
        manifest = {
            "format": FORMAT,
            "date": args.date,
            "utc_offset_hours": args.utc_offset,
            "mode": "replay-only",
            "cycle_count": len(cycles),
            "warnings": {
                d["cycle_id"]: d["warnings"] for d in details if d.get("warnings")
            },
            "row_counts": {t: len(rs) for t, rs in rows.items()},
            "files": files,
            "excluded": [
                "credentials",
                "raw radar",
                "model weights",
                "queues",
                "obsolete large products",
                "online basemap tiles",
            ],
        }
        (stage / "manifest.json").write_text(encoded(manifest))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        partial = args.output.with_suffix(".zip.partial")
        if partial.exists():
            raise ValueError("partial output exists; inspect before retry")
        try:
            with zipfile.ZipFile(
                partial,
                "x",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=1,
                allowZip64=True,
            ) as archive:
                for path in sorted(stage.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(stage).as_posix())
            partial.rename(args.output)
        finally:
            partial.unlink(missing_ok=True)
    digest = file_hash(args.output)
    Path(str(args.output) + ".sha256").write_text(
        digest + "  " + args.output.name + "\n"
    )
    print(
        "Created:",
        args.output,
        "cycles:",
        len(cycles),
        "bytes:",
        args.output.stat().st_size,
    )


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def row_order(rows, schema):
    """Parents first, ignoring only explicitly deferred FKs; never disable checks."""
    nodes, indexes = {}, {}
    for table, values in rows.items():
        if table not in TABLES or table not in schema["pk"]:
            raise ValueError("unrecognized case table")
        for row in values:
            if set(row) != set(schema["columns"][table]):
                raise ValueError("case row/schema mismatch")
            key = (table, tuple(row[k] for k in schema["pk"][table]))
            if key in nodes:
                raise ValueError("duplicate case row")
            nodes[key] = row
    for fk in schema["fks"]:
        indexes[(fk["parent"], tuple(fk["parent_keys"]))] = {
            tuple(row[k] for k in fk["parent_keys"]): key
            for key, row in nodes.items()
            if key[0] == fk["parent"]
        }
    emitted, active, output = set(), set(), []

    def visit(key):
        if key in emitted:
            return
        if key in active:
            raise ValueError("non-deferred metadata dependency cycle")
        active.add(key)
        for fk in schema["fks"]:
            if fk["child"] != key[0]:
                continue
            value = tuple(nodes[key][k] for k in fk["child_keys"])
            if any(v is None for v in value):
                continue
            parent = indexes[(fk["parent"], tuple(fk["parent_keys"]))].get(value)
            if parent is None:
                raise ValueError("case is missing a required metadata parent")
            if not fk["deferred"]:
                visit(parent)
        active.remove(key)
        emitted.add(key)
        output.append((key[0], nodes[key]))

    for key in nodes:
        visit(key)
    return output


def import_case(args):
    package = args.package.resolve()
    manifest = json.loads((package / "manifest.json").read_text())
    if manifest.get("format") != FORMAT or manifest.get("mode") != "replay-only":
        raise ValueError("unsupported history package")
    if not {"database.json", "schema.json", "cycles.json"}.issubset(
        manifest.get("files", {})
    ):
        raise ValueError("package metadata is not covered by checksums")
    for path, record in manifest["files"].items():
        file = safe_path(package, path)
        if file.stat().st_size != record["size"] or file_hash(file) != record["sha256"]:
            raise ValueError("package checksum mismatch: " + path)
    rows = json.loads((package / "database.json").read_text())
    schema = json.loads((package / "schema.json").read_text())
    validate_replay_rows(rows)
    ordered = row_order(rows, schema)
    if args.verify_only:
        print(
            "Verified package:",
            manifest["date"],
            "cycles:",
            manifest["cycle_count"],
            "files:",
            len(manifest["files"]),
        )
        return
    env = read_env(args.env_file)
    if not args.check_database and (
        env.get("RAINPULSE_RADAR_INGEST_ENABLED", "false").lower() != "false"
        or env.get("RAINPULSE_PIPELINE_ENABLED", "false").lower() != "false"
    ):
        raise ValueError(
            "disable ingest/pipeline and restart base stack before importing"
        )
    db, objects = Database(args), Objects(env)
    if args.check_database and args.database == "rainpulse":
        raise ValueError(
            "database acceptance checks require an isolated empty database name"
        )
    if not args.check_database:
        db.require_stopped_readers()
    current = db.schema()
    for table in rows:
        if current["columns"].get(table) != schema["columns"].get(table):
            raise ValueError(
                "database schema differs; install matching program package first"
            )
    # Use the target constraints, not caller-supplied dependency ordering.
    ordered = row_order(rows, current)
    # Fresh case store only. Never truncate, replace, or disable FK constraints.
    occupied = db.query(
        "SELECT (SELECT count(*) FROM analysis_cycles)+(SELECT count(*) FROM forecast_runs)+(SELECT count(*) FROM products) AS n"
    )[0]["n"]
    if occupied:
        raise ValueError(
            "target already contains case data; refusing to merge or overwrite"
        )
    sql = ["BEGIN;", "SET CONSTRAINTS ALL DEFERRED;"]
    for table, row in ordered:
        if table in REFERENCE_TABLES:
            predicate = " AND ".join(
                ident(k) + "::text=" + quote(row[k]) for k in schema["pk"][table]
            )
            old = db.query("SELECT * FROM " + ident(table) + " WHERE " + predicate)
            if old:
                stable = set(row) - {"created_at", "updated_at"}
                if any(old[0][k] != row[k] for k in stable):
                    raise ValueError("reference metadata conflicts: " + table)
                continue
        sql.append(
            "INSERT INTO "
            + ident(table)
            + " SELECT * FROM json_populate_record(NULL::"
            + ident(table)
            + ","
            + quote(encoded(row))
            + "::json);"
        )
    sql.append("COMMIT;")
    if args.check_database:
        sql[-1] = "ROLLBACK;"
        db.run("\n".join(sql))
        print(
            "Database constraints verified; transaction rolled back; no objects copied."
        )
        return
    # Upload immutable derived artifacts first. A failed SQL transaction leaves
    # no visible case; retries reuse byte-identical artifacts, never overwrite.
    for relative in manifest["files"]:
        source = safe_path(package, relative)
        if relative.startswith("objects/"):
            key = relative[len("objects/") :]
            if not key.startswith(
                ("products/", "diagnostics/", "analysis/diagnostics/")
            ):
                raise ValueError("non-replay object import refused")
            try:
                old = objects.request("GET", key)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                old = None
            if old is not None:
                if (
                    hashlib.sha256(old).hexdigest()
                    != manifest["files"][relative]["sha256"]
                ):
                    raise ValueError("target object conflict; refusing overwrite")
            else:
                objects.request("PUT", key, data=source.read_bytes())
        elif relative.startswith("files/"):
            _, kind, tail = relative.split("/", 2)
            if kind not in ("ensemble", "nowcastnet"):
                raise ValueError("unknown case file kind")
            setting = "RAINPULSE_" + kind.upper() + "_PRODUCT_HOST_ROOT"
            root = Path(env.get(setting, "../runtime/products/" + kind))
            if not root.is_absolute():
                root = args.root / "deploy" / root
            dest = safe_path(root, tail)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                if file_hash(dest) != manifest["files"][relative]["sha256"]:
                    raise ValueError("target product file conflict")
            else:
                with dest.open("xb") as file:
                    with source.open("rb") as original:
                        shutil.copyfileobj(original, file)
    db.run("\n".join(sql))
    print(
        "Imported replay metadata and current artifacts. Restart API to clear catalog caches, then verify the selected local date in Web."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["export", "import"])
    parser.add_argument(
        "--root", type=Path, default=Path.cwd(), help="installed/source RainPulse root"
    )
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--date", default="2026-08-28")
    parser.add_argument(
        "--utc-offset",
        type=int,
        default=8,
        help="local calendar date UTC offset (default Beijing time)",
    )
    parser.add_argument("--api-base", default="http://127.0.0.1:8080")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--database",
        default="rainpulse",
        help="database name; override only for isolated acceptance checks",
    )
    parser.add_argument(
        "--check-database",
        action="store_true",
        help="validate metadata against an empty database, then roll back; no object writes",
    )
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.env_file = (args.env_file or args.root / "deploy/.env").resolve()
    args.output = (
        args.output or args.root / ".build" / ("rainpulse-case-" + args.date + ".zip")
    ).resolve()
    try:
        export_case(args) if args.mode == "export" else import_case(args)
    except (
        ValueError,
        RuntimeError,
        OSError,
        urllib.error.URLError,
        KeyError,
    ) as error:
        # Keep HTTP exceptions sanitized (authorization is never printed).
        raise SystemExit("History package failed: " + str(error)) from None


if __name__ == "__main__":
    main()
