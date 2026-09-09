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
docker build -t skill-registry:v1.1.0 .
docker run -d --name skill-registry --restart unless-stopped \
  -p 127.0.0.1:8125:8000 \
  -e SKILLS_SH_TOKEN="$SKILLS_SH_TOKEN" \
  -e SKILL_REGISTRY_ADMIN_KEY="<random>" \
  -v skill-data:/data/files \
  -v /var/run/docker.sock:/var/run/docker.sock \
  skill-registry:v1.1.0
curl localhost:8125/healthz
```

Cloud mode serves JSON-RPC at `POST /mcp` plus `GET /healthz`
(container HEALTHCHECK wired). Flags: `--catalog DIR` (default bundled
`catalog/`), `--data DIR` (default `~/.skill-registry/files`,
`SKILL_REGISTRY_DATA` env or `/data/files` in Docker). HTTP request bodies are
limited to 2MB; artifact and package responses may be larger.

## Tools

| Tool | Args | Returns |
|---|---|---|
| `search` | `query, topic?, pack?, publisher?, agent_class?, execution_mode?, official_only?, audited_only?, include_revoked?, include_deprecated?, limit?` | relevance-ranked cards with identity, discovery, trust, compatibility, permissions, modes, and entrypoints |
| `resolve` | `task, tags?, agent_class?, allowed_modes?, allowlist?, denylist?, official_only?, audited_only?, policy?, require_review?, limit?` | relevant `candidates`, gated `review_candidates`, rationale, policy verdicts, and fallback |
| `list_versions` | `skill_id` | semantic-version-ordered versions and latest |
| `validate_skill` | `skill_id, version?, profile?` | package conformance report (`ecosystem` or `strict`) |
| `load` | `skill_id, version?` | instruction context, workflow definition, or tool binding availability plus entrypoints and files |
| `get_artifact` | `skill_id, version?` | modes, instruction/tool/workflow payload, schemas, provenance, files, and entrypoints |
| `get_file` | `skill_id, path, version?` | one verified file (`contents` for UTF-8 and exact `contents_b64`) |
| `get_files` | `skill_id, version?, paths?` | selected or all verified files in one round trip |
| `get_package` | `skill_id, version?` | deterministic ZIP, package hash, and file inventory receipt |
| `invoke_tool` | `skill_id, version?, input?, policy?, approved?` | schema-validated call through a configured server-side handler, or an explicit unavailable/gated result |
| `refresh` | `ids[], official_ids?, admin_key?` | imports live from skills.sh (needs token + admin key over HTTP) |
| `execute` | `skill_id, version?, entrypoint, args?, inputs?, policy?, approved?, timeout_s?` | container run, separate stdout/stderr, dependencies, artifact receipt, or a gated result |
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
Retrieval returns bytes only when they match the pinned hash; oversize files
(>10MB) or bundles (>100MB) are refused at import. `get_files` avoids a
round trip per file; `get_package` returns the entire verified tree as a
deterministic uncompressed ZIP with its own hash and file inventory. Binary
files use `contents_b64` and are never decoded lossily. File contents persist
under `~/.skill-registry/files` (`--data` overrides, `/data/files` in Docker);
imported manifests persist alongside (`*.manifest.json`) and reload on
startup, keyed by skill and version, so refreshes survive restarts and old
versions remain addressable. The catalog bundle
lives in `catalog/` (`--catalog` overrides).

## Telemetry

Applicable tool operations append events to `<data>/telemetry.jsonl`, including
`search.requested`, `candidates.returned`, `skill.selected`, `skill.rejected`,
`fetch.ok|fail`, `integrity.ok|fail`, `cache.hit|miss`, and
`execution.ok|fail`. A single call emits only the events relevant to its path.
The event names match the library.

## Exact execution (`execute` tool)

`execute {skill_id, version?, entrypoint, args?, inputs?, policy?, approved?, timeout_s?}`
runs the skill's pinned file tree in a fresh container and returns separate
`stdout/stderr`, `exit_code`, `duration_ms`, image and dependency receipts,
plus `artifacts` and `artifact_receipt`. Only paths declared in
`artifact.entrypoints` may run — pick one from `load.entrypoints`.
`artifacts` carry text in `contents` and exact bytes in `contents_b64`
(both when ≤10MB/file). Up to 200 artifacts and 25MB total are returned;
the receipt discloses count/byte truncation. Mechanics:

- **Agent files in:** pass `inputs: [{path, text} | {path, b64}]`
  (10MB/file, 50MB total, 100 files; traversal, duplicate paths, malformed
  base64, and non-string args refused). They are staged to
  `/inputs/<path>` — reference them from `args`
  (e.g. `args: ["merge", "/inputs/a.pdf", "/inputs/b.pdf",
  "--output", "/scratch/merged.pdf"]`).
- **Skill outputs out:** the skill writes results to `/scratch/<path>`;
  everything under `/scratch` returns as `artifacts` (hash-pinned
  `path/size/sha256` + payload). Binary outputs (PDFs, images) arrive as
  `contents_b64` — decode to exact bytes.
- **Gate first:** the caller's `policy` runs through `decide`. Anything but
  `allow` (e.g. `sandbox-only` for script-bearing skills) needs
  `approved: true`, else the result is `needs-approval` and nothing runs.
- **Exact tree:** all pinned files materialized byte-identical, relative
  paths preserved; unknown entrypoints and path traversal refused. Runs
  are copy-free (tree baked into the per-hash image, `/scratch` + `/inputs`
  dirs baked into the image, agent inputs in an ephemeral per-run layer),
  so execution works whether the server runs on the host or in a container.
- **Isolation:** `--network none` (no bind mounts — tree and inputs ride
  image layers), 512MB RAM, 1 CPU, 120s default timeout; container +
  ephemeral input layer removed after. The container layer is writable so
  `docker cp` can retrieve `/scratch` (`docker cp` cannot see tmpfs
  mounts); skills read `/inputs`, write `/scratch`, and any tree
  tampering dies with the throwaway container.
- **Dependencies:** `requirements.txt` (found anywhere in the declared tree)
  installs binary wheels into a per-skill-hash image, built once and reused.
  Requirement directives, alternate indexes, URLs, VCS refs, and local paths
  fail closed. The build has network only when packages must be fetched; the
  execution container always has `--network none`. A dependency receipt
  reports declared files/count and whether every package is exactly pinned.
  Images are versioned by build recipe (`EXEC_RECIPE`) so runtime fixes never
  reuse stale layers. Supported entrypoints: `.py`, `.sh`, `.js`.
- **Host requirement:** the server needs a Docker daemon. In-container
  deployments mount it explicitly:
  `-v /var/run/docker.sock:/var/run/docker.sock` (grants the container
  Docker control — appropriate for single-tenant local runs, never for
  shared hosts without further hardening).

## Safety model

Policy runs inside `resolve`: revoked and denylisted skills are dropped;
unaudited/unofficial and executable matches are surfaced separately as review
candidates unless the caller explicitly includes them. Instruction context is
served without YAML frontmatter (`get_file` returns raw verified content).
Tool invocation validates both declared schemas and only calls handlers
configured by the embedding server. Exact execution happens only in throwaway
containers behind explicit approval—never in-process.
