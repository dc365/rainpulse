import hashlib
import json
import threading

from core_modules import access, cache


class Store:
    def __init__(self, objects, *, packed=False, pack_size=32, prefix="asset"):
        self.prefix, self.calls, self.lock = prefix, [], threading.Lock()
        physical, entries = {}, []
        if packed:
            buf = bytearray()
            name = "packs/0000.bin"
            for key, value in sorted(objects.items()):
                if buf and len(buf) + len(value) > pack_size:
                    physical[name] = bytes(buf); buf.clear()
                    name = f"packs/{len(physical):04d}.bin"
                entries.append([key, name, len(buf), len(value)])
                buf.extend(value)
            physical[name] = bytes(buf)
        else:
            physical = dict(objects)
        digest = access.artifact_digest(objects)
        self.marker = {
            "schema_version": "3.0" if packed else "2.0", "sha256": digest,
            "size_bytes": sum(map(len, objects.values())), "data_prefix": "_objects/" + digest,
            "objects": [{"key": k, "size_bytes": len(v), "sha256": hashlib.sha256(v).hexdigest()}
                        for k, v in sorted(physical.items())],
        }
        if packed: self.marker["packed_entries"] = entries
        self.data = {prefix + "/" + self.marker["data_prefix"] + "/" + k: v for k, v in physical.items()}
        self.update_marker()

    def update_marker(self):
        self.data[self.prefix + "/_SUCCESS.json"] = json.dumps(self.marker).encode()

    def read(self, bucket, key, maximum):
        with self.lock: self.calls.append((bucket, key))
        value = self.data[key]
        if len(value) > maximum: raise RuntimeError("read exceeds declared size")
        return value

    def reader(self, *, maximum=2 * 1024**3, retained=1024**2, scope="test", workers=2, shared=None):
        shared = shared or cache.VerifiedObjectCache(cache.CacheLimits(max_bytes=retained))
        return access.VerifiedArtifactReader(self.read, namespace=scope, maximum=maximum,
                                            workers=workers, cache=shared)

    @property
    def object_gets(self):
        return [k for _, k in self.calls if not k.endswith("/_SUCCESS.json")]
