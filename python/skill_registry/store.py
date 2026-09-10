"""In-memory + file-backed store. Key: (skill_id, version)."""
import json
import re
from pathlib import Path
from .manifest import normalize, validate


class Registry:
    def __init__(self):
        self._items: dict[tuple[str, str], dict] = {}

    def add(self, raw: dict) -> dict:
        m = normalize(raw)
        errs = validate(m)
        if errs:
            raise ValueError("; ".join(errs))
        self._items[(m["skill_id"], m["version"])] = m
        return m

    def load_dir(self, d: str | Path) -> int:
        n = 0
        for p in Path(d).glob("*.json"):
            self.add(json.loads(p.read_text()))
            n += 1
        return n

    def all(self) -> list[dict]:
        return list(self._items.values())

    def get(self, skill_id: str, version: str | None = None) -> dict | None:
        if version:
            return self._items.get((skill_id, version))
        cands = [m for (sid, _), m in self._items.items() if sid == skill_id]
        if not cands:
            return None
        return max(cands, key=_manifest_version_key)

    def versions(self, skill_id: str) -> list[str]:
        """Return known versions newest first using semantic-version precedence."""
        versions = [version for sid, version in self._items if sid == skill_id]
        return sorted(versions, key=_semver_key, reverse=True)
    def get_artifact(self, skill_id: str, version: str | None = None) -> dict | None:
        m = self.get(skill_id, version)
        if m is None:
            return None
        return {
            "skill_id": m["skill_id"],
            "version": m["version"],
            "execution_modes": m.get("execution_modes", []),
            "instruction": m.get("artifact", {}).get("instruction"),
            "tool_ref": m.get("artifact", {}).get("tool_ref"),
            "workflow": m.get("artifact", {}).get("workflow"),
            "source_hash": m.get("artifact", {}).get("source_hash"),
            "input_schema": m.get("input_schema", {}),
            "output_schema": m.get("output_schema", {}),
            "files": m.get("artifact", {}).get("files", []),
            "entrypoints": m.get("artifact", {}).get("entrypoints", []),
        }


_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)(?:-(?P<pre>[^+]+))?(?:\+(?P<build>.*))?$"
)


def _semver_key(version: str) -> tuple:
    match = _SEMVER_RE.match(version)
    if not match:
        return (-1, -1, -1, 0, (), version)
    pre = match.group("pre")
    pre_key = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in (pre.split(".") if pre else ())
    )
    return (
        int(match.group("major")), int(match.group("minor")), int(match.group("patch")),
        1 if pre is None else 0, pre_key, match.group("build") or "",
    )


def _manifest_version_key(manifest: dict) -> tuple:
    return (_semver_key(manifest["version"]), manifest.get("updated_at") or "")
