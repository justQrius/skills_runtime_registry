"""skills.sh ingestion adapter: V1Skill detail+audit -> portable manifest.

Stdlib only (urllib.request). `to_manifest` is pure (no network);
`fetch_page` / `import_ids` do the HTTP.
"""
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from .runtimes import is_supported_entrypoint


def fetch_page(base: str, path: str, token: str, params: dict | None = None) -> dict:
    """GET base/api/v1/<path> with bearer auth. Returns decoded JSON."""
    token = token.strip().replace("\r", "")
    qs = ("?" + urllib.parse.urlencode(params)) if params else ""
    url = base.rstrip("/") + "/api/v1/" + path.lstrip("/") + qs
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        if e.code == 429:
            retry = e.headers.get("Retry-After")
            try:
                wait = float(retry) if retry else 1.0
            except ValueError:
                wait = 1.0
            time.sleep(wait)
            req2 = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(req2) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e2:
                b2 = e2.read().decode(errors="replace")
                raise RuntimeError(f"skills.sh {e2.code} {url}: {b2}") from e2
        if e.code in (401, 404, 503):
            raise RuntimeError(f"skills.sh {e.code} {url}: {body}") from e
        raise RuntimeError(f"skills.sh {e.code} {url}: {body}") from e


def _skill_md(detail: dict) -> str | None:
    files = detail.get("files") or []
    for f in files:
        if f.get("path") == "SKILL.md" and f.get("contents"):
            return f["contents"]
    return None


def _description(detail: dict, skill_md: str | None) -> str:
    slug = detail.get("slug") or detail.get("id", "")
    if not skill_md:
        return slug
    text = skill_md
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            frontmatter = text[3:end]
            match = re.search(r"(?im)^description\s*:\s*(.+?)\s*$", frontmatter)
            if match:
                value = match.group(1).strip()
                if ((value.startswith('"') and value.endswith('"')) or
                        (value.startswith("'") and value.endswith("'"))):
                    value = value[1:-1]
                if value:
                    return value[:500]
            text = text[end + 4:]
    for block in text.split("\n\n"):
        b = block.strip()
        if not b or b.startswith("#"):
            continue
        return b[:500]
    return slug


def to_manifest(detail: dict, audits: dict | None, extra: dict | None = None) -> dict:
    """Map skills.sh detail + audits to a portable manifest (pure, no network)."""
    extra = extra or {}
    skill_id = detail["id"]
    id_parts = skill_id.split("/")
    publisher_id = id_parts[0]
    pack = "/".join(id_parts[:2]) if len(id_parts) >= 3 else None
    slug = detail.get("slug") or skill_id
    skill_md = _skill_md(detail)
    topics = list(dict.fromkeys([
        *extra.get("topics", []), *(detail.get("topics") or []),
    ]))
    tags = list(dict.fromkeys([*topics, *(detail.get("tags") or [])]))
    audit_list = (audits or {}).get("audits", []) if audits else []
    passed = [a for a in audit_list if a.get("status") == "pass"]
    audited = bool(passed)
    audit_ref = None
    if passed:
        first = passed[0]
        ref_slug = first.get("slug") or str(first.get("provider", "")).lower()
        audit_ref = f"skills.sh:{ref_slug}:{first.get('auditedAt')}"
    files_meta = []
    entrypoints = []
    for f in detail.get("files") or []:
        if not f.get("path"):
            continue
        raw = (f.get("contents") or "").encode()
        files_meta.append({
            "path": f["path"],
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
        })
        if is_supported_entrypoint(f["path"]):
            entrypoints.append(f["path"])
    modes = ["instruction", "executable"] if entrypoints else ["instruction"]
    raw_version = detail.get("version")
    if not isinstance(raw_version, str) or not re.match(
            r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)([-+].*)?$",
            raw_version):
        version_hash = re.sub(r"[^0-9A-Za-z.-]", "", str(detail.get("hash") or ""))[:12]
        raw_version = "0.0.0" + (f"+{version_hash}" if version_hash else "")
    manifest = {
        "skill_id": skill_id,
        "version": raw_version,
        "name": slug,
        "description": _description(detail, skill_md),
        "publisher": {"id": publisher_id},
        "topics": topics,
        "tags": tags,
        "pack": pack,
        "execution_modes": modes,
        "compatibility": {"agent_classes": [], "harnesses": ["agnostic"]},
        "input_schema": {},
        "output_schema": {},
        "tool_dependencies": [],
        "permissions": {"network": False, "filesystem": "none"},
        "trust": {
            "official": bool(extra.get("official", False)),
            "audited": audited,
            "audit_ref": audit_ref,
            "revoked": False,
        },
        "integrity": {"sha256": None},
        "cache": {"ttl_seconds": 300, "pin_recommended": True},
        "deprecation": {"deprecated": False, "replaced_by": None},
        "popularity": int(detail.get("installs", 0) or 0),
        "updated_at": detail.get("updatedAt") or detail.get("updated_at"),
        "artifact": {"instruction": skill_md, "tool_ref": None,
                     "source_hash": detail.get("hash"),
                     "files": files_meta, "entrypoints": entrypoints},
    }
    from .manifest import normalize
    manifest = normalize(manifest)
    from .cache import digest as manifest_digest
    manifest["integrity"]["sha256"] = manifest_digest(manifest)
    return manifest


def import_ids(
    ids: list[str],
    base: str,
    token: str,
    official_set: set[str] | None = None,
    files: "FileStore | None" = None,
) -> list[dict]:
    """Fetch detail+audit per id, map via to_manifest, add to a Registry.

    Skips `isDuplicate: true` entries. Dedupes by skill_id keeping highest
    popularity. Returns normalized manifests. When `files` is given, every
    fetched file is stored (verified bytes keyed by skill_id + path).
    """
    from .cache import Cache
    from .files import FileStore
    from .store import Registry

    official_set = official_set or set()
    cache = Cache()
    reg = Registry()
    store = files or FileStore()
    best: dict[str, dict] = {}
    for sid in ids:
        detail = fetch_page(base, f"skills/{sid}", token)
        if detail.get("isDuplicate") is True:
            continue
        try:
            audits = fetch_page(base, f"skills/audit/{sid}", token)
        except RuntimeError as e:
            if " 404 " in str(e):
                audits = None
            else:
                raise
        cached = cache.get(detail.get("id", sid))
        if (
            cached is not None
            and detail.get("hash")
            and cached.get("artifact", {}).get("source_hash") == detail.get("hash")
        ):
            m = cached
        else:
            m = to_manifest(detail, audits, {"official": detail.get("id", sid) in official_set})
            m = reg.add(m)
            cache.put(m, ttl=300)
            package_files = [
                (f["path"], f["contents"])
                for f in detail.get("files") or []
                if f.get("path") and f.get("contents") is not None
            ]
            store.put_bundle(m["skill_id"], m["version"], package_files)
        key = m["skill_id"]
        if key not in best or m.get("popularity", 0) > best[key].get("popularity", 0):
            best[key] = m
    return list(best.values())
