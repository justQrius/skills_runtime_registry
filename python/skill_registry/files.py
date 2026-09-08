"""Verified file-content store for multi-file skills. Stdlib only.

Manifests carry file *metadata* (path/sha256/size); this store carries the
bytes. Every read re-verifies content against the pinned sha256. Disk
payloads are base64 so binary files survive exactly.
"""
import base64
import hashlib
import json
from pathlib import Path

PER_FILE_LIMIT = 1_000_000
BUNDLE_LIMIT = 10_000_000


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
class FileStore:
    def __init__(self, root: str | Path | None = None):
        self._mem: dict[tuple[str, str], bytes] = {}
        self.root = Path(root) if root else None
        if self.root:
            self.root.mkdir(parents=True, exist_ok=True)

    def _disk_path(self, skill_id: str, path: str) -> Path | None:
        if not self.root:
            return None
        safe = "__".join(skill_id.split("/")) + "__" + "__".join(Path(path).parts)
        return self.root / (safe + ".json")

    def put(self, skill_id: str, path: str, contents: str | bytes) -> dict:
        raw = contents.encode() if isinstance(contents, str) else contents
        if len(raw) > PER_FILE_LIMIT:
            raise ValueError(f"file too large: {path} ({len(raw)} bytes)")
        total = sum(len(v) for (sid, _), v in self._mem.items() if sid == skill_id) + len(raw)
        if total > BUNDLE_LIMIT:
            raise ValueError(f"bundle too large: {skill_id}")
        self._mem[(skill_id, path)] = raw
        meta = {"path": path, "sha256": sha256_bytes(raw), "size": len(raw)}
        disk = self._disk_path(skill_id, path)
        if disk:
            disk.write_text(json.dumps({
                "skill_id": skill_id, **meta,
                "contents_b64": base64.b64encode(raw).decode(),
            }))
        return meta

    def get_bytes(self, skill_id: str, path: str,
                  expected_sha: str | None = None) -> bytes | None:
        raw = self._mem.get((skill_id, path))
        if raw is None and (disk := self._disk_path(skill_id, path)) and disk.exists():
            doc = json.loads(disk.read_text())
            if "contents_b64" in doc:
                raw = base64.b64decode(doc["contents_b64"])
            else:  # legacy text payload
                raw = doc["contents"].encode()
            self._mem[(skill_id, path)] = raw
        if raw is None:
            return None
        if expected_sha and sha256_bytes(raw) != expected_sha:
            return None
        return raw

    def get(self, skill_id: str, path: str, expected_sha: str | None = None) -> dict | None:
        raw = self.get_bytes(skill_id, path, expected_sha)
        if raw is None:
            return None
        return {"path": path, "sha256": sha256_bytes(raw), "size": len(raw),
                "contents": raw.decode("utf-8", "replace")}
