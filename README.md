# Skill Registry

Harness/agent/transport-agnostic on-demand skill registry. Fetches skills from
[skills.sh](https://skills.sh) through trusted refreshes, checks them always,
and briefs whichever agent is on duty — with receipts.

- **Search / rank under policy** — relevance-first token/morphology matching
  across descriptions, instructions, paths, topics, and tags; policy-gated
  matches remain visible as review candidates with machine-readable rationale.
- **Multi-file skills** — manifests pin every file (`path/sha256/size`);
  contents are served hash-verified individually, in bulk, or as a deterministic
  ZIP with a package receipt.
- **Exact execution** — script-bearing skills run in fresh network-isolated
  containers behind explicit approval; Python, shell, and JavaScript entrypoints
  are declared explicitly and instruction skills stay text-only.
- **Any agent** — MCP server (stdio for local, HTTP for cloud) over the same
  stdlib-only core; Python + JS SDKs for embedding.

## Quickstart

**Use it from an agent (zero config)** — add the MCP server:

```json
{
  "mcpServers": {
    "skill-registry": {
      "command": "python",
      "args": ["<repo>/python/skill_registry/server.py"]
    }
  }
}
```
Tools: `discover` (live skills.sh search, no token), `search`, `resolve`, `list_versions`, `validate_skill`, `load`,
`get_artifact`, `get_file`, `get_files`, `get_package`, `invoke_tool`, `refresh`
(live import; needs `SKILLS_SH_TOKEN`), and `execute` (container runs;
approval-gated).

The served catalog is a curated persisted snapshot rather than a complete live
skills.sh index. Empty discovery results are catalog misses, not proof that no
upstream skill exists; see `docs/mcp-server.md` for safe read-only discovery and
trusted refresh behavior.

Production HTTP deployments can refresh without retaining an expiring token in
Docker. The trusted `skill-registry-refresh` client sends a just-in-time Vercel
OIDC bearer token for one admin-gated request; `ops/install_windows_refresh_task.ps1`
installs the daily workstation automation used by this deployment.

**Run it in cloud:**

```sh
docker build -t skill-registry:v1.3.0 .
docker run -d --name skill-registry --restart unless-stopped \
  -p 127.0.0.1:8125:8000 \
  -e SKILL_REGISTRY_ADMIN_KEY="<random>" \
  -v skill-data:/data/files \
  -v /var/run/docker.sock:/var/run/docker.sock \
  skill-registry:v1.3.0
curl localhost:8125/healthz
```

**Hack on it:**

```sh
PYTHONPATH=python python python/demo.py   # end-to-end proof
PYTHONPATH=python python -m unittest discover -s tests -v
node js/test.mjs
```
| Path | What |
|---|---|
| `python/skill_registry/` | Core library (stdlib only): manifest/schema validation, search, resolve, policy, cache, telemetry, loading, file ingest, conformance, runtimes, and execution |
| `python/skill_registry/server.py` | MCP server: stdio (local) + `--http` (cloud) |
| `python/demo.py` | End-to-end verification demo |
| `js/index.js` | JS SDK mirror (search/resolve/load/verify) |
| `catalog/` | Bundled offline catalog (served with zero config) |
| `schema/manifest.schema.json` | Manifest v1 JSON Schema |
| `examples/seeds/`, `examples/conformance/` | Seed skills + valid/invalid/revoked fixtures |
| `tests/` | Stdlib unittest suite (the permanent gate) |
| `docs/` | `manifest-v1`, `client-sdk`, `publisher`, `mcp-server` |

## Docs

- `docs/mcp-server.md` — install, tools, safety model
- `docs/manifest-v1.md` — manifest field reference
- `docs/client-sdk.md` — Python + JS snippets, token setup
- `docs/publisher.md` — authoring skills, digests
- `agent_agnostic_skill_registry_prd.md` — product rationale, delivered scope, and roadmap
- `CHANGELOG.md` — what changed, per iteration
- `AGENTS.md` — conventions for agents working in this repo
