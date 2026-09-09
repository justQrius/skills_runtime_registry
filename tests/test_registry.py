"""Permanent gate: stdlib unittest over the whole registry + MCP handler."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import base64
import io
import zipfile
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

    def test_valid_fixture_integrity_survives_normalization(self):
        for f in ("valid-instruction.json", "valid-tool.json"):
            with self.subTest(f):
                m = normalize(fixture(f))
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

    def test_wrong_types_are_rejected_before_search_can_crash(self):
        m = normalize(fixture("valid-instruction.json"))
        m["popularity"] = "lots"
        m["topics"] = "react"
        errs = validate(m)
        self.assertIn("popularity must be a non-negative integer", errs)
        self.assertIn("topics must be an array of strings", errs)

    def test_mode_specific_artifacts_are_required(self):
        tool = normalize(fixture("valid-tool.json"))
        tool["artifact"]["tool_ref"] = None
        self.assertIn("tool mode requires artifact.tool_ref", validate(tool))

        executable = normalize({
            "skill_id": "acme/runner", "version": "1.0.0", "name": "Runner",
            "description": "Runs things.", "publisher": {"id": "acme"},
            "execution_modes": ["executable"], "artifact": {"files": []},
        })
        self.assertIn("executable mode requires artifact.entrypoints", validate(executable))

        workflow = normalize({
            "skill_id": "acme/workflow", "version": "1.0.0", "name": "Workflow",
            "description": "Runs a workflow.", "publisher": {"id": "acme"},
            "execution_modes": ["workflow"],
        })
        self.assertIn("workflow mode requires artifact.workflow", validate(workflow))

    def test_unsafe_and_duplicate_package_paths_are_rejected(self):
        m = normalize(fixture("valid-instruction.json"))
        m["artifact"]["files"] = [
            {"path": "../escape.py", "sha256": None, "size": 1},
            {"path": "../escape.py", "sha256": None, "size": 1},
        ]
        errs = validate(m)
        self.assertTrue(any("unsafe artifact file path" in e for e in errs), errs)
        self.assertTrue(any("duplicate artifact file path" in e for e in errs), errs)

    def test_normalize_migrates_legacy_multisegment_publisher_and_pack(self):
        raw = dict(fixture("valid-instruction.json"))
        raw["skill_id"] = "vercel-labs/agent-skills/react-best-practices"
        raw["publisher"] = {"id": "vercel-labs/agent-skills"}
        raw["pack"] = None
        m = normalize(raw)
        self.assertEqual(m["publisher"]["id"], "vercel-labs")
        self.assertEqual(m["pack"], "vercel-labs/agent-skills")


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

    def test_search_stems_terms_and_indexes_instruction_and_file_paths(self):
        item = normalize({
            "skill_id": "acme/pdf-kit", "version": "1.0.0", "name": "PDF kit",
            "description": "Utilities for rotating documents.",
            "publisher": {"id": "acme"}, "execution_modes": ["instruction"],
            "artifact": {
                "instruction": "Add a watermark to confidential files.",
                "files": [{"path": "scripts/rotate_pages.py", "sha256": None, "size": 0}],
            },
        })
        for query in ("rotate", "rotation", "watermark", "rotate pages"):
            with self.subTest(query=query):
                self.assertEqual([m["skill_id"] for m in search([item], query)],
                                 ["acme/pdf-kit"])

    def test_search_requires_all_meaningful_query_terms(self):
        items = seed_registry().all()
        self.assertEqual(search(items, "discover submarine"), [])


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

    def test_resolve_does_not_rank_irrelevant_skills_on_trust_alone(self):
        result = resolve(seed_registry().all(), task="repair submarine sonar")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["fallback"], "fall back to native reasoning")

    def test_resolve_reports_relevant_gated_candidates(self):
        item = normalize({
            "skill_id": "acme/pdf-runner", "version": "1.0.0", "name": "PDF runner",
            "description": "Rotate PDF documents.", "publisher": {"id": "acme"},
            "execution_modes": ["executable"],
            "artifact": {"files": [{"path": "run.py", "sha256": None, "size": 0}],
                         "entrypoints": ["run.py"]},
        })
        result = resolve([item], task="rotate a PDF")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["fallback"], "matching skills require review")
        self.assertEqual(result["review_candidates"][0]["skill_id"], "acme/pdf-runner")

    def test_resolve_rejects_non_positive_limits(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            resolve(seed_registry().all(), task="react", limit=-1)


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

    def test_cache_keeps_multiple_versions(self):
        c = Cache()
        for version in ("1.9.0", "1.10.0"):
            m = normalize(fixture("valid-instruction.json"))
            m["version"] = version
            m["integrity"]["sha256"] = None
            c.put(m)
        self.assertEqual(c.get_version("acme/react-review", "1.9.0")["version"],
                         "1.9.0")
        self.assertEqual(c.get("acme/react-review")["version"], "1.10.0")


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

    def test_hybrid_load_exposes_all_modes_and_entrypoints(self):
        m = normalize({
            "skill_id": "acme/hybrid", "version": "1.0.0", "name": "Hybrid",
            "description": "Instructions and a runner.", "publisher": {"id": "acme"},
            "execution_modes": ["instruction", "executable"],
            "artifact": {"instruction": "Use this.",
                         "files": [{"path": "run.py", "sha256": None, "size": 0}],
                         "entrypoints": ["run.py"]},
        })
        out = load(m)
        self.assertEqual(out["kind"], "instruction")
        self.assertEqual(out["execution_modes"], ["instruction", "executable"])
        self.assertEqual(out["entrypoints"], ["run.py"])
        self.assertTrue(out["requires_approval"])

    def test_workflow_load_returns_portable_definition(self):
        definition = {"steps": [{"id": "review", "skill_id": "acme/react-review"}]}
        m = normalize({
            "skill_id": "acme/workflow", "version": "1.0.0", "name": "Workflow",
            "description": "Runs a workflow.", "publisher": {"id": "acme"},
            "execution_modes": ["workflow"], "artifact": {"workflow": definition},
        })
        out = load(m)
        self.assertEqual(out["kind"], "workflow")
        self.assertEqual(out["workflow"], definition)
        self.assertFalse(out["deferred"])


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
        self.assertEqual(m["artifact"]["source_hash"], "abc")
        self.assertTrue(Cache().verify(m))

    def test_mapping_uses_owner_as_publisher_repo_as_pack_and_supported_entrypoints(self):
        d = {"id": "vercel-labs/agent-skills/x", "slug": "x", "hash": "a" * 64,
             "version": "2.3.4", "topics": ["react"],
             "files": [{"path": "SKILL.md", "contents": "# X\n\nGuide."},
                       {"path": "run.js", "contents": "console.log(1)"},
                       {"path": "build.ts", "contents": "console.log(2)"}]}
        m = normalize(to_manifest(d, None))
        self.assertEqual(m["publisher"]["id"], "vercel-labs")
        self.assertEqual(m["pack"], "vercel-labs/agent-skills")
        self.assertEqual(m["topics"], ["react"])
        self.assertEqual(m["version"], "2.3.4")
        self.assertEqual(m["artifact"]["entrypoints"], ["run.js"])
        self.assertIn("executable", m["execution_modes"])

    def test_mapping_does_not_advertise_unsupported_runtimes(self):
        d = {"id": "acme/ts-only", "slug": "ts-only", "hash": "b" * 64,
             "files": [{"path": "SKILL.md", "contents": "# T\n\nGuide."},
                       {"path": "run.ts", "contents": "console.log(1)"}]}
        m = normalize(to_manifest(d, None))
        self.assertEqual(m["execution_modes"], ["instruction"])
        self.assertEqual(m["artifact"]["entrypoints"], [])

    def test_frontmatter_stripped(self):
        d = {"id": "a/b", "slug": "b", "installs": 1, "hash": "h",
             "files": [{"path": "SKILL.md",
                        "contents": "---\nname: x\n---\n\n# T\n\nReal desc."}]}
        self.assertEqual(to_manifest(d, None)["description"], "Real desc.")

    def test_frontmatter_description_is_indexed(self):
        d = {"id": "a/b", "slug": "b", "installs": 1, "hash": "h",
             "files": [{"path": "SKILL.md",
                        "contents": "---\nname: x\ndescription: Rotate and watermark PDFs.\n---\n\n# T\n\nBody."}]}
        self.assertEqual(to_manifest(d, None)["description"],
                         "Rotate and watermark PDFs.")

    def test_audited_only_rejects_before_package_persistence(self):
        from skill_registry.ingest import import_ids

        detail = {
            "id": "owner/pack/unaudited",
            "slug": "unaudited",
            "hash": "source-hash",
            "files": [{"path": "SKILL.md", "contents": "# Unsafe"}],
        }
        with tempfile.TemporaryDirectory() as d:
            store = FileStore(d)
            with mock.patch("skill_registry.ingest.fetch_page",
                            side_effect=[detail, {"audits": []}]):
                with self.assertRaisesRegex(PermissionError, "not audited"):
                    import_ids([detail["id"]], "https://skills.sh", "token",
                               files=store, audited_only=True)
            self.assertEqual(list(Path(d).glob("*.json")), [])

    def test_audited_only_validates_entire_batch_before_persistence(self):
        from skill_registry.ingest import import_ids

        accepted = {
            "id": "owner/pack/accepted", "slug": "accepted", "hash": "one",
            "files": [{"path": "SKILL.md", "contents": "# Accepted"}],
        }
        rejected = {
            "id": "owner/pack/rejected", "slug": "rejected", "hash": "two",
            "files": [{"path": "SKILL.md", "contents": "# Rejected"}],
        }
        passed_audit = {"audits": [{"provider": "Snyk", "status": "pass",
                                     "auditedAt": "2026-09-09T00:00:00Z"}]}
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("skill_registry.ingest.fetch_page", side_effect=[
                accepted, passed_audit, rejected, {"audits": []},
            ]):
                with self.assertRaises(PermissionError):
                    import_ids([accepted["id"], rejected["id"]],
                               "https://skills.sh", "token",
                               files=FileStore(d), audited_only=True)
            self.assertEqual(list(Path(d).glob("*.json")), [])

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
        self.assertEqual(fs.put("a/b", "medium.bin", b"x" * 1_000_001)["size"],
                         1_000_001)
        with self.assertRaises(ValueError):
            fs.put("a/b", "big.bin", b"x" * (10_000_001))

    def test_binary_files_roundtrip_exactly_over_mcp_shape(self):
        fs = FileStore()
        raw = bytes(range(256))
        meta = fs.put("a/b", "asset.bin", raw, version="1.0.0")
        doc = fs.get("a/b", "asset.bin", meta["sha256"], version="1.0.0")
        self.assertIsNone(doc["contents"])
        self.assertEqual(base64.b64decode(doc["contents_b64"]), raw)

    def test_versions_do_not_overwrite_each_others_files(self):
        with tempfile.TemporaryDirectory() as d:
            fs = FileStore(d)
            one = fs.put("a/b", "SKILL.md", "one", version="1.0.0")
            two = fs.put("a/b", "SKILL.md", "two", version="2.0.0")
            reopened = FileStore(d)
            self.assertEqual(reopened.get("a/b", "SKILL.md", one["sha256"],
                                          version="1.0.0")["contents"], "one")
            self.assertEqual(reopened.get("a/b", "SKILL.md", two["sha256"],
                                          version="2.0.0")["contents"], "two")

    def test_bundle_write_is_atomic_when_any_file_is_too_large(self):
        fs = FileStore()
        with self.assertRaisesRegex(ValueError, "file too large"):
            fs.put_bundle("a/b", "1.0.0", [
                ("small.txt", b"small"),
                ("large.bin", b"x" * 10_000_001),
            ])
        self.assertIsNone(fs.get("a/b", "small.txt", version="1.0.0"))


class StoreVersionTest(unittest.TestCase):
    def test_latest_uses_semver_not_lexical_order(self):
        reg = Registry()
        for version in ("1.9.0", "1.10.0"):
            raw = dict(fixture("valid-instruction.json"))
            raw["version"] = version
            reg.add(raw)
        self.assertEqual(reg.get("acme/react-review")["version"], "1.10.0")


class JsParityTest(unittest.TestCase):
    def test_js_sdk_conformance(self):
        import shutil
        if shutil.which("node") is None:
            self.skipTest("node unavailable")
        run = subprocess.run(["node", "js/test.mjs"], cwd=ROOT,
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("js sdk conformance ok", run.stdout)


class McpTest(unittest.TestCase):
    def test_handle(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
        init = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"]["name"], "skill-registry")

        names = [t["name"] for t in
                 srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                 ["result"]["tools"]]
        self.assertEqual(names, ["search", "resolve", "list_versions", "validate_skill",
                                 "load", "get_artifact",
                                 "get_file", "get_files", "get_package",
                                 "invoke_tool", "refresh", "execute"])

        def text(resp):
            return json.loads(resp["result"]["content"][0]["text"])

        r = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "search", "arguments": {"query": "react"}}})
        cards = text(r)
        self.assertEqual([m["skill_id"] for m in cards], ["acme/react-review"])
        for field in ("name", "publisher", "topics", "tags", "pack", "permissions",
                      "compatibility", "integrity", "entrypoints"):
            self.assertIn(field, cards[0])

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

    def test_stdio_rejects_non_object_json_and_continues(self):
        requests = [
            [], None, "invalid", 7, True,
            {"jsonrpc": "2.0", "id": 9, "method": "ping"},
        ]
        with tempfile.TemporaryDirectory() as data:
            run = subprocess.run(
                [sys.executable, str(ROOT / "python" / "skill_registry" / "server.py"),
                 "--catalog", str(ROOT / "catalog"), "--data", data],
                input="\n".join(json.dumps(request) for request in requests) + "\n",
                capture_output=True, text=True, timeout=10,
            )

        self.assertEqual(run.returncode, 0, run.stderr)
        responses = [json.loads(line) for line in run.stdout.splitlines()]
        self.assertEqual([response["error"]["code"] for response in responses[:-1]],
                         [-32600] * 5)
        self.assertEqual(responses[-1]["result"], {})

    def test_search_limit_is_declared_and_validated(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
        search_schema = srv.TOOLS["search"][1]
        self.assertIn("limit", search_schema["properties"])
        with self.assertRaisesRegex(ValueError, "limit"):
            srv.t_search({"limit": -1})

    def test_list_versions_is_semver_ordered(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
        for version in ("1.9.0", "1.10.0"):
            raw = dict(fixture("valid-instruction.json"))
            raw["version"] = version
            srv.reg.add(raw)
        result = srv.t_list_versions({"skill_id": "acme/react-review"})
        self.assertEqual(result["versions"][:2], ["1.10.0", "1.9.0"])

    def test_validate_skill_supports_ecosystem_and_strict_profiles(self):
        import hashlib
        from skill_registry import server as mcp_server

        body = (b"---\nname: sample\ndescription: Sample skill.\n---\n\n# Sample\n\n"
                b"See `references/guide.md`.\n")
        reference = b"# Guide\n"
        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            raw = normalize({
                "skill_id": "test/sample", "version": "1.0.0", "name": "Sample",
                "description": "Sample skill.", "publisher": {"id": "test"},
                "execution_modes": ["instruction"], "artifact": {"instruction": body.decode(),
                    "files": [
                        {"path": "SKILL.md", "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)},
                        {"path": "references/guide.md", "sha256": hashlib.sha256(reference).hexdigest(), "size": len(reference)},
                    ]},
            })
            srv.reg.add(raw)
            srv.files.put_bundle(raw["skill_id"], raw["version"],
                                 [("SKILL.md", body), ("references/guide.md", reference)])
            ecosystem = srv.t_validate_skill({"skill_id": raw["skill_id"]})
            self.assertTrue(ecosystem["valid"])
            strict = srv.t_validate_skill({"skill_id": raw["skill_id"], "profile": "strict"})
            self.assertFalse(strict["valid"])
            self.assertIn("missing section: Contract", strict["issues"])

    def test_bulk_files_and_deterministic_package_archive(self):
        from skill_registry import server as mcp_server

        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            raw = dict(fixture("valid-instruction.json"))
            body = b"---\nname: x\ndescription: y\n---\n\n# X\n"
            import hashlib
            raw["artifact"] = {"instruction": body.decode(), "tool_ref": None,
                               "files": [{"path": "SKILL.md",
                                          "sha256": hashlib.sha256(body).hexdigest(),
                                          "size": len(body)}]}
            raw["integrity"] = {"sha256": None}
            srv.reg.add(raw)
            srv.files.put(raw["skill_id"], "SKILL.md", body, version=raw["version"])

            files = srv.t_get_files({"skill_id": raw["skill_id"]})
            self.assertEqual([f["path"] for f in files["files"]], ["SKILL.md"])
            package1 = srv.t_get_package({"skill_id": raw["skill_id"]})
            package2 = srv.t_get_package({"skill_id": raw["skill_id"]})
            self.assertEqual(package1["sha256"], package2["sha256"])
            self.assertEqual(package1["files"], raw["artifact"]["files"])
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(package1["contents_b64"]))) as z:
                self.assertEqual(z.read("SKILL.md"), body)

    def test_execute_distinguishes_unknown_version_from_unknown_skill(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
        with self.assertRaisesRegex(KeyError, "unknown version"):
            srv.t_execute({"skill_id": "acme/react-review", "version": "9.9.9",
                           "entrypoint": "run.py"})

    def test_tool_invocation_proxy_reports_and_uses_registered_handler(self):
        from skill_registry import server as mcp_server

        unavailable = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp())
        loaded = unavailable.t_load({"skill_id": "official/procurement-flow"})
        self.assertFalse(loaded["available"])
        self.assertEqual(unavailable.t_invoke_tool({
            "skill_id": "official/procurement-flow", "input": {"id": 7},
        })["status"], "unavailable")

        available = mcp_server.Server(
            ROOT / "catalog", tempfile.mkdtemp(),
            tool_handlers={"procurement.approve": lambda value: {"approved": value["id"]}},
        )
        self.assertTrue(available.t_load({"skill_id": "official/procurement-flow"})["available"])
        self.assertEqual(available.t_invoke_tool({
            "skill_id": "official/procurement-flow", "input": {"id": 7},
        })["output"], {"approved": 7})

    def test_tool_invocation_validates_input_and_output_schemas(self):
        from skill_registry import server as mcp_server

        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp(),
                                tool_handlers={"math.double": lambda value: {"result": "bad"}})
        srv.reg.add({
            "skill_id": "test/double", "version": "1.0.0", "name": "Double",
            "description": "Double a number.", "publisher": {"id": "test"},
            "execution_modes": ["tool"], "artifact": {"tool_ref": "math.double"},
            "input_schema": {"type": "object", "required": ["value"],
                             "properties": {"value": {"type": "number"}}},
            "output_schema": {"type": "object", "required": ["result"],
                              "properties": {"result": {"type": "number"}}},
        })
        with self.assertRaisesRegex(ValueError, "input.value"):
            srv.t_invoke_tool({"skill_id": "test/double", "input": {"value": "bad"}})
        with self.assertRaisesRegex(ValueError, "output.result"):
            srv.t_invoke_tool({"skill_id": "test/double", "input": {"value": 2}})

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

    def test_refresh_accepts_request_scoped_token_without_persisting_it(self):
        from skill_registry import server as mcp_server

        old = os.environ.pop("SKILLS_SH_TOKEN", None)
        try:
            srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp(),
                                    admin_key="secret")
            srv.public = True
            with mock.patch("skill_registry.ingest.import_ids",
                            return_value=[]) as imported:
                body = srv.t_refresh({
                    "ids": ["owner/pack/skill"],
                    "_admin_key": "secret",
                    "_skills_sh_token": "fresh-request-token",
                })
            self.assertEqual(body, {"status": "ok", "imported": []})
            self.assertEqual(imported.call_args.args[2], "fresh-request-token")
            self.assertNotIn("fresh-request-token", json.dumps(body))
            self.assertNotIn("SKILLS_SH_TOKEN", os.environ)
        finally:
            if old is not None:
                os.environ["SKILLS_SH_TOKEN"] = old

    def test_http_credentials_are_injected_only_into_refresh(self):
        from skill_registry import server as mcp_server

        refresh = {"params": {"name": "refresh", "arguments": {}}}
        mcp_server._inject_http_credentials(
            refresh, "admin", "Bearer request-token")
        self.assertEqual(refresh["params"]["arguments"], {
            "_admin_key": "admin",
            "_skills_sh_token": "request-token",
        })

        search = {"params": {"name": "search", "arguments": {}}}
        mcp_server._inject_http_credentials(search, "admin", "Bearer secret")
        self.assertEqual(search["params"]["arguments"], {})


class RefreshClientTest(unittest.TestCase):
    def test_sends_credentials_in_headers_and_returns_tool_result(self):
        from skill_registry.refresh_client import refresh_registry

        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                inner = json.dumps({"status": "ok", "imported": []})
                return json.dumps({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {"content": [{"type": "text", "text": inner}]},
                }).encode()

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

        result = refresh_registry(
            "http://127.0.0.1:8125/mcp",
            ["owner/pack/skill"],
            token="oidc-token",
            admin_key="admin-key",
            opener=opener,
        )

        request = captured["request"]
        self.assertEqual(request.get_header("Authorization"), "Bearer oidc-token")
        self.assertEqual(request.get_header("X-admin-key"), "admin-key")
        body = json.loads(request.data)
        self.assertEqual(body["params"]["arguments"], {
            "ids": ["owner/pack/skill"],
            "official_ids": [],
            "audited_only": True,
        })
        self.assertEqual(result, {"status": "ok", "imported": []})

    def test_refuses_plain_http_to_non_loopback_host(self):
        from skill_registry.refresh_client import refresh_registry

        with self.assertRaisesRegex(ValueError, "HTTPS or a loopback"):
            refresh_registry("http://registry.example/mcp", ["a/b/c"],
                             token="token", admin_key="key")


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

    def test_refresh_audited_only_fails_closed_before_registration(self):
        from skill_registry import server as mcp_server

        candidate = dict(fixture("valid-instruction.json"))
        candidate["skill_id"] = "test/unaudited"
        candidate["trust"] = {"official": False, "audited": False,
                              "revoked": False}
        srv = mcp_server.Server(ROOT / "catalog", tempfile.mkdtemp(),
                                admin_key="secret")
        srv.public = True
        with mock.patch("skill_registry.ingest.import_ids",
                        return_value=[candidate]):
            with self.assertRaisesRegex(PermissionError, "not audited"):
                srv.t_refresh({
                    "ids": [candidate["skill_id"]],
                    "audited_only": True,
                    "_admin_key": "secret",
                    "_skills_sh_token": "fresh-token",
                })
        self.assertIsNone(srv.reg.get(candidate["skill_id"]))

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

    def test_manifest_persistence_keeps_multiple_versions(self):
        from skill_registry import server as mcp_server

        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            for version in ("1.0.0", "2.0.0"):
                m = dict(fixture("valid-instruction.json"))
                m["version"] = version
                srv._save_manifest(m)
            srv2 = mcp_server.Server(ROOT / "catalog", d)
            self.assertIsNotNone(srv2.reg.get("acme/react-review", "1.0.0"))
            self.assertIsNotNone(srv2.reg.get("acme/react-review", "2.0.0"))

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

            refresh_body = json.dumps({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "refresh", "arguments": {"ids": ["a/b"]}},
            }).encode()
            refresh_req = urllib.request.Request(
                f"http://127.0.0.1:{port}/mcp",
                data=refresh_body,
                headers={"Authorization": "Bearer request-token",
                         "Content-Type": "application/json",
                         "X-Admin-Key": "secret"})
            with mock.patch("skill_registry.ingest.import_ids",
                            return_value=[]) as imported:
                with urllib.request.urlopen(refresh_req, timeout=5) as r:
                    refresh_resp = json.loads(r.read())
            refresh_result = json.loads(
                refresh_resp["result"]["content"][0]["text"])
            self.assertEqual(refresh_result, {"status": "ok", "imported": []})
            self.assertEqual(imported.call_args.args[2], "request-token")

            bad = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=b"[]",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(bad, timeout=5) as r:
                invalid = json.loads(r.read())
            self.assertEqual(invalid["error"]["code"], -32600)

            ping_body = json.dumps({"jsonrpc": "2.0", "id": 2,
                                    "method": "ping"}).encode()
            ping = urllib.request.Request(f"http://127.0.0.1:{port}/mcp",
                                          data=ping_body,
                                          headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(ping, timeout=5) as r:
                ping_response = json.loads(r.read())
            self.assertEqual(ping_response["result"], {})


class ExecuteTest(unittest.TestCase):
    def manifest(self):
        return {"skill_id": "t/hello", "version": "1.0.0",
                "execution_modes": ["instruction", "executable"],
                "trust": {"audited": False}, "permissions": {},
                "integrity": {"sha256": "0" * 64},
                "artifact": {"files": [
                    {"path": "hello.py", "sha256": "", "size": 0},
                    {"path": "SKILL.md", "sha256": "", "size": 0}],
                    "entrypoints": ["hello.py"]},
                "popularity": 0}

    def test_gate_needs_approval(self):
        from skill_registry.execute import gate
        self.assertEqual(gate(self.manifest(), None, False)["status"], "needs-approval")
        self.assertEqual(gate(self.manifest(), None, True)["status"], "ok")

    def test_docker_text_output_is_decoded_independently_of_host_locale(self):
        from unittest import mock
        from skill_registry.execute import _run_text

        with mock.patch("skill_registry.execute.subprocess.run") as run:
            _run_text(["docker", "version"], timeout=3)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "replace")

    def test_inputs_validated(self):
        from skill_registry.execute import parse_inputs
        self.assertEqual(parse_inputs(None), [])
        self.assertEqual(parse_inputs([]), [])
        self.assertEqual(parse_inputs([{"path": "a.txt", "text": "hi"}]),
                         [("a.txt", b"hi")])
        self.assertEqual(parse_inputs([{"path": "b.bin", "b64": "aGk="}]),
                         [("b.bin", b"hi")])
        for bad in ({"path": "../evil.txt", "text": "x"},
                    {"path": "/abs.txt", "text": "x"},
                    {"path": "nested\\windows.txt", "text": "x"},
                    {"path": "a.txt"},
                    {"path": "a.txt", "text": "x", "b64": "eA=="},
                    {"path": "a.txt", "b64": "!!!"}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_inputs([bad])
        with self.assertRaisesRegex(ValueError, "duplicate input path"):
            parse_inputs([{"path": "same.txt", "text": "one"},
                          {"path": "same.txt", "text": "two"}])
        with self.assertRaises(ValueError):
            parse_inputs([{"path": f"file-{i}.bin", "b64": "eA=="}
                          for i in range(101)])

    def test_inputs_oversize_rejected(self):
        from skill_registry.execute import parse_inputs
        self.assertEqual(len(parse_inputs([
            {"path": "medium.bin", "text": "x" * 1_000_001}
        ])[0][1]), 1_000_001)
        with self.assertRaises(ValueError):
            parse_inputs([{"path": "big.bin", "text": "x" * 10_000_001}])

    def test_dependency_plan_rejects_requirement_injection(self):
        from skill_registry.execute import dependency_plan
        with tempfile.TemporaryDirectory() as d:
            req = Path(d) / "requirements.txt"
            req.write_text("--index-url https://evil.invalid/simple\npackage==1.0.0\n")
            with self.assertRaisesRegex(ValueError, "unsafe requirements directive"):
                dependency_plan(Path(d))

    def test_dependency_plan_reports_reproducibility(self):
        from skill_registry.execute import dependency_plan
        with tempfile.TemporaryDirectory() as d:
            req = Path(d) / "requirements.txt"
            req.write_text("package>=1.0\n")
            self.assertFalse(dependency_plan(Path(d))["reproducible"])
            req.write_text("package==1.2.3\n")
            plan = dependency_plan(Path(d))
            self.assertTrue(plan["reproducible"])
            self.assertTrue(plan["network_required"])

    def test_execution_request_limits_validate_before_docker(self):
        from skill_registry.execute import execute
        m = self.manifest()
        blobs = {"hello.py": b"print('hi')", "SKILL.md": b"# T"}
        with self.assertRaisesRegex(ValueError, "timeout_s"):
            execute(m, lambda p, s: blobs.get(p), "hello.py", approved=True,
                    timeout_s=0)
        with self.assertRaisesRegex(ValueError, "args"):
            execute(m, lambda p, s: blobs.get(p), "hello.py", args=[7],
                    approved=True)

    def test_artifacts_include_b64(self):
        from skill_registry.execute import collect_artifacts
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "out.txt").write_bytes(b"hello")
            (root / "out.pdf").write_bytes(bytes(range(256)))
            arts = {a["path"]: a for a in collect_artifacts(root)}
            self.assertEqual(arts["out.txt"]["contents"], "hello")
            import base64
            self.assertEqual(base64.b64decode(arts["out.pdf"]["contents_b64"]),
                             bytes(range(256)))
            self.assertIsNone(arts["out.pdf"]["contents"])
            self.assertTrue(all("\\" not in a["path"] for a in arts.values()))

    def test_nested_artifact_paths_are_posix(self):
        from skill_registry.execute import collect_artifacts
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "nested" / "out.txt"
            path.parent.mkdir()
            path.write_text("hello")
            self.assertEqual(collect_artifacts(Path(d))[0]["path"],
                             "nested/out.txt")

    def test_artifact_receipt_discloses_truncation(self):
        from skill_registry.execute import collect_artifacts_with_receipt
        with tempfile.TemporaryDirectory() as d:
            for i in range(201):
                (Path(d) / f"{i:03}.txt").write_text(str(i))
            artifacts, receipt = collect_artifacts_with_receipt(Path(d))
            self.assertEqual(len(artifacts), 200)
            self.assertEqual(receipt["discovered"], 201)
            self.assertTrue(receipt["truncated"])
            self.assertIn("artifact-count", receipt["reasons"])

    def test_unknown_entrypoint(self):
        from skill_registry.execute import execute
        m = self.manifest()
        blobs = {"hello.py": b"print('hi')", "SKILL.md": b"# T"}
        with self.assertRaises(ValueError):
            execute(m, lambda p, s: blobs.get(p), "missing.py", approved=True)

    def test_supported_but_undeclared_entrypoint_is_rejected(self):
        from skill_registry.execute import execute
        m = self.manifest()
        m["artifact"]["files"].append({"path": "helper.py", "sha256": "", "size": 0})
        blobs = {"hello.py": b"print('hi')", "helper.py": b"print('wrong')",
                 "SKILL.md": b"# T"}
        with self.assertRaisesRegex(ValueError, "declared entrypoint"):
            execute(m, lambda p, s: blobs.get(p), "helper.py", approved=True)

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
        import hashlib
        m["integrity"]["sha256"] = hashlib.sha256(b"live-run-v1" + body).hexdigest()
        for f in m["artifact"]["files"]:
            if f["path"] == "hello.py":
                f["sha256"] = hashlib.sha256(body).hexdigest()
                f["size"] = len(body)
        blobs = {"hello.py": body, "SKILL.md": b"# T"}
        r = execute(m, lambda p, s: blobs.get(p), "hello.py", args=["42"],
                    approved=True, timeout_s=120)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["exit_code"], 0)
        self.assertIn("hello-exact:42", r["stdout"])
        self.assertEqual(r["verdict"], "sandbox-only")

    def test_live_inputs_roundtrip(self):
        import base64
        import hashlib
        import shutil
        from skill_registry.execute import execute
        if shutil.which("docker") is None:
            self.skipTest("no docker")
        images = subprocess.run(["docker", "images", "-q", "python:3.12-slim"],
                                capture_output=True, text=True, timeout=60)
        if not images.stdout.strip():
            self.skipTest("no python:3.12-slim")
        body = (b"from pathlib import Path\n"
                b"data = Path('/inputs/in.txt').read_bytes()\n"
                b"Path('/scratch/out.bin').write_bytes(data + b'!')\n"
                b"print('staged-ok')\n")
        m = self.manifest()
        m["integrity"]["sha256"] = hashlib.sha256(b"live-inputs-v1" + body).hexdigest()
        for f in m["artifact"]["files"]:
            if f["path"] == "hello.py":
                f["sha256"] = hashlib.sha256(body).hexdigest()
                f["size"] = len(body)
        blobs = {"hello.py": body, "SKILL.md": b"# T"}
        r = execute(m, lambda p, s: blobs.get(p), "hello.py",
                    inputs=[{"path": "in.txt", "text": "payload-42"}],
                    approved=True, timeout_s=120)
        self.assertEqual(r["status"], "ok")
        self.assertIn("staged-ok", r["stdout"])
        arts = {a["path"]: a for a in r["artifacts"]}
        self.assertIn("out.bin", arts)
        self.assertEqual(base64.b64decode(arts["out.bin"]["contents_b64"]),
                         b"payload-42!")

    def test_server_execute_gate(self):
        from skill_registry import server as mcp_server

        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d)
            raw = {"skill_id": "t/gated", "version": "1.0.0", "name": "G",
                   "description": "gated exec skill",
                   "publisher": {"id": "t"}, "execution_modes": ["executable"],
                   "artifact": {"instruction": "Run me.",
                                "files": [{"path": "run.py", "sha256": None, "size": 0}],
                                "entrypoints": ["run.py"]}}
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

    def test_failed_execution_preserves_streams_sets_mcp_error_and_emits_telemetry(self):
        import hashlib
        import shutil
        from skill_registry import server as mcp_server

        if shutil.which("docker") is None:
            self.skipTest("no docker")
        body = b"import sys\nprint('hello')\nprint('boom', file=sys.stderr)\nsys.exit(7)\n"
        with tempfile.TemporaryDirectory() as d:
            srv = mcp_server.Server(ROOT / "catalog", d, admin_key="secret")
            raw = normalize({
                "skill_id": "test/failure", "version": "1.0.0", "name": "Failure",
                "description": "Fails for testing.", "publisher": {"id": "test"},
                "execution_modes": ["executable"],
                "artifact": {"files": [{"path": "fail.py",
                                          "sha256": hashlib.sha256(body).hexdigest(),
                                          "size": len(body)}],
                             "entrypoints": ["fail.py"]},
            })
            srv.reg.add(raw)
            srv.files.put(raw["skill_id"], "fail.py", body, version=raw["version"])
            response = srv.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "execute", "arguments": {
                    "skill_id": raw["skill_id"], "entrypoint": "fail.py",
                    "approved": True,
                }},
            })
            self.assertTrue(response["result"]["isError"])
            result = json.loads(response["result"]["content"][0]["text"])
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["exit_code"], 7)
            self.assertIn("hello", result["stdout"])
            self.assertIn("boom", result["stderr"])
            self.assertIn("execution.fail", [e["event"] for e in srv.tel.events])


if __name__ == "__main__":
    unittest.main()
