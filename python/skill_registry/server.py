"""Skill-registry MCP server (stdlib only, NDJSON over stdio).

Exposes the registry to any MCP-speaking agent: search, resolve, load,
artifact delivery, tool invocation, refresh, and exact execution.

Zero-touch: serves the bundled `catalog/` with no config. Set
SKILLS_SH_TOKEN for trusted stdio refreshes, or use a request-scoped bearer
token for admin-gated HTTP refreshes.
"""
import base64
import hashlib
import io
import json
import os
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

from skill_registry import (  # noqa: E402
    FileStore,
    Registry,
    Telemetry,
    load as load_skill,
    resolve,
    search,
)

NAME = "skill-registry"
VERSION = "1.3.0"


def _inject_http_credentials(msg: object, admin_key: str | None,
                             authorization: str | None) -> None:
    """Add transport credentials to a refresh request without exposing schema args."""
    if not isinstance(msg, dict):
        return
    params = msg.get("params")
    if not isinstance(params, dict) or params.get("name") != "refresh":
        return
    args = params.get("arguments")
    if not isinstance(args, dict):
        return
    if admin_key:
        args.setdefault("_admin_key", admin_key)
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        if token and len(token) <= 16_384 and not any(ord(c) < 32 for c in token):
            args.setdefault("_skills_sh_token", token)


def _slim(m: dict) -> dict:
    return {
        "skill_id": m["skill_id"],
        "version": m["version"],
        "name": m["name"],
        "description": m["description"],
        "publisher": m.get("publisher", {}),
        "topics": m.get("topics", []),
        "tags": m.get("tags", []),
        "pack": m.get("pack"),
        "permissions": m.get("permissions", {}),
        "compatibility": m.get("compatibility", {}),
        "trust": m.get("trust", {}),
        "integrity": m.get("integrity", {}),
        "popularity": m.get("popularity", 0),
        "execution_modes": m.get("execution_modes", []),
        "entrypoints": m.get("artifact", {}).get("entrypoints", []),
    }

class Server:
    def __init__(self, catalog: str | Path, data: str | Path | None = None,
                 admin_key: str | None = None, tool_handlers: dict | None = None):
        self.reg = Registry()
        self.reg.load_dir(str(catalog))
        default_data = Path.home() / ".skill-registry" / "files"
        self.data_dir = Path(data or str(default_data))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.files = FileStore(self.data_dir)
        self.base = os.environ.get("SKILLS_SH_BASE", "https://skills.sh")
        self.admin_key = admin_key or os.environ.get("SKILL_REGISTRY_ADMIN_KEY")
        self.tool_handlers = dict(tool_handlers or {})
        self.public = False  # set True by _serve_http; refresh is gated there
        self.tel = Telemetry(sink=self.data_dir / "telemetry.jsonl")
        for saved in sorted(self.data_dir.glob("*.manifest.json")):
            try:
                self.reg.add(json.loads(saved.read_text()))
            except (ValueError, json.JSONDecodeError, OSError):
                continue

    def _save_manifest(self, m: dict) -> None:
        safe = ("__".join(m["skill_id"].split("/")) + "__v__" +
                m["version"].replace("/", "_").replace("\\", "_") +
                ".manifest.json")
        try:
            (self.data_dir / safe).write_text(json.dumps(m))
        except OSError:
            pass

    @staticmethod
    def _limit(value, *, default: int, maximum: int = 100) -> int:
        value = default if value is None else value
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
            raise ValueError(f"limit must be an integer from 1 to {maximum}")
        return value

    def _manifest(self, skill_id: str, version: str | None = None) -> dict:
        m = self.reg.get(skill_id, version)
        if m is not None:
            return m
        if version is not None and self.reg.get(skill_id) is not None:
            raise KeyError(f"unknown version: {skill_id}@{version}")
        raise KeyError(f"unknown skill: {skill_id}")

    def t_search(self, a: dict) -> list:
        hits = search(
            self.reg.all(),
            a.get("query", ""),
            topic=a.get("topic"),
            pack=a.get("pack"),
            publisher=a.get("publisher"),
            agent_class=a.get("agent_class"),
            execution_mode=a.get("execution_mode"),
            official_only=a.get("official_only", False),
            audited_only=a.get("audited_only", False),
            include_revoked=a.get("include_revoked", False),
            include_deprecated=a.get("include_deprecated", False),
        )
        limit = self._limit(a.get("limit"), default=10)
        self.tel.emit("search.requested", query=a.get("query", ""))
        self.tel.emit("candidates.returned", count=len(hits))
        return [_slim(m) for m in hits[:limit]]
    def t_resolve(self, a: dict) -> dict:
        self.tel.emit("search.requested", task=a.get("task", ""))
        out = resolve(
            self.reg.all(),
            task=a.get("task", ""),
            tags=a.get("tags"),
            agent_class=a.get("agent_class"),
            allowed_modes=a.get("allowed_modes"),
            allowlist=a.get("allowlist"),
            denylist=a.get("denylist"),
            official_only=a.get("official_only", False),
            audited_only=a.get("audited_only", False),
            policy=a.get("policy"),
            require_review=a.get("require_review", False),
            limit=a.get("limit", 5),
        )
        self.tel.emit("candidates.returned", count=len(out["candidates"]))
        return out

    def t_list_versions(self, a: dict) -> dict:
        versions = self.reg.versions(a["skill_id"])
        if not versions:
            raise KeyError(f"unknown skill: {a['skill_id']}")
        return {"skill_id": a["skill_id"], "versions": versions,
                "latest": versions[0]}

    def t_validate_skill(self, a: dict) -> dict:
        from skill_registry.conformance import validate_package

        m = self._manifest(a["skill_id"], a.get("version"))
        return validate_package(
            m,
            lambda path: self.files.get_bytes(
                m["skill_id"], path,
                next((f.get("sha256") for f in m.get("artifact", {}).get("files", [])
                      if f["path"] == path), None),
                version=m["version"],
            ),
            profile=a.get("profile", "ecosystem"),
        )

    def t_load(self, a: dict) -> dict:
        try:
            m = self._manifest(a["skill_id"], a.get("version"))
        except KeyError:
            self.tel.emit("fetch.fail", skill_id=a.get("skill_id"))
            raise
        self.tel.emit("skill.selected", skill_id=m["skill_id"])
        self.tel.emit("fetch.ok", skill_id=m["skill_id"])
        out = load_skill(m)
        out["skill_id"] = m["skill_id"]
        out["version"] = m["version"]
        out["files"] = m.get("artifact", {}).get("files", [])
        if out["kind"] == "tool":
            out["available"] = out["tool"] in self.tool_handlers
            if not out["available"]:
                out["availability_reason"] = "no server-side handler registered"
        return out
    def t_get_artifact(self, a: dict) -> dict:
        try:
            m = self._manifest(a["skill_id"], a.get("version"))
        except KeyError:
            self.tel.emit("fetch.fail", skill_id=a.get("skill_id"))
            raise
        art = self.reg.get_artifact(m["skill_id"], m["version"])
        self.tel.emit("fetch.ok", skill_id=art["skill_id"])
        return art

    def t_get_file(self, a: dict) -> dict:
        try:
            m = self._manifest(a["skill_id"], a.get("version"))
        except KeyError:
            self.tel.emit("fetch.fail", skill_id=a.get("skill_id"))
            raise
        want = {f["path"]: f for f in m.get("artifact", {}).get("files", [])}
        meta = want.get(a["path"])
        if meta is None:
            self.tel.emit("fetch.fail", skill_id=m["skill_id"], path=a.get("path"))
            raise KeyError(f"unknown file: {a['path']}")
        doc = self.files.get(m["skill_id"], a["path"], meta.get("sha256"),
                             version=m["version"])
        if doc is None:
            self.tel.emit("cache.miss", skill_id=m["skill_id"], path=a["path"])
            self.tel.emit("integrity.fail", skill_id=m["skill_id"], path=a["path"])
            raise KeyError(f"contents unavailable or hash mismatch: {a['path']}")
        self.tel.emit("cache.hit", skill_id=m["skill_id"], path=a["path"])
        self.tel.emit("integrity.ok", skill_id=m["skill_id"], path=a["path"])
        return doc

    def t_get_files(self, a: dict) -> dict:
        """Fetch several or all verified files in one round trip."""
        m = self._manifest(a["skill_id"], a.get("version"))
        declared = [f["path"] for f in m.get("artifact", {}).get("files", [])]
        paths = a.get("paths")
        if paths is None:
            paths = declared
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise ValueError("paths must be an array of strings")
        if len(paths) > 500:
            raise ValueError("too many paths (max 500)")
        files = [self.t_get_file({"skill_id": m["skill_id"], "version": m["version"],
                                  "path": path}) for path in paths]
        return {"skill_id": m["skill_id"], "version": m["version"], "files": files}

    def t_get_package(self, a: dict) -> dict:
        """Return a deterministic ZIP containing the complete verified package."""
        m = self._manifest(a["skill_id"], a.get("version"))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as archive:
            for meta in sorted(m.get("artifact", {}).get("files", []),
                               key=lambda item: item["path"]):
                raw = self.files.get_bytes(m["skill_id"], meta["path"],
                                           meta.get("sha256"), version=m["version"])
                if raw is None:
                    raise KeyError(f"contents unavailable or hash mismatch: {meta['path']}")
                info = zipfile.ZipInfo(meta["path"], date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, raw)
        raw_zip = buf.getvalue()
        return {"skill_id": m["skill_id"], "version": m["version"],
                "file_count": len(m.get("artifact", {}).get("files", [])),
                "files": m.get("artifact", {}).get("files", []),
                "size": len(raw_zip), "sha256": hashlib.sha256(raw_zip).hexdigest(),
                "contents_b64": base64.b64encode(raw_zip).decode("ascii")}

    def t_invoke_tool(self, a: dict) -> dict:
        """Invoke a configured implementation for a tool-mode skill."""
        from skill_registry.policy import decide
        from skill_registry.schema_validation import validate_instance

        m = self._manifest(a["skill_id"], a.get("version"))
        if "tool" not in m.get("execution_modes", []):
            raise ValueError(f"skill is not tool-capable: {m['skill_id']}")
        tool = m.get("artifact", {}).get("tool_ref")
        verdict, reason = decide(m, a.get("policy") or {})
        if verdict == "deny":
            return {"status": "refused", "tool": tool, "verdict": verdict,
                    "reason": reason}
        if verdict != "allow" and not a.get("approved", False):
            return {"status": "needs-approval", "tool": tool, "verdict": verdict,
                    "reason": reason}
        handler = self.tool_handlers.get(tool)
        if handler is None:
            return {"status": "unavailable", "tool": tool,
                    "reason": "no server-side handler registered"}
        value = a.get("input", {})
        if not isinstance(value, dict):
            raise ValueError("input must be an object")
        validate_instance(value, m.get("input_schema", {}), "input")
        output = handler(value)
        validate_instance(output, m.get("output_schema", {}), "output")
        self.tel.emit("skill.selected", skill_id=m["skill_id"])
        return {"status": "ok", "skill_id": m["skill_id"], "version": m["version"],
                "tool": tool, "output": output}

    def t_discover(self, a: dict) -> dict:
        """Live skills.sh search — discovery cards, no token needed."""
        from skill_registry.discover import discover

        limit = self._limit(a.get("limit"), default=20)
        try:
            result = discover(
                self.base,
                a.get("query", ""),
                limit=limit,
                owner=a.get("owner"),
            )
        except (RuntimeError, ValueError) as e:
            self.tel.emit("discover.fail", query=a.get("query", ""))
            raise
        self.tel.emit("discover.ok", query=a.get("query", ""),
                      count=result.get("count"))
        return result

    def t_refresh(self, a: dict) -> dict:
        from skill_registry.ingest import import_ids

        token = a.get("_skills_sh_token") or os.environ.get("SKILLS_SH_TOKEN")
        if not token:
            return {"status": "unconfigured",
                    "message": "Provide a request bearer token or set SKILLS_SH_TOKEN to enable live refresh; serving bundled catalog."}
        if self.public and not self.admin_key:
            raise PermissionError("refresh disabled over HTTP: set SKILL_REGISTRY_ADMIN_KEY")
        if self.admin_key and a.get("admin_key") != self.admin_key:
            if a.get("_admin_key") != self.admin_key:
                raise PermissionError("bad admin key")
        ids = a.get("ids", [])
        official = set(a.get("official_ids", []))
        try:
            ms = import_ids(ids, self.base, token,
                            official_set=official or None, files=self.files,
                            audited_only=bool(a.get("audited_only")))
        except RuntimeError as e:
            self.tel.emit("fetch.fail", ids=ids)
            raise
        if a.get("audited_only"):
            rejected = [m["skill_id"] for m in ms
                        if not m.get("trust", {}).get("audited")
                        or m.get("trust", {}).get("revoked")]
            if rejected:
                self.tel.emit("skill.rejected", skill_ids=rejected,
                              reason="refresh requires audited, non-revoked skills")
                raise PermissionError(
                    "refresh rejected skills that are not audited and non-revoked: "
                    + ", ".join(rejected))
        for m in ms:
            try:
                self.reg.add(m)
            except ValueError:
                continue
            self._save_manifest(m)
        self.tel.emit("fetch.ok", count=len(ms))
        return {"status": "ok", "imported": [_slim(m) for m in ms]}

    def t_execute(self, a: dict) -> dict:
        from skill_registry.execute import execute

        try:
            m = self._manifest(a["skill_id"], a.get("version"))
        except KeyError:
            self.tel.emit("fetch.fail", skill_id=a.get("skill_id"))
            raise
        entrypoint = a.get("entrypoint", "")
        try:
            result = execute(
                m, lambda p, s: self.files.get_bytes(m["skill_id"], p, s,
                                                     version=m["version"]),
                entrypoint, args=a.get("args"),
                policy=a.get("policy"), approved=a.get("approved", False),
                timeout_s=a.get("timeout_s", 120), inputs=a.get("inputs"))
        except (ValueError, RuntimeError) as e:
            self.tel.emit("execution.fail", skill_id=m["skill_id"],
                          entrypoint=entrypoint)
            raise
        if result["status"] == "ok":
            self.tel.emit("skill.selected", skill_id=m["skill_id"])
            self.tel.emit("execution.ok", skill_id=m["skill_id"],
                          entrypoint=entrypoint,
                          duration_ms=result.get("duration_ms"))
        elif result["status"] == "failed":
            self.tel.emit("execution.fail", skill_id=m["skill_id"],
                          entrypoint=entrypoint,
                          duration_ms=result.get("duration_ms"),
                          exit_code=result.get("exit_code"))
        elif result["status"] in ("refused", "needs-approval"):
            self.tel.emit("skill.rejected", skill_id=m["skill_id"],
                          reason=result.get("reason"), verdict=result.get("verdict"))
        return result

    TOOLS = {
        "search": ("Find skills by keyword and filters.", {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "topic": {"type": "string"},
                "pack": {"type": "string"},
                "publisher": {"type": "string"},
                "agent_class": {"type": "string"},
                "execution_mode": {"type": "string"},
                "official_only": {"type": "boolean"},
                "audited_only": {"type": "boolean"},
                "include_revoked": {"type": "boolean"},
                "include_deprecated": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }}),
        "resolve": ("Rank skills for a task under policy, with rationale.", {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "agent_class": {"type": "string"},
                "allowed_modes": {"type": "array", "items": {"type": "string"}},
                "allowlist": {"type": "array", "items": {"type": "string"}},
                "denylist": {"type": "array", "items": {"type": "string"}},
                "official_only": {"type": "boolean"},
                "audited_only": {"type": "boolean"},
                "policy": {"type": "object"},
                "require_review": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }}),
        "list_versions": ("List available versions for a skill in semantic-version order.", {
            "type": "object", "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"}}}),
        "validate_skill": ("Validate package structure, frontmatter, references, and entrypoints.", {
            "type": "object", "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"}, "version": {"type": "string"},
                            "profile": {"enum": ["ecosystem", "strict"]}}}),
        "load": ("Activate a skill: instruction context or tool binding plus file list.", {
            "type": "object", "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"}, "version": {"type": "string"}}}),
        "get_artifact": ("Fetch a skill's manifest payload (instruction, tool_ref, schemas).", {
            "type": "object", "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"}, "version": {"type": "string"}}}),
        "get_file": ("Fetch one supporting file's verified contents.", {
            "type": "object", "required": ["skill_id", "path"],
            "properties": {"skill_id": {"type": "string"}, "version": {"type": "string"},
                            "path": {"type": "string"}}}),
        "get_files": ("Fetch selected or all supporting files with verified text/base64 contents.", {
            "type": "object", "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"}, "version": {"type": "string"},
                            "paths": {"type": "array", "maxItems": 500,
                                      "items": {"type": "string"}}}}),
        "get_package": ("Fetch the complete verified package as a deterministic ZIP archive.", {
            "type": "object", "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"},
                            "version": {"type": "string"}}}),
        "invoke_tool": ("Invoke a configured implementation for a tool-mode skill.", {
            "type": "object", "required": ["skill_id"],
            "properties": {"skill_id": {"type": "string"}, "version": {"type": "string"},
                            "input": {"type": "object"}, "policy": {"type": "object"},
                            "approved": {"type": "boolean"}}}),
        "discover": ("Search the live skills.sh index (no token; catalog not required). Returns ranked discovery cards; pass chosen ids to refresh to import.", {
            "type": "object", "required": ["query"],
            "properties": {"query": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1,
                                      "maximum": 100},
                            "owner": {"type": "string"}}}),
        "refresh": ("Import skill ids live from skills.sh (needs SKILLS_SH_TOKEN; over HTTP also needs admin_key).", {
            "type": "object",
            "properties": {"ids": {"type": "array", "items": {"type": "string"}},
                            "official_ids": {"type": "array", "items": {"type": "string"}},
                            "audited_only": {"type": "boolean"},
                            "admin_key": {"type": "string"}}}),
        "execute": ("Run a skill entrypoint in a fresh network-isolated container; non-allow verdicts need approved:true. Pass agent files as inputs[{path, text|b64}] (staged to /inputs); write outputs to /scratch (returned as artifacts with contents + contents_b64).", {
            "type": "object", "required": ["skill_id", "entrypoint"],
            "properties": {"skill_id": {"type": "string"}, "version": {"type": "string"},
                            "entrypoint": {"type": "string"},
                            "args": {"type": "array", "items": {"type": "string"}},
                            "inputs": {"type": "array", "items": {"type": "object"}},
                            "policy": {"type": "object"}, "approved": {"type": "boolean"},
                            "timeout_s": {"type": "integer", "minimum": 1,
                                          "maximum": 600}}}),
    }

    # ----- JSON-RPC -----
    def handle(self, msg: object):
        if not isinstance(msg, dict):
            return {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32600,
                              "message": "invalid request: expected object"}}
        mid = msg.get("id")
        method = msg.get("method", "")

        def ok(result):
            return {"jsonrpc": "2.0", "id": mid, "result": result}

        def err(code, message):
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": code, "message": message}}

        if method == "initialize":
            return ok({"protocolVersion": "2024-11-05",
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": NAME, "version": VERSION}})
        if method == "tools/list":
            return ok({"tools": [
                {"name": n, "description": d, "inputSchema": s}
                for n, (d, s) in self.TOOLS.items()]})
        if method == "tools/call":
            p = msg.get("params", {})
            fn = getattr(self, "t_" + p.get("name", "").replace("-", "_"), None)
            if fn is None:
                return err(-32601, f"unknown tool: {p.get('name')}")
            try:
                result = fn(p.get("arguments", {}))
            except KeyError as e:
                return err(-32002, str(e))
            except ValueError as e:
                return err(-32001, str(e))
            except PermissionError as e:
                return err(-32004, str(e))
            except RuntimeError as e:
                return err(-32003, str(e))
            payload = {"content": [{"type": "text", "text": json.dumps(result)}]}
            if isinstance(result, dict) and result.get("status") in ("failed", "refused"):
                payload["isError"] = True
            return ok(payload)
        if method.startswith("notifications/"):
            return None
        if method == "ping":
            return ok({})
        return err(-32601, f"unknown method: {method}")


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Skill-registry MCP server (stdio or HTTP).")
    ap.add_argument("--catalog", default=str(REPO_ROOT / "catalog"))
    ap.add_argument("--data", default=os.environ.get("SKILL_REGISTRY_DATA"))
    ap.add_argument("--http", type=int, default=0, metavar="PORT",
                    help="serve JSON-RPC over HTTP on PORT instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    srv = Server(args.catalog, args.data,
                 admin_key=os.environ.get("SKILL_REGISTRY_ADMIN_KEY"))
    if args.http:
        srv.public = True
        _serve_http(srv, args.host, args.http)
    stdin = sys.stdin
    stdout = sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": str(e)}}) + "\n")
            stdout.flush()
            continue
        resp = srv.handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()



def _serve_http(srv: Server, host: str, port: int) -> None:
    """Cloud mode: POST /mcp (JSON-RPC) + GET /healthz. Stdlib only."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    log = sys.stderr

    class Handler(BaseHTTPRequestHandler):
        server_version = NAME + "/" + VERSION

        def log_message(self, fmt, *args):
            log.write("mcp-http %s\n" % (fmt % args))

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, {"status": "ok", "server": NAME,
                                 "version": VERSION,
                                 "skills": len(srv.reg.all())})
                return
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/mcp":
                self._send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length <= 0 or length > 2_000_000:
                self._send(400, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32600,
                                           "message": "missing or oversize body"}})
                return
            try:
                msg = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, OSError) as e:
                self._send(400, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32700, "message": str(e)}})
                return
            _inject_http_credentials(msg, self.headers.get("X-Admin-Key"),
                                     self.headers.get("Authorization"))
            resp = srv.handle(msg)
            self._send(200, resp or {"jsonrpc": "2.0", "id": msg.get("id"),
                                     "result": {"accepted": True}})
    httpd = ThreadingHTTPServer((host, port), Handler)
    log.write(f"{NAME} http on {host}:{port}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
if __name__ == "__main__":
    main()
