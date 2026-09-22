"""Verified manifest sessions with selective reads and immutable byte reuse.

Schema 1/2 manifests commit to every logical object's hash, so a selected subset
can be verified against the exact whole-artifact identity without downloading
unrelated objects. Schema 3 commits to physical packs, but has no per-logical-
object hashes: selected reads deliberately fall back to full verification.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .asset_cache import ObjectIdentity, VerifiedObjectCache, process_asset_cache

MAX_MARKER_BYTES = 16 * 1024**2
MAX_OBJECTS = 100_000
_SHA = re.compile(r"^[0-9a-f]{64}$")


def artifact_digest(objects: Mapping[str, bytes]) -> str:
    return manifest_digest(
        Entry(key, len(value), hashlib.sha256(value).hexdigest())
        for key, value in objects.items()
    )


@dataclass(frozen=True)
class Entry:
    key: str
    size: int
    sha256: str


def manifest_digest(entries: Iterable[Entry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.key):
        key = entry.key.encode()
        digest.update(len(key).to_bytes(4, "big"))
        digest.update(key)
        digest.update(entry.size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(entry.sha256))
    return digest.hexdigest()


def _key(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise RuntimeError("published artifact has an invalid key")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or str(path) != value
            or value in (".", "_SUCCESS.json")):
        raise RuntimeError("published artifact has an unsafe key")
    return value


def _sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise RuntimeError("published artifact has an invalid SHA-256")
    return value


def _uri(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    key = parsed.path.strip("/")
    if (parsed.scheme != "s3" or not parsed.netloc or parsed.username is not None
            or parsed.query or parsed.fragment):
        raise ValueError("expected an s3 artifact URI")
    return parsed.netloc, _key(key)


def _unique_json(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError("published artifact marker repeats a JSON property")
        result[key] = value
    return result


class ManifestIndex:
    def __init__(self, raw: bytes, maximum: int, expected_sha256: str | None):
        if len(raw) > MAX_MARKER_BYTES:
            raise RuntimeError("artifact marker exceeds reader size limit")
        try:
            marker = json.loads(raw, object_pairs_hook=_unique_json)
        except (ValueError, RecursionError) as error:
            raise RuntimeError("unreadable artifact marker") from error
        if not isinstance(marker, dict):
            raise RuntimeError("published artifact marker must be an object")
        self.sha256 = _sha(marker.get("sha256"))
        if expected_sha256 is not None and self.sha256 != _sha(expected_sha256):
            raise RuntimeError("published artifact identity differs from requested SHA-256")
        self.schema = marker.get("schema_version", "1.0")
        if self.schema not in ("1.0", "2.0", "3.0"):
            raise RuntimeError("unsupported artifact marker schema")
        prefix = marker.get("data_prefix", "")
        self.data_prefix = _key(prefix) if prefix else ""
        # Current immutable publisher embeds the digest in this path. Other
        # historical relative prefixes remain supported but are never trusted
        # in place of manifest/object checksum verification.
        if (self.data_prefix.startswith("_objects/")
                and self.data_prefix != f"_objects/{self.sha256}"):
            raise RuntimeError("artifact content prefix differs from bundle SHA-256")
        size = marker.get("size_bytes")
        if type(size) is not int or size < 0:
            raise RuntimeError("published artifact marker has an invalid size")
        if size > maximum:
            raise RuntimeError("published artifact exceeds the configured input byte limit")
        self.size = size
        entries = marker.get("objects")
        if not isinstance(entries, list) or not 0 < len(entries) <= MAX_OBJECTS:
            raise RuntimeError("published artifact marker has no bounded object manifest")
        self.physical: dict[str, Entry] = {}
        for item in entries:
            if not isinstance(item, dict) or type(item.get("size_bytes")) is not int:
                raise RuntimeError("invalid artifact manifest entry")
            key, count, sha = _key(item.get("key")), item["size_bytes"], _sha(item.get("sha256"))
            if count < 0 or count > maximum or key in self.physical:
                raise RuntimeError("invalid artifact size or duplicate object")
            self.physical[key] = Entry(key, count, sha)
        if sum(item.size for item in self.physical.values()) != size:
            raise RuntimeError("published artifact marker has inconsistent size metadata")
        self.logical: dict[str, tuple[str, int, int]] = {}
        if self.schema == "3.0":
            packed = marker.get("packed_entries")
            if not isinstance(packed, list) or not 0 < len(packed) <= MAX_OBJECTS:
                raise RuntimeError("invalid packed artifact index")
            positions = dict.fromkeys(self.physical, 0)
            for item in packed:
                if not isinstance(item, list) or len(item) != 4:
                    raise RuntimeError("invalid packed artifact entry")
                key, pack, offset, count = item
                key = _key(key)
                if (not isinstance(pack, str) or pack not in self.physical or key in self.logical
                        or type(offset) is not int or type(count) is not int or count < 0
                        or offset != positions[pack]
                        or offset + count > self.physical[pack].size):
                    raise RuntimeError("invalid packed artifact bounds or duplicate key")
                self.logical[key] = (pack, offset, count)
                positions[pack] += count
            if any(positions[key] != entry.size for key, entry in self.physical.items()):
                raise RuntimeError("packed artifact has unreferenced bytes")
        else:
            if "packed_entries" in marker:
                raise RuntimeError("packed artifact requires schema 3.0")
            self.logical = {key: (key, 0, item.size) for key, item in self.physical.items()}
            if manifest_digest(self.physical.values()) != self.sha256:
                raise RuntimeError("published artifact bundle checksum differs")

    def select(self, keys: Iterable[str] | None, prefixes: Iterable[str] | None) -> list[str]:
        if keys is None and prefixes is None:
            return sorted(self.logical)
        if isinstance(keys, str) or isinstance(prefixes, str):
            raise ValueError("selection requires sequences, not one string")
        explicit = set(_key(key) for key in (keys or ()))
        missing = explicit - self.logical.keys()
        if missing:
            raise KeyError("requested artifact object is absent: " + sorted(missing)[0])
        for prefix in prefixes or ():
            if not isinstance(prefix, str):
                raise ValueError("artifact prefix must be a string")
            prefix = _key(prefix.rstrip("/"))
            matches = {key for key in self.logical if key == prefix or key.startswith(prefix + "/")}
            if not matches:
                raise KeyError("requested artifact prefix is absent: " + prefix)
            explicit.update(matches)
        if not explicit:
            raise ValueError("artifact selection must not be empty")
        return sorted(explicit)


class ArtifactSession:
    """One validated manifest snapshot; repeat selections cannot mix revisions."""
    def __init__(self, *, index: ManifestIndex, bucket: str, prefix: str,
                 namespace: str, read_bytes: Callable[[str, str, int], bytes],
                 cache: VerifiedObjectCache, workers: int):
        self.index, self.bucket, self.prefix = index, bucket, prefix
        self.namespace, self.read_bytes = namespace, read_bytes
        self.cache, self.workers = cache, workers
        self._lock = threading.Lock()
        self.stats = {"object_gets": 0, "download_bytes": 0, "cache_hits": 0,
                      "shared_reads": 0, "selected_objects": 0, "selected_bytes": 0,
                      "packed_full_fallback": 0}

    def _read_entry(self, entry: Entry) -> tuple[str, bytes]:
        path = "/".join(filter(None, (self.prefix, self.index.data_prefix, entry.key)))
        identity = ObjectIdentity(self.namespace, self.bucket, path, entry.sha256, entry.size)
        loaded = self.cache.read(identity, lambda: self.read_bytes(self.bucket, path, entry.size))
        with self._lock:
            if loaded.source == "store":
                self.stats["object_gets"] += 1
                self.stats["download_bytes"] += len(loaded.data)
            elif loaded.source == "cache":
                self.stats["cache_hits"] += 1
            else:
                self.stats["shared_reads"] += 1
        return entry.key, loaded.data

    def load(self, *, keys: Iterable[str] | None = None,
             prefixes: Iterable[str] | None = None) -> dict[str, bytes]:
        selected = self.index.select(keys, prefixes)
        if self.index.schema == "3.0":
            # Legacy pack index has no logical-object hashes. Full verification
            # is required even when only one key was requested.
            to_read = sorted(self.index.physical)
            if len(selected) != len(self.index.logical):
                self.stats["packed_full_fallback"] += 1
        else:
            to_read = selected
        physical = dict(_parallel(
            [self.index.physical[key] for key in to_read], self._read_entry, self.workers
        ))
        if self.index.schema == "3.0":
            # Do not materialize all unpacked bytes: hash views over each pack
            # and only copy selected logical objects into the returned mapping.
            digest = hashlib.sha256()
            for key in sorted(self.index.logical):
                pack, offset, size = self.index.logical[key]
                value = memoryview(physical[pack])[offset:offset + size]
                encoded = key.encode()
                digest.update(len(encoded).to_bytes(4, "big"))
                digest.update(encoded)
                digest.update(size.to_bytes(8, "big"))
                digest.update(hashlib.sha256(value).digest())
            if digest.hexdigest() != self.index.sha256:
                raise RuntimeError("published artifact bundle checksum differs")
            result = {}
            for key in selected:
                pack, offset, size = self.index.logical[key]
                result[key] = bytes(memoryview(physical[pack])[offset:offset + size])
        else:
            result = {key: physical[key] for key in selected}
        self.stats["selected_objects"] += len(result)
        self.stats["selected_bytes"] += sum(map(len, result.values()))
        return result


def _parallel(items: list[Entry], function: Callable, workers: int) -> list[tuple[str, bytes]]:
    if workers == 1 or len(items) <= 1:
        return [function(item) for item in items]
    result = []
    iterator = iter(items)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="rp-asset") as pool:
        pending = {pool.submit(function, entry) for entry in items[:workers]}
        for _ in range(min(workers, len(items))):
            next(iterator)
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            try:
                for future in done:
                    result.append(future.result())
                    entry = next(iterator, None)
                    if entry is not None:
                        pending.add(pool.submit(function, entry))
            except BaseException:
                for future in pending:
                    future.cancel()
                raise
    return result


class VerifiedArtifactReader:
    def __init__(self, read_bytes: Callable[[str, str, int], bytes], *, namespace: str,
                 maximum: int = 2 * 1024**3, workers: int = 4,
                 cache: VerifiedObjectCache | None = None):
        if type(maximum) is not int or maximum <= 0:
            raise ValueError("artifact input byte limit must be positive")
        if type(workers) is not int or not 1 <= workers <= 32:
            raise ValueError("artifact workers must be between 1 and 32")
        if not namespace:
            raise ValueError("artifact reader namespace is required")
        self.read_bytes, self.namespace = read_bytes, namespace
        self.maximum, self.workers = maximum, workers
        self.cache = cache if cache is not None else process_asset_cache()
        self.last_session: ArtifactSession | None = None

    def open(self, uri: str, *, expected_sha256: str | None = None) -> ArtifactSession:
        bucket, prefix = _uri(uri)
        # Never use a cached success marker: a missing, corrupt or replaced
        # marker must be visible even if verified object bytes remain cached.
        raw = self.read_bytes(bucket, prefix + "/_SUCCESS.json", MAX_MARKER_BYTES)
        index = ManifestIndex(raw, self.maximum, expected_sha256)
        session = ArtifactSession(index=index, bucket=bucket, prefix=prefix,
                                  namespace=self.namespace, read_bytes=self.read_bytes,
                                  cache=self.cache, workers=self.workers)
        self.last_session = session
        return session

    def load(self, artifact_uri: str, *, expected_sha256: str | None = None) -> dict[str, bytes]:
        return self.open(artifact_uri, expected_sha256=expected_sha256).load()

    def load_selected(self, artifact_uri: str, *, keys: Iterable[str] | None = None,
                      prefixes: Iterable[str] | None = None,
                      expected_sha256: str | None = None) -> dict[str, bytes]:
        if keys is None and prefixes is None:
            raise ValueError("load_selected requires keys or prefixes")
        return self.open(artifact_uri, expected_sha256=expected_sha256).load(
            keys=keys, prefixes=prefixes
        )
