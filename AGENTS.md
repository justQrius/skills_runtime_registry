# AGENTS.md — skills_runtime_registry

Stdlib-only repo (no runtime deps). Python >= 3.10.

## Commands

```sh
PYTHONPATH=python python python/demo.py        # end-to-end proof, must pass
PYTHONPATH=python python -m unittest discover -s tests
```

JS check: `node --input-type=module -e "import('./js/index.js').then(...)"`.
MCP stdio/HTTP probes are throwaway scripts in /tmp, never committed.

## Conventions

- Library lives in `python/skill_registry/`; one concern per module.
- `manifest.py` + `schema/manifest.schema.json` + `docs/manifest-v1.md`
  change together (code, schema, docs — same commit).
- Python `digest()` is canonical: JSON sorted keys, `", "`/`": "` separators.
  JS `canonical()` must match byte-for-byte; verify with
  `examples/conformance/valid-instruction.json` digests.
- `loader.load` keeps executable/workflow `deferred: True` — in-process
  execution never happens; exact runs go only through
  `skill_registry.execute` (policy gate + approval + fresh container).
- Policy verdicts (`policy.py`) gate before scoring; new verdicts need
  `resolve` + JS `decide` + `docs/mcp-server.md` updates.
- `EXEC_RECIPE` (`execute.py`) bumps whenever the generated Dockerfile
  changes — stale per-hash images must never be reused.
- Every change updates `CHANGELOG.md` (Unreleased section) and any affected
  doc in `docs/`. No UIs, no diagrams, no new docs beyond `docs/` + root
  spine unless asked.
