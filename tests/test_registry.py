"""Permanent gate: stdlib unittest over the whole registry + MCP handler."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "python"))

from skill_registry import (  # noqa: E402
    Cache,
    FileStore,
    Registry,
    Telemetry,
    decide,
    load,
    normalize,
    resolve,
    search,
    unload,
    validate,
)
from skill_registry.cache import digest  # noqa: E402
from skill_registry.ingest import to_manifest  # noqa: E402


def fixture(name: str) -> dict:
    return json.loads((ROOT / "examples" / "conformance" / name).read_text())


def seed_registry() -> Registry:
    reg = Registry()
    reg.load_dir(ROOT / "examples" / "seeds")
    return reg


class ManifestTest(unittest.TestCase):
    def test_valid_fixtures(self):
        for f in ("valid-instruction.json", "valid-tool.json"):
            with self.subTest(f):
                m = fixture(f)
                self.assertEqual(validate(m), [])
                self.assertEqual(digest(m), m["integrity"]["sha256"])

    def test_invalid_fixtures(self):
        cases = {"invalid-bad-id.json": "bad skill_id",
                 "invalid-bad-version.json": "bad version",
                 "invalid-unknown-field.json": "unknown field: owner"}
        for f, needle in cases.items():
            with self.subTest(f):
                errs = validate(normalize(fixture(f)))
                self.assertTrue(any(needle in e for e in errs), errs)

    def test_revoked_requires_hash(self):
        m = normalize(fixture("valid-instruction.json"))
        m["trust"]["revoked"] = True
        m["integrity"]["sha256"] = None
        self.assertIn("revoked requires integrity.sha256", validate(m))

    def test_empty_modes_rejected(self):
        m = normalize(fixture("valid-instruction.json"))
        m["execution_modes"] = []
        self.assertTrue(any("execution_modes" in e for e in validate(m)))

    def test_singular_mode_normalized(self):
        m = normalize({**fixture("valid-instruction.json"),
                       "execution_modes": ["instruction"]})
        raw = dict(fixture("valid-instruction.json"))
        del raw["execution_modes"]
        raw["execution_mode"] = "instruction"
        self.assertEqual(normalize(raw)["execution_modes"], ["instruction"])
        self.assertNotIn("execution_mode", normalize(raw))
        self.assertEqual(validate(m), [])


class SearchTest(unittest.TestCase):
    def test_keyword(self):
        items = seed_registry().all()
        self.assertEqual([m["skill_id"] for m in search(items, "react")],
                         ["acme/react-review"])

    def test_revoked_excluded(self):
        self.assertEqual(search([fixture("revoked.json")], "react"), [])

    def test_enterprise_filters(self):
        items = seed_registry().all()
        got = search(items, "", official_only=True, audited_only=True)
        self.assertEqual([m["skill_id"] for m in got], ["official/procurement-flow"])


class PolicyTest(unittest.TestCase):
    def base(self, **kw):
        m = {"skill_id": "a/b", "publisher": {"id": "p"},
             "trust": {"official": False, "audited": True, "revoked": False},
             "execution_modes": ["instruction"]}
        m.update(kw)
        return m

    def test_rules_in_order(self):
        m = self.base()
        self.assertEqual(decide(m, {"denylist": ["a/b"]}), ("deny", "denylisted"))
        self.assertEqual(decide(m, {"allowlist": ["other"]}), ("deny", "not-allowlisted"))
        self.assertEqual(decide({**m, "trust": {"revoked": True}}, {}), ("deny", "revoked"))
        self.assertEqual(decide({**m, "trust": {"audited": False}},
                                {"require_audited": True}), ("require-review", "unaudited"))
        self.assertEqual(decide(m, {"official_only": True}), ("require-review", "unofficial"))
        self.assertEqual(decide(m, {}), ("allow", "ok"))

    def test_resolve_gate(self):
        items = [{"skill_id": "a/b", "version": "1.0.0", "permissions": {},
                  "trust": {"audited": False}, "integrity": {},
                  "execution_modes": ["instruction"], "topics": [], "tags": [],
                  "publisher": {"id": "p"}, "popularity": 0,
                  "compatibility": {"agent_classes": []}, "deprecation": {}}]
        gated = resolve(items, policy={"require_audited": True})
        self.assertEqual(gated["candidates"], [])
        self.assertIsNotNone(gated["fallback"])
        kept = resolve(items, policy={"require_audited": True}, require_review=True)
        self.assertEqual(kept["candidates"][0]["rationale"], ["policy:require-review"])


class CacheTest(unittest.TestCase):
    def test_verify_roundtrip_and_pin(self):
        c = Cache()
        m = fixture("valid-instruction.json")
        c.put(m)
        self.assertTrue(c.verify(c.get(m["skill_id"])))
        self.assertIsNone(c.get_pinned(m["skill_id"], "0" * 64))
        self.assertIsNotNone(c.get_pinned(m["skill_id"], m["integrity"]["sha256"]))

    def test_audit_withdrawal_evicts(self):
        c = Cache()
        m = {"skill_id": "x/y", "version": "1.0.0",
             "trust": {"audited": True}, "cache": {}}
        c.put(m)
        m["trust"]["audited"] = False
        self.assertIsNone(c.get("x/y"))


class LoaderTest(unittest.TestCase):
    def test_instruction_bind_unbind(self):
        m = seed_registry().get("acme/react-review")
        sess: dict = {}
        out = load(m, sess)
        self.assertTrue(out["session_bound"])
        self.assertEqual(sess["context_blocks"], [out["context"]])
        unload(m, sess)
        self.assertEqual(sess["context_blocks"], [])

    def test_tool_bind_unbind(self):
        m = seed_registry().get("official/procurement-flow")
        sess: dict = {}
        out = load(m, sess)
        self.assertIn(out["tool"], sess["tools"])
        unload(m, sess)
        self.assertEqual(sess["tools"], {})


class TelemetryTest(unittest.TestCase):
    def test_unknown_rejected_alias_kept(self):
        t = Telemetry()
        with self.assertRaises(ValueError):
            t.emit("nope")
        t.emit("skill_selected", skill_id="a/b")
        self.assertEqual(t.events[-1]["event"], "skill.selected")

    def test_jsonl_sink(self):
        with tempfile.TemporaryDirectory() as d:
            sink = Path(d) / "t.jsonl"
            Telemetry(sink=sink).emit("search.requested", query="x")
            self.assertEqual(json.loads(sink.read_text())["event"], "search.requested")


class IngestTest(unittest.TestCase):
    def test_mapping(self):
        d = {"id": "vercel-labs/agent-skills/x", "slug": "x", "installs": 10,
             "hash": "abc",
             "files": [{"path": "SKILL.md", "contents": "# T\n\nGuide."},
                       {"path": "run.sh", "contents": "echo hi"}]}
        a = {"audits": [{"provider": "Snyk", "slug": "snyk", "status": "pass",
                         "auditedAt": "2026-04-15T12:03:00.000Z"}]}
        m = normalize(to_manifest(d, a, {"official": True}))
        self.assertEqual(validate(m), [])
        self.assertEqual(m["trust"]["audit_ref"], "skills.sh:snyk:2026-04-15T12:03:00.000Z")
        self.assertEqual([f["path"] for f in m["artifact"]["files"]],
                         ["SKILL.md", "run.sh"])
        self.assertTrue(all(len(f["sha256"]) == 64 for f in m["artifact"]["files"]))

    def test_frontmatter_stripped(self):
        d = {"id": "a/b", "slug": "b", "installs": 1, "hash": "h",
             "files": [{"path": "SKILL.md",
                        "contents": "---\nname: x\n---\n\n# T\n\nReal desc."}]}
        self.assertEqual(to_manifest(d, None)["description"], "Real desc.")

    def test_store_artifact(self):
        reg = seed_registry()
        art = reg.get_artifact("acme/react-review")
        self.assertEqual(art["skill_id"], "acme/react-review")
        self.assertIn("instruction", art)


class FilesTest(unittest.TestCase):
    def test_roundtrip_and_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            fs = FileStore(d)
            meta = fs.put("a/b", "SKILL.md", "hello")
            doc = fs.get("a/b", "SKILL.md", meta["sha256"])
            self.assertEqual(doc["contents"], "hello")
            self.assertIsNone(fs.get("a/b", "SKILL.md", "0" * 64))
            self.assertIsNone(fs.get("a/b", "nope", None))

    def test_oversize_rejected(self):
        fs = FileStore()
        with self.assertRaises(ValueError):
            fs.put("a/b", "big.bin", b"x" * (1_000_001))


class McpTest(unittest.TestCase):
    def test_handle(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
        init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "skill-registry")

        names = [t["name"] for t in
                 srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                 ["result"]["tools"]]
        self.assertEqual(names, ["search", "resolve", "load", "get_artifact",
                                 "get_file", "refresh", "execute"])

        def text(resp):
            return json.loads(resp["result"]["content"][0]["text"])

        r = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "search", "arguments": {"query": "react"}}})
        self.assertEqual([m["skill_id"] for m in text(r)], ["acme/react-review"])

        r = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "resolve",
                                   "arguments": {"task": "approve procurement",
                                                 "agent_class": "workflow-agent",
                                                 "official_only": True,
                                                 "audited_only": True}}})
        self.assertEqual(text(r)["candidates"][0]["skill_id"],
                         "official/procurement-flow")

        r = srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                        "params": {"name": "load",
                                   "arguments": {"skill_id": "acme/react-review"}}})
        self.assertIn("context", text(r))

        r = srv.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                        "params": {"name": "nope", "arguments": {}}})
        self.assertEqual(r["error"]["code"], -32601)

        r = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                        "params": {"name": "get_file",
                                   "arguments": {"skill_id": "acme/react-review",
                                                 "path": "SKILL.md"}}})
        self.assertEqual(r["error"]["code"], -32002)

    def test_refresh_unconfigured(self):
        from skill_registry import server as mcp_server

        env = {k: v for k, v in os.environ.items() if k != "SKILLS_SH_TOKEN"}
        old = os.environ.get("SKILLS_SH_TOKEN")
        os.environ.pop("SKILLS_SH_TOKEN", None)
        try:
            srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
            body = srv.t_refresh({"ids": ["a/b"]})
        finally:
            if old is not None:
                os.environ["SKILLS_SH_TOKEN"] = old
        self.assertEqual(body["status"], "unconfigured")


class HardenTest(unittest.TestCase):
    def test_frontmatter_stripped_from_context(self):
        m = {"skill_id": "a/b", "description": "d", "execution_modes": ["instruction"],
             "artifact": {"instruction": "---\nname: x\n---\n\nReal body."}}
        sess: dict = {}
        out = load(m, sess)
        self.assertEqual(out["context"], "Real body.")
        unload(m, sess)
        self.assertEqual(sess["context_blocks"], [])

    def test_refresh_admin_gate(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp(),
                                admin_key="secret")
        srv.public = True
        old = os.environ.get("SKILLS_SH_TOKEN")
        os.environ["SKILLS_SH_TOKEN"] = "dummy"
        try:
            with self.assertRaises(PermissionError):
                srv.t_refresh({"ids": ["a/b"]})
            with self.assertRaises(PermissionError):
                srv.t_refresh({"ids": ["a/b"], "admin_key": "wrong"})
            open_denied = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                      "params": {"name": "refresh",
                                                 "arguments": {"ids": ["a/b"]}}})
            self.assertEqual(open_denied["error"]["code"], -32004)
        finally:
            if old is None:
                os.environ.pop("SKILLS_SH_TOKEN", None)
            else:
                os.environ["SKILLS_SH_TOKEN"] = old

    def test_refresh_disabled_public_without_key(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
        srv.public = True
        old = os.environ.get("SKILLS_SH_TOKEN")
        os.environ["SKILLS_SH_TOKEN"] = "dummy"
        try:
            with self.assertRaises(PermissionError):
                srv.t_refresh({"ids": ["a/b"]})
        finally:
            if old is None:
                os.environ.pop("SKILLS_SH_TOKEN", None)
            else:
                os.environ["SKILLS_SH_TOKEN"] = old

    def test_manifest_persist_reload(self):
        from skill_registry import server as mcp_server

        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            m = dict(fixture("valid-instruction.json"))
            srv._save_manifest(m)
            srv2 = mcp_server.Server(ROOT / "catalog", d)
            self.assertIsNotNone(srv2.reg.get("acme/react-review"))

    def test_server_telemetry(self):
        from skill_registry import server as mcp_server

        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            srv.t_search({"query": "react"})
            srv.t_load({"skill_id": "acme/react-review"})
            names = [e["event"] for e in srv.tel.events]
            for want in ("search.requested", "candidates.returned",
                         "skill.selected", "fetch.ok"):
                self.assertIn(want, names)
            self.assertTrue((Path(d) / "telemetry.jsonl").exists())

    def test_filestore_disk_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            FileStore(d).put("a/b", "SKILL.md", "persisted")
            doc = FileStore(d).get("a/b", "SKILL.md")
            self.assertEqual(doc["contents"], "persisted")

    def test_catalog_matches_conformance(self):
        for src, dst in (("valid-instruction.json", "react-review.json"),
                         ("valid-tool.json", "procurement-flow.json")):
            a = json.loads((ROOT / "examples" / "conformance" / src).read_text())
            b = json.loads((ROOT / "catalog" / dst).read_text())
            self.assertEqual(a, b)

    def test_http_health_and_call(self):
        import threading
        import time
        import urllib.request
        from http.server import ThreadingHTTPServer
        from skill_registry import server as mcp_server
        from skill_registry.server import _serve_http

        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            probe = ThreadingHTTPServer(("127.0.0.1", 0), None)
            port = probe.server_address[1]
            probe.server_close()
            t = threading.Thread(target=_serve_http,
                                 args=(srv, "127.0.0.1", port), daemon=True)
            t.start()
            deadline = time.time() + 10
            health = None
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz",
                                               timeout=2) as r:
                        health = json.loads(r.read())
                    break
                except OSError:
                    time.sleep(0.1)
            self.assertEqual(health["status"], "ok")
            body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "search",
                                          "arguments": {"query": "react"}}}).encode()
            req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                resp = json.loads(r.read())
            items = json.loads(resp["result"]["content"][0]["text"])
            self.assertEqual([m["skill_id"] for m in items], ["acme/react-review"])


class ExecuteTest(unittest.TestCase):
    def manifest(self):
        return {"skill_id": "t/hello", "version": "1.0.0",
                "execution_modes": ["instruction", "executable"],
                "trust": {"audited": False}, "permissions": {},
                "integrity": {"sha256": "0" * 64},
                "artifact": {"files": [
                    {"path": "hello.py", "sha256": "", "size": 0},
                    {"path": "SKILL.md", "sha256": "", "size": 0}]},
                "popularity": 0}

    def test_gate_needs_approval(self):
        from skill_registry.execute import gate
        self.assertEqual(gate(self.manifest(), None, False)["status"], "needs-approval")
        self.assertEqual(gate(self.manifest(), None, True)["status"], "ok")

    def test_unknown_entrypoint(self):
        from skill_registry.execute import execute
        m = self.manifest()
        blobs = {"hello.py": b"print('hi')", "SKILL.md": b"# T"}
        with self.assertRaises(ValueError):
            execute(m, lambda p, s: blobs.get(p), "missing.py", approved=True)

    def test_unsupported_entrypoint(self):
        from skill_registry.execute import execute
        m = self.manifest()
        m["artifact"]["files"].append({"path": "doc.pdf", "sha256": "", "size": 1})
        with self.assertRaises(ValueError):
            execute(m, lambda p, s: b"x", "doc.pdf", approved=True)

    def test_live_container_run(self):
        import shutil
        from skill_registry.execute import execute
        if shutil.which("docker") is None:
            self.skipTest("no docker")
        images = subprocess.run(["docker", "images", "-q", "python:3.12-slim"],
                                capture_output=True, text=True, timeout=60)
        if not images.stdout.strip():
            self.skipTest("no python:3.12-slim")
        body = b"import sys\nprint('hello-exact:' + sys.argv[1])\n"
        m = self.manifest()
        for f in m["artifact"]["files"]:
            if f["path"] == "hello.py":
                import hashlib
                f["sha256"] = hashlib.sha256(body).hexdigest()
                f["size"] = len(body)
        blobs = {"hello.py": body, "SKILL.md": b"# T"}
        r = execute(m, lambda p, s: blobs.get(p), "hello.py", args=["42"],
                    approved=True, timeout_s=120)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["exit_code"], 0)
        self.assertIn("hello-exact:42", r["stdout"])
        self.assertEqual(r["verdict"], "sandbox-only")

    def test_server_execute_gate(self):
        from skill_registry import server as mcp_server

        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            raw = {"skill_id": "t/gated", "version": "1.0.0", "name": "G",
                   "description": "gated exec skill",
                   "publisher": {"id": "t"}, "execution_modes": ["executable"],
                   "artifact": {"instruction": "Run me.", "files": []}}
            srv.reg.add(raw)
            r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": "execute",
                                       "arguments": {"skill_id": "t/gated",
                                                     "entrypoint": "run.py"}}})
            body = json.loads(r["result"]["content"][0]["text"])
            self.assertEqual(body["status"], "needs-approval")
            self.assertEqual(body["verdict"], "sandbox-only")
            names = [e["event"] for e in srv.tel.events]
            self.assertNotIn("execution.ok", names)


if __name__ == "__main__":
    unittest.main()
