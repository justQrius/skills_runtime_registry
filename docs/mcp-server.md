# MCP server

One install, zero config. The server speaks MCP over stdio and serves a
bundled catalog — no token, no REPL, no setup calls.

## Install (local)

Via pip (entry point `skill-registry-mcp`):

```sh
pip install .
# then in MCP client config: { "command": "skill-registry-mcp" }
```

Or direct, no install — add to your MCP client config (Codex, Claude, Hermes):

```json
{
  "mcpServers": {
    "skill-registry": {
      "command": "python",
      "args": ["<repo>/python/skill_registry/server.py"],
      "env": {}
    }
  }
}
```

Optional live refresh from skills.sh — add one env var:

```json
{ "env": { "SKILLS_SH_TOKEN": "<vercel-oidc-token>" } }
```

Without the token every tool works against the bundled catalog; `refresh`
replies `unconfigured` instead of failing.

## Deploy (cloud)

```sh
docker build -t skill-registry .
docker run -p 8000:8000 \
  -e SKILLS_SH_TOKEN="$SKILLS_SH_TOKEN" \
  -v skill-data:/data/files \
  skill-registry
```

Cloud mode serves JSON-RPC at `POST /mcp` plus `GET /healthz`
(container HEALTHCHECK wired). Flags: `--catalog DIR` (default bundled
`catalog/`), `--data DIR` (default `~/.skill-registry/files`,
`SKILL_REGISTRY_DATA` env or `/data/files` in Docker).

## Tools

| Tool | Args | Returns |
|---|---|---|
| `search` | `query, topic?, pack?, publisher?, agent_class?, execution_mode?, official_only?, audited_only?, include_revoked?, include_deprecated?, limit?` | slim cards: id, version, description, trust, popularity |
| `resolve` | `task, tags?, agent_class?, allowed_modes?, allowlist?, denylist?, official_only?, audited_only?, policy?, require_review?, limit?` | ranked candidates with `rationale` + policy verdicts |
| `load` | `skill_id, version?` | instruction context or tool binding + `files[]` metadata |
| `get_artifact` | `skill_id, version?` | instruction, tool_ref, input/output schemas |
| `get_file` | `skill_id, path, version?` | verified file contents (sha256-checked, 1MB/file cap) |
| `refresh` | `ids[], official_ids?, admin_key?` | imports live from skills.sh (needs token + admin key over HTTP) |
| `execute` | `skill_id, entrypoint, args?, policy?, approved?, timeout_s?` | container run: stdout/stderr/exit_code/artifacts, or `needs-approval` |
## Refresh auth

`refresh` spends your token and writes into the server, so over HTTP it is
gated: set `SKILL_REGISTRY_ADMIN_KEY` on the server, then call with
`admin_key` (or `X-Admin-Key` header). Without a server-side key, HTTP
`refresh` is refused; stdio `refresh` stays open (local = trusted).
Docker: `-e SKILL_REGISTRY_ADMIN_KEY="<random>"`, and bind loopback-only
(`-p 127.0.0.1:8125:8000`).

Local-agent convention: keep the key in `~/.skill-registry/admin_key`
(generated once via `secrets.token_hex(16)`) and pass it with each
`refresh` call. The file protects against network callers; local processes
already share your privileges, so this is the correct trust boundary.

## Multi-file skills

Manifests carry `artifact.files[] = {path, sha256, size}` (metadata only).
`get_file` returns bytes only when they match the pinned hash; oversize
files (>1MB) or bundles (>10MB) are refused at import. File contents persist
under `~/.skill-registry/files` (`--data` overrides, `/data/files` in Docker);
imported manifests persist alongside (`*.manifest.json`) and reload on
startup, so refreshes survive restarts. The catalog bundle
lives in `catalog/` (`--catalog` overrides).

## Telemetry

Every tool call appends to `<data>/telemetry.jsonl`: `search.requested` →
`candidates.returned` → `skill.selected` → `fetch.ok|fail` →
`integrity.ok|fail` → `cache.hit|miss` → `execution.ok|fail`.
Same event names as the library.

## Exact execution (`execute` tool)

`execute {skill_id, entrypoint, args?, policy?, approved?, timeout_s?}`
runs the skill's pinned file tree in a fresh container and returns
`stdout/stderr/exit_code/duration_ms/image/artifacts`. Mechanics:

- **Gate first:** the caller's `policy` runs through `decide`. Anything but
  `allow` (e.g. `sandbox-only` for script-bearing skills) needs
  `approved: true`, else the result is `needs-approval` and nothing runs.
- **Exact tree:** all pinned files materialized byte-identical, relative
  paths preserved; unknown entrypoints and path traversal refused. Runs
  are copy-free (tree baked into the per-hash image, scratch on tmpfs),
  so execution works whether the server runs on the host or in a container.
- **Isolation:** `--network none`, read-only tree, 64MB tmpfs scratch,
  512MB RAM, 1 CPU, 120s default timeout; container removed after.
- **Dependencies:** `requirements.txt` (found anywhere in the tree, when
  declared) installed into a per-skill-hash image, built once and reused
  across runs and agents; images are versioned by build recipe
  (`EXEC_RECIPE`) so recipe fixes never reuse stale layers.
  Undeclared deps fail closed. Supported entrypoints: `.py`, `.sh`.
- **Host requirement:** the server needs a Docker daemon. In-container
  deployments mount it explicitly:
  `-v /var/run/docker.sock:/var/run/docker.sock` (grants the container
  Docker control — appropriate for single-tenant local runs, never for
  shared hosts without further hardening).

## Safety model

Policy runs inside `resolve`, before the agent sees anything: revoked and
denylisted are dropped, unaudited/unofficial become `require-review`,
executables become `sandbox-only`. Instruction context is served without
YAML frontmatter (`get_file` returns raw bytes). Exact execution happens
only in throwaway containers behind explicit approval — never in-process.
