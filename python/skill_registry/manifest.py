"""Manifest normalize + validate (stdlib only, mirrors schema/manifest.schema.json)."""
import copy
import re
from pathlib import PurePosixPath

from .runtimes import is_supported_entrypoint

_VERSION_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)([-+].*)?$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$")
_MODES = {"instruction", "tool", "workflow", "executable"}
_FS = {"none", "read-only", "read-write"}


def normalize(m: dict) -> dict:
    m = copy.deepcopy(m)
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
    id_parts = str(m.get("skill_id", "")).split("/")
    legacy_publisher = "/".join(id_parts[:-1])
    if len(id_parts) >= 3 and pub.get("id") == legacy_publisher:
        pub["id"] = id_parts[0]
        if m.get("pack") is None:
            m["pack"] = "/".join(id_parts[:2])
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
    m["artifact"].setdefault("workflow", None)
    m["artifact"].setdefault("files", [])
    m["artifact"].setdefault(
        "entrypoints",
        [f.get("path") for f in m["artifact"]["files"]
         if isinstance(f, dict) and isinstance(f.get("path"), str)
         and is_supported_entrypoint(f["path"])],
    )
    return m


def _is_non_negative_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _string_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _safe_relative_path(path: str) -> bool:
    if not isinstance(path, str) or not path or "\\" in path:
        return False
    if path.startswith("/") or (len(path) > 1 and path[1] == ":"):
        return False
    parts = PurePosixPath(path).parts
    return ".." not in parts and "." not in parts


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
    if not isinstance(m["skill_id"], str) or not _ID_RE.match(m["skill_id"]):
        errs.append("bad skill_id")
    if not isinstance(m["version"], str) or not _VERSION_RE.match(m["version"]):
        errs.append("bad version")
    if not isinstance(m["name"], str) or not isinstance(m["description"], str) or not m["name"] or not m["description"]:
        errs.append("name/description empty")
    pub = m.get("publisher", {})
    if not isinstance(pub, dict) or not pub.get("id"):
        errs.append("bad publisher.id")
    elif not isinstance(pub.get("id"), str):
        errs.append("bad publisher.id")
    else:
        for key in ("verified", "official"):
            if key in pub and not isinstance(pub[key], bool):
                errs.append(f"publisher.{key} must be boolean")
    modes = m.get("execution_modes", [])
    if not _string_list(modes) or not modes or not set(modes) <= _MODES:
        errs.append(f"bad execution_modes: {modes}")
    elif len(modes) != len(set(modes)):
        errs.append("duplicate execution_modes")

    for field in ("topics", "tags", "tool_dependencies"):
        if not _string_list(m.get(field, [])):
            errs.append(f"{field} must be an array of strings")
    pack = m.get("pack")
    if pack is not None and not isinstance(pack, str):
        errs.append("pack must be string or null")
    updated = m.get("updated_at")
    if updated is not None and not isinstance(updated, str):
        errs.append("updated_at must be string or null")
    if not _is_non_negative_int(m.get("popularity", 0)):
        errs.append("popularity must be a non-negative integer")

    compatibility = m.get("compatibility", {})
    if not isinstance(compatibility, dict):
        errs.append("compatibility must be an object")
    else:
        for field in ("agent_classes", "harnesses"):
            if not _string_list(compatibility.get(field, [])):
                errs.append(f"compatibility.{field} must be an array of strings")
    for field in ("input_schema", "output_schema"):
        if not isinstance(m.get(field, {}), dict):
            errs.append(f"{field} must be an object")

    permissions = m.get("permissions", {})
    if not isinstance(permissions, dict):
        errs.append("permissions must be an object")
    else:
        if not isinstance(permissions.get("network", False), bool):
            errs.append("permissions.network must be boolean")
        fs = permissions.get("filesystem", "none")
        if fs not in _FS:
            errs.append(f"bad permissions.filesystem: {fs}")

    trust = m.get("trust", {})
    if not isinstance(trust, dict):
        errs.append("trust must be an object")
        trust = {}
    else:
        for key in ("official", "audited", "revoked"):
            if not isinstance(trust.get(key, False), bool):
                errs.append(f"trust.{key} must be boolean")
        if trust.get("audit_ref") is not None and not isinstance(trust.get("audit_ref"), str):
            errs.append("trust.audit_ref must be string or null")

    integrity = m.get("integrity", {})
    if not isinstance(integrity, dict):
        errs.append("integrity must be an object")
        integrity = {}
    elif integrity.get("sha256") is not None and not isinstance(integrity.get("sha256"), str):
        errs.append("integrity.sha256 must be string or null")
    if trust.get("revoked") is True and integrity.get("sha256") is None:
        errs.append("revoked requires integrity.sha256")

    cache = m.get("cache", {})
    if not isinstance(cache, dict):
        errs.append("cache must be an object")
    else:
        if not _is_non_negative_int(cache.get("ttl_seconds", 3600)):
            errs.append("cache.ttl_seconds must be a non-negative integer")
        if not isinstance(cache.get("pin_recommended", False), bool):
            errs.append("cache.pin_recommended must be boolean")
    deprecation = m.get("deprecation", {})
    if not isinstance(deprecation, dict):
        errs.append("deprecation must be an object")
    else:
        if not isinstance(deprecation.get("deprecated", False), bool):
            errs.append("deprecation.deprecated must be boolean")
        if deprecation.get("replaced_by") is not None and not isinstance(deprecation.get("replaced_by"), str):
            errs.append("deprecation.replaced_by must be string or null")

    artifact = m.get("artifact", {})
    if not isinstance(artifact, dict):
        errs.append("artifact must be an object")
        return errs
    instruction = artifact.get("instruction")
    tool_ref = artifact.get("tool_ref")
    workflow = artifact.get("workflow")
    source_hash = artifact.get("source_hash")
    if instruction is not None and not isinstance(instruction, str):
        errs.append("artifact.instruction must be string or null")
    if tool_ref is not None and not isinstance(tool_ref, str):
        errs.append("artifact.tool_ref must be string or null")
    if workflow is not None and not isinstance(workflow, dict):
        errs.append("artifact.workflow must be object or null")
    if source_hash is not None and not isinstance(source_hash, str):
        errs.append("artifact.source_hash must be string or null")
    files = artifact.get("files", [])
    if not isinstance(files, list):
        errs.append("artifact.files must be an array")
        files = []
    seen_paths: set[str] = set()
    for i, file in enumerate(files):
        if not isinstance(file, dict):
            errs.append(f"artifact.files[{i}] must be an object")
            continue
        path = file.get("path")
        if not _safe_relative_path(path):
            errs.append(f"unsafe artifact file path: {path}")
        if path in seen_paths:
            errs.append(f"duplicate artifact file path: {path}")
        if isinstance(path, str):
            seen_paths.add(path)
        sha = file.get("sha256")
        if sha is not None and not isinstance(sha, str):
            errs.append(f"artifact.files[{i}].sha256 must be string or null")
        if not _is_non_negative_int(file.get("size")):
            errs.append(f"artifact.files[{i}].size must be a non-negative integer")
    entrypoints = artifact.get("entrypoints", [])
    if not _string_list(entrypoints):
        errs.append("artifact.entrypoints must be an array of strings")
        entrypoints = []
    elif len(entrypoints) != len(set(entrypoints)):
        errs.append("duplicate artifact entrypoint")
    for entrypoint in entrypoints:
        if not _safe_relative_path(entrypoint):
            errs.append(f"unsafe artifact entrypoint: {entrypoint}")
        if entrypoint not in seen_paths:
            errs.append(f"artifact entrypoint is not a declared file: {entrypoint}")
        if not is_supported_entrypoint(entrypoint):
            errs.append(f"unsupported artifact entrypoint: {entrypoint}")
    if "tool" in modes and not tool_ref:
        errs.append("tool mode requires artifact.tool_ref")
    if "workflow" in modes and not workflow:
        errs.append("workflow mode requires artifact.workflow")
    if "executable" in modes and not entrypoints:
        errs.append("executable mode requires artifact.entrypoints")
    return errs
