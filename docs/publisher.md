# Publisher guide

## `skill_id`

Lowercase, one or more segments: `^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$`.
Bad: `BAD`, `Acme/Review`. skills.sh imports use the upstream `id`
(`source/slug`, e.g. `vercel-labs/agent-skills/vercel-react-best-practices`).

## Version

Strict semver `MAJOR.MINOR.PATCH` (`1.2.0`, not `1`). skills.sh has no
versions: every import is `1.0.0` pinned by upstream `hash`.

## Topics / tags

Lowercase keywords. `topics` = domain (`react`), `tags` = retrieval hints
(`react,review,frontend`). Each tag match scores `+2` in `resolve()`.

## Trust

```json
"publisher": {"id": "acme", "verified": true, "official": false},
"trust": {"official": false, "audited": true, "audit_ref": "audit-2026-001", "revoked": false}
```

- `official`: curated-list membership (imports: `extra.official`).
- `audited`: any `status == "pass"` audit; `audit_ref` = `skills.sh:<slug>:<at>`.
- `revoked: true` requires `integrity.sha256` set, else `validate()` fails.

## Executable skills

Ship scripts (`.py`, `.sh`, …) alongside `SKILL.md` and declare them:
`"execution_modes": ["instruction", "executable"]`. The registry serves
the files hash-pinned and marks the skill `sandbox-only`, so agents must
pass explicit approval before the `execute` tool runs anything — exact
scripts in a fresh network-isolated container, never in-process.

## Digest

```python
from skill_registry.manifest import normalize
from skill_registry.cache import Cache
m = normalize(raw)
m["integrity"]["sha256"] = Cache().digest(m)  # or digest(m)
```
`digest()` = sha256 over JSON (sorted keys) of every field except `integrity`.
Recompute after any edit; `verify()` / `get_pinned()` enforce the pin.
