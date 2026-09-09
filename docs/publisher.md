# Publisher guide

## `skill_id`

Lowercase, two or more segments: `^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$`.
Bad: `BAD`, `Acme/Review`. skills.sh imports use the upstream `id`
(`source/slug`, e.g. `vercel-labs/agent-skills/vercel-react-best-practices`).

## Version

Strict semver `MAJOR.MINOR.PATCH` (`1.2.0`, not `1`), with prerelease and
build metadata supported. A skills.sh import uses an upstream semver when one
is present; otherwise the upstream content hash becomes a stable
`0.0.0+<hash-prefix>` version. Multiple versions coexist and are resolved in
semantic-version order.

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

Ship supported scripts (`.py`, `.sh`, `.js`) alongside `SKILL.md`, declare
`"execution_modes": ["instruction", "executable"]`, and list runnable paths
in `artifact.entrypoints`. The registry serves the files hash-pinned and marks
the skill `sandbox-only`, so agents must pass explicit approval before
`execute` runs an entrypoint in a fresh isolated container, never in-process.

Use `validate_skill` before publishing. The `ecosystem` profile accepts common
third-party packages while checking structure, frontmatter, references, and
entrypoints. The opinionated `strict` profile also requires explicit
`Contract`, `Anti-Patterns`, and `Output Format` sections.

## Digest

```python
from skill_registry.manifest import normalize
from skill_registry import digest
m = normalize(raw)
m["integrity"]["sha256"] = digest(m)
```
`digest()` = sha256 over JSON (sorted keys) of every field except `integrity`.
Recompute after any edit; `verify()` / `get_pinned()` enforce the pin.
An upstream package hash belongs in `artifact.source_hash`; importers keep it
as provenance and compute `integrity.sha256` over the portable manifest.
