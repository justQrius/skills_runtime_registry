"""Manifest normalize + validate (stdlib only, mirrors schema/manifest.schema.json)."""
import re

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)([-+].*)?$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$")
_MODES = {"instruction", "tool", "workflow", "executable"}
_FS = {"none", "read-only", "read-write"}


def normalize(m: dict) -> dict:
    m = dict(m)
    # Accept singular execution_mode from PRD appendix for compat.
    if "execution_mode" in m and "execution_modes" not in m:
        m["execution_modes"] = [m["execution_mode"]]
    m.pop("execution_mode", None)
    m.setdefault("topics", [])
    m.setdefault("tags", [])
    m.setdefault("pack", None)
    m.setdefault("compatibility", {})
    m["compatibility"].setdefault("agent_classes", [])
    m["compatibility"].setdefault("harnesses", [])
    m.setdefault("input_schema", {})
    m.setdefault("output_schema", {})
    m.setdefault("tool_dependencies", [])
    m.setdefault("permissions", {})
    m["permissions"].setdefault("network", False)
    m["permissions"].setdefault("filesystem", "none")
    m.setdefault("trust", {})
    for k, d in (("official", False), ("audited", False), ("audit_ref", None), ("revoked", False)):
        m["trust"].setdefault(k, d)
    # publisher.official mirrors trust.official if absent
    pub = m.get("publisher", {})
    if "official" not in pub and "official" in m["trust"]:
        pub["official"] = m["trust"]["official"]
    m["publisher"] = pub
    m.setdefault("integrity", {})
    m["integrity"].setdefault("sha256", None)
    m.setdefault("cache", {})
    m["cache"].setdefault("ttl_seconds", 3600)
    m["cache"].setdefault("pin_recommended", False)
    m.setdefault("deprecation", {})
    m["deprecation"].setdefault("deprecated", False)
    m["deprecation"].setdefault("replaced_by", None)
    m.setdefault("popularity", 0)
    m.setdefault("updated_at", None)
    m.setdefault("artifact", {})
    m["artifact"].setdefault("instruction", None)
    m["artifact"].setdefault("tool_ref", None)
    m["artifact"].setdefault("files", [])
    return m


def validate(m: dict) -> list[str]:
    """Return list of errors; empty means valid."""
    errs: list[str] = []
    _ALLOWED = {
        "skill_id", "version", "name", "description", "publisher",
        "topics", "tags", "pack", "execution_modes", "compatibility",
        "input_schema", "output_schema", "tool_dependencies", "permissions",
        "trust", "integrity", "cache", "deprecation", "popularity",
        "updated_at", "artifact",
    }
    for k in m:
        if k not in _ALLOWED:
            errs.append(f"unknown field: {k}")
    for f in ("skill_id", "version", "name", "description", "publisher", "execution_modes"):
        if f not in m:
            errs.append(f"missing: {f}")
    if errs:
        return errs
    if not _ID_RE.match(m["skill_id"]):
        errs.append("bad skill_id")
    if not _VERSION_RE.match(m["version"]):
        errs.append("bad version")
    if not m["name"] or not m["description"]:
        errs.append("name/description empty")
    pub = m.get("publisher", {})
    if not isinstance(pub, dict) or not pub.get("id"):
        errs.append("bad publisher.id")
    modes = m.get("execution_modes", [])
    if not modes or not set(modes) <= _MODES:
        errs.append(f"bad execution_modes: {modes}")
    fs = m.get("permissions", {}).get("filesystem", "none")
    if fs not in _FS:
        errs.append(f"bad permissions.filesystem: {fs}")
    if m.get("trust", {}).get("revoked") is True and m.get("integrity", {}).get("sha256") is None:
        errs.append("revoked requires integrity.sha256")
    return errs
