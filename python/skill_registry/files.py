"""Verified file-content store for multi-file skills. Stdlib only.

Manifests carry file *metadata* (path/sha256/size); this store carries the
bytes. Every read re-verifies content against the pinned sha256. Disk
payloads are base64 so binary files survive exactly.
"""
import base64
import hashlib
import json
from pathlib import Path

PER_FILE_LIMIT = 10_000_000
BUNDLE_LIMIT = 100_000_000


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
class FileStore:
    def __init__(self, root: str | Path | None = None):
        self._mem: dict[tuple[str, str | None, str], bytes] = {}
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    def _disk_path(self, skill_id: str, path: str,
                   version: str | None = None) -> Path | None:
        if not self.root:
            return None
        prefix = "__".join(skill_id.split("/"))
        if version is not None:
            prefix += "__v__" + version.replace("/", "_").replace("\\", "_")
        safe = prefix + "__" + "__".join(Path(path).parts)
        return self.root / (safe + ".json")

    def put(self, skill_id: str, path: str, contents: str | bytes,
            version: str | None = None) -> dict:
        raw = contents.encode() if isinstance(contents, str) else contents
        if len(raw) > PER_FILE_LIMIT:
            raise ValueError(f"file too large: {path} ({len(raw)} bytes)")
        previous = self._mem.get((skill_id, version, path))
        total = (sum(len(v) for (sid, ver, _), v in self._mem.items()
                     if sid == skill_id and ver == version)
                 - (len(previous) if previous is not None else 0) + len(raw))
        if total > BUNDLE_LIMIT:
            raise ValueError(f"bundle too large: {skill_id}")
        self._mem[(skill_id, version, path)] = raw
        meta = {"path": path, "sha256": sha256_bytes(raw), "size": len(raw)}
        disk = self._disk_path(skill_id, path, version)
        if disk:
            disk.write_text(json.dumps({
                "skill_id": skill_id, "version": version, **meta,
                "contents_b64": base64.b64encode(raw).decode(),
            }))
        return meta

    def put_bundle(self, skill_id: str, version: str,
                   files: list[tuple[str, str | bytes]]) -> list[dict]:
        """Preflight and store a complete package without partial size failures."""
        prepared: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        for path, contents in files:
            if path in seen:
                raise ValueError(f"duplicate file: {path}")
            seen.add(path)
            raw = contents.encode() if isinstance(contents, str) else contents
            if len(raw) > PER_FILE_LIMIT:
                raise ValueError(f"file too large: {path} ({len(raw)} bytes)")
            prepared.append((path, raw))
        retained = sum(
            len(raw) for (sid, ver, path), raw in self._mem.items()
            if sid == skill_id and ver == version and path not in seen
        )
        total = retained + sum(len(raw) for _, raw in prepared)
        if total > BUNDLE_LIMIT:
            raise ValueError(f"bundle too large: {skill_id} ({total} bytes)")
        return [self.put(skill_id, path, raw, version=version)
                for path, raw in prepared]

    def get_bytes(self, skill_id: str, path: str,
                  expected_sha: str | None = None,
                  version: str | None = None) -> bytes | None:
        raw = self._mem.get((skill_id, version, path))
        disk = self._disk_path(skill_id, path, version)
        # Read legacy, unversioned payloads so existing registries upgrade in place.
        if raw is None and version is not None and (disk is None or not disk.exists()):
            raw = self._mem.get((skill_id, None, path))
            disk = self._disk_path(skill_id, path, None)
        if raw is None and disk and disk.exists():
            doc = json.loads(disk.read_text())
            if "contents_b64" in doc:
                raw = base64.b64decode(doc["contents_b64"])
            else:  # legacy text payload
                raw = doc["contents"].encode()
            self._mem[(skill_id, version, path)] = raw
        if raw is None:
            return None
        if expected_sha and sha256_bytes(raw) != expected_sha:
            return None
        return raw

    def get(self, skill_id: str, path: str, expected_sha: str | None = None,
            version: str | None = None) -> dict | None:
        raw = self.get_bytes(skill_id, path, expected_sha, version)
        if raw is None:
            return None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {"path": path, "sha256": sha256_bytes(raw), "size": len(raw),
                "contents": text,
                "contents_b64": base64.b64encode(raw).decode("ascii")}
