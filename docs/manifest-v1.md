# Manifest v1

Strict subset of `schema/manifest.schema.json`. Unknown top-level keys rejected (`unknown field: <key>`).

| `skill_id` | `publisher/name` | `^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$` (multi-segment for skills.sh `source/slug`) |
|---|---|---|
| `version` | semver | `1.2.0`; skills.sh uses upstream semver when available, otherwise a content-derived `0.0.0+<hash>` |
| `name` | string | non-empty |
| `description` | string | non-empty; SKILL.md paragraph for imports |
| `publisher` | `{id, verified?, official?}` | `id` required |
| `topics` / `tags` | string[] | ranking signals (`tag:+2`) |
| `pack` | string\|null | grouping |
| `execution_modes` | 1+ of `instruction,tool,workflow,executable` | empty rejected; imports infer `executable` only for supported entrypoints (`.py`, `.sh`, `.js`), yielding a `sandbox-only` policy verdict |
| `compatibility` | `{agent_classes[], harnesses[]}` | empty classes = universal |
| `input_schema` / `output_schema` | object | tool I/O contract |
| `tool_dependencies` | string[] | |
| `permissions` | `{network, filesystem}` | filesystem: `none,read-only,read-write` |
| `trust` | `{official,audited,audit_ref,revoked}` | `revoked` requires `integrity.sha256` |
| `integrity` | `{sha256\|null}` | `digest()` over body minus `integrity` |
| `cache` | `{ttl_seconds,pin_recommended}` | default 3600s |
| `deprecation` | `{deprecated,replaced_by}` | `deprecated` scores `-5` |
| `popularity` | int ≥ 0 | `min(pop/100, 2)` in score |
| `updated_at` | string\|null | |
| `artifact` | `{instruction\|null, tool_ref\|null, workflow\|null, source_hash?, files[], entrypoints[]}` | instruction falls back to `description`; `source_hash` preserves upstream provenance separately from registry integrity; entrypoints must be declared files with a supported runtime |

## Deprecation / replacement

- `deprecation.deprecated: true` demotes (`-5`) but still returns unless filtered.
- `deprecation.replaced_by: "<skill_id>"` names the successor; clients should resolve the successor next.
- Cache evicts entries whose stored `audited: true` flips to `false` on the live object (`audit-withdrawn`).

## Conformance

Fixtures in `examples/conformance/`: `valid-instruction`, `valid-tool`, `invalid-bad-id`, `invalid-bad-version`, `invalid-unknown-field`, `revoked`. `Registry.add()` runs `normalize()` then strict manifest validation. The MCP `validate_skill` tool additionally checks the complete package: frontmatter identity, local Markdown references, file availability, and declared entrypoints. Its `strict` profile also requires `Contract`, `Anti-Patterns`, and `Output Format` sections.
