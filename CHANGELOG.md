# Changelog

All notable changes, newest first. Updated every iteration.

## Unreleased

- `execute` inputs plane: `inputs: [{path, text} | {path, b64}]` staged to
  `/inputs/<path>` (1MB/file, 10MB total, traversal refused; baked into an
  ephemeral per-run layer over the cached skill image). Any pinned file
  already runs as `entrypoint`. Skill writes to `/scratch/<path>`; artifacts
  now add `contents_b64` (exact bytes ≤100KB, incl. binaries) alongside
  `contents`. Container stderr merged into `stdout` (tracebacks no longer
  dropped); traversal check hardened cross-platform (shared helper).
- Fixed: `/scratch` was a tmpfs mount, which `docker cp` cannot see — every
  run returned `artifacts: []`. `/scratch` + `/inputs` are now plain dirs
  baked into the image (`EXEC_RECIPE` 2→3, stale images invalidated);
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
- Tests at 36 (gates, telemetry, persistence, HTTP, catalog parity, execution).
- Production cut: `tests/test_registry.py` (23 stdlib unittests, the gate).
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
