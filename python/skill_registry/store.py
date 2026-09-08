"""In-memory + file-backed store. Key: (skill_id, version)."""
import json
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
        return sorted(cands, key=lambda m: m["version"])[-1]
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
            "input_schema": m.get("input_schema", {}),
            "output_schema": m.get("output_schema", {}),
        }
