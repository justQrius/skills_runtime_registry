# Skill Registry

Harness/agent/transport-agnostic on-demand skill registry. Fetches skills from
[skills.sh](https://skills.sh) once, checks them always, and briefs whichever
agent is on duty — with receipts.

- **Search / rank under policy** — keyword + trust + compatibility ranking with
  machine-readable rationale; revoked/denylisted dropped before any agent sees
  them.
- **Multi-file skills** — manifests pin every file (`path/sha256/size`);
  contents served hash-verified from a verified store.
- **Exact execution** — script-bearing skills run in fresh network-isolated
  containers behind explicit approval; instruction skills stay text-only.
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
Tools: `search`, `resolve`, `load`, `get_artifact`, `get_file`, `refresh`
(live import; needs `SKILLS_SH_TOKEN`), `execute` (container runs; approval-gated).

**Run it in cloud:**

```sh
docker build -t skill-registry .
docker run -p 127.0.0.1:8125:8000 \
  -e SKILLS_SH_TOKEN="$SKILLS_SH_TOKEN" \
  -e SKILL_REGISTRY_ADMIN_KEY="<random>" \
  -v skill-data:/data/files \
  -v /var/run/docker.sock:/var/run/docker.sock \
  skill-registry
curl localhost:8125/healthz
```

**Hack on it:**

```sh
PYTHONPATH=python python python/demo.py   # end-to-end proof
PYTHONPATH=python python -m unittest discover -s tests -v
node --input-type=module -e "import('./js/index.js').then(m => console.log(typeof m.resolve))"
```
| Path | What |
|---|---|
| `python/skill_registry/` | Core library (stdlib only): manifest, search, resolve, policy, cache, telemetry, loader, files, ingest, execute |
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
- `CHANGELOG.md` — what changed, per iteration
- `AGENTS.md` — conventions for agents working in this repo
