# Changelog

All notable changes, newest first. Updated every iteration.

## Unreleased

- Documented curated-catalog semantics and the safe agent fallback for catalog
  misses: read-only upstream discovery is allowed, local installs are not, and
  agents must never solicit registry admin or Vercel OIDC credentials.
- Documented that `SKILLS_SH_TOKEN` is short-lived, how to interpret refresh
  authentication failures, and that read-only registry operation remains
  available when trusted refresh is not.
- Fixed stdio and HTTP JSON-RPC dispatch so valid JSON arrays and scalars
  return `-32600` instead of crashing the server process or request handler.
- Documentation synchronized with the v1.1 package, SDK, MCP tools, current
  Docker deployment, executable runtime, and delivered product scope.

## 1.1.0 — 2026-09-08

- Whole-package registry: version-aware file persistence and cache keys,
  semantic-version ordering, `list_versions`, bulk `get_files`, and a
  deterministic `get_package` ZIP with archive hash and file inventory.
- Package conformance: `validate_skill` checks `SKILL.md` frontmatter, local
  references, file integrity/availability, and declared entrypoints; the
  `strict` profile adds Contract/Anti-Patterns/Output Format requirements.
- Discovery: token/morphology matching now indexes descriptions, instructions,
  file paths, topics, tags, publisher, and pack. Resolution enforces relevance
  before trust and returns matching policy-gated skills as `review_candidates`.
- Activation: executable and workflow metadata is explicit; tool-mode skills
  can be invoked through configured server-side handlers with policy and
  dependency-free JSON Schema validation. Standalone servers report missing
  handlers honestly instead of pretending a tool reference is callable.
- Runtime: declared Python, shell, and JavaScript entrypoints; validated request
  limits; separate stdout/stderr; failure/rejection telemetry; POSIX artifact
  paths; locale-independent UTF-8 Docker logs; 25MB/200-artifact output limits
  with truncation receipts.
- Dependency hardening: directives, URLs, VCS/local dependencies, and source
  distributions fail closed; dependency builds get network only when needed,
  execution stays offline, and receipts disclose exact-pin reproducibility.
- Import integrity: content-derived fallback versions, richer upstream metadata,
  strict nested manifest validation, safe path/entrypoint checks, exact binary
  retrieval, canonical registry digests distinct from upstream source hashes,
  and atomic bundle size preflight (10MB/file, 100MB/package).
- Python package and MCP server version advanced to 1.1.0; JS parity now has a
  permanent executable test harness.

- `execute` inputs plane: `inputs: [{path, text} | {path, b64}]` staged to
  `/inputs/<path>` (10MB/file, 50MB total, traversal refused; baked into an
  ephemeral per-run layer over the cached skill image). Declared entrypoints
  run from the pinned tree. Skill writes to `/scratch/<path>`; artifacts
  now add `contents_b64` (exact bytes ≤10MB, incl. binaries) alongside
  `contents`. Container stdout and stderr are preserved separately (tracebacks
  are no longer dropped); traversal checks are hardened cross-platform.
- Fixed: `/scratch` was a tmpfs mount, which `docker cp` cannot see — every
  run returned `artifacts: []`. `/scratch` + `/inputs` are now plain dirs
  baked into the image (`EXEC_RECIPE` 2→4, stale images invalidated);
  `--read-only` dropped for the same reason (throwaway container still:
  `--network none`, capped, removed after with its input layer).
- Exact execution: `skill_registry.execute` (gate → materialize pinned
  tree → per-hash image → fresh `--network none` container → artifacts +
  receipt) and MCP `execute` tool (approval-gated, telemetry
  `execution.ok|fail`). Copy-free runs (tree baked in image, plain
  `/scratch` dir) for Docker-out-of-Docker; `requirements.txt` found anywhere;
  images tagged with build recipe (`EXEC_RECIPE`).
- Security: `refresh` over HTTP needs `SKILL_REGISTRY_ADMIN_KEY`
  (`admin_key` arg or `X-Admin-Key` header; `-32004` on denial).
- Server telemetry (`<data>/telemetry.jsonl`) + imported manifests persist
  and reload (`*.manifest.json`) — refreshes survive restarts.
- `load` strips YAML frontmatter from agent context; `fetch_page` strips
  bearer tokens (Windows `\r` hygiene).
- Tests at 77 (manifest/package integrity, versions, discovery, policy,
  activation, telemetry, persistence, HTTP, catalog parity, and execution).
- Initial production cut: `tests/test_registry.py` began with 23 stdlib
  unittests and remains the permanent Python gate.
- Server moved in-package (`skill_registry.server`); `pip install .` verified
  (`skill-registry-mcp` stdio search green).
- `README.md` quickstart + repo map; `docs/mcp-server.md` pip/Docker/HTTP.

## 1.0.0 — MCP product

- MCP server (`python/skill_registry/server.py`): stdio + `--http` (with `/healthz`),
  tools `search/resolve/load/get_artifact/get_file/refresh`.
- Multi-file skills: `artifact.files[]` (`path/sha256/size`), verified
  `FileStore` (1MB/file, 10MB/bundle caps), lazy hash-checked fetch.
- Bundled offline `catalog/`; `refresh` needs `SKILLS_SH_TOKEN`, else
  `unconfigured` (zero-touch install).
- Packaging: `pyproject.toml` (`skill-registry-mcp`), `Dockerfile` + healthcheck.
- Docs spine: `README.md`, `AGENTS.md`, `docs/mcp-server.md`.
- Live-driven fixes: `skills/audit/{id}` path; multi-segment `skill_id`;
  frontmatter-stripped descriptions; single-char id segments.

## 0.1.0 — Library MVP (PRD)

- Manifest v1 strict validate + `examples/conformance/` fixtures.
- Policy engine (`decide`) + `resolve` gate (`policy`/`require_review`).
- `get_artifact`, session-binding `load`/`unload`.
- Telemetry event gate + cache hash-pinning (`get_pinned`, audit-withdrawal).
- JS SDK parity (search/resolve/verify, digest-compatible).
- skills.sh ingester (`fetch_page`/`to_manifest`/`import_ids`).
- `docs/manifest-v1.md`, `docs/client-sdk.md`, `docs/publisher.md`.
