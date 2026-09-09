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

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
