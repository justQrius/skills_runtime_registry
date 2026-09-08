# Client SDK

## Python

```python
from skill_registry import Registry, search, resolve, load, unload
import json

reg = Registry()
reg.load_dir("examples/seeds")
items = reg.all()

hits = search(items, "react")  # [acme/react-review]
res = resolve(items, official_only=True, audited_only=True,
              agent_class="workflow-agent")  # top: official/procurement-flow
top = reg.get(res["candidates"][0]["skill_id"])

session: dict = {}
out = load(top, session)  # instruction -> session["context_blocks"]
unload(top, session)

artifact = reg.get_artifact("acme/react-review")
```

Enterprise gate: `official_only` / `audited_only` drop non-matching candidates
before scoring. `policy={...}` + `require_review=True` keeps
`require-review` / `sandbox-only` with `policy:<verdict>` rationale instead.

## Exact execution

```python
from skill_registry import execute_skill

r = execute_skill(manifest, read_bytes, "scripts/run.py", args=["--help"],
                  approved=True)  # sandbox-only verdicts need approved=True
# r -> {status, exit_code, stdout, stderr, duration_ms, image, artifacts}
# without approval: {"status": "needs-approval", "verdict": ..., ...}
# agent files in, skill outputs out:
r = execute_skill(manifest, read_bytes, "scripts/run.py",
                  args=["merge", "/inputs/a.pdf", "/inputs/b.pdf",
                        "--output", "/scratch/merged.pdf"],
                  inputs=[{"path": "a.pdf", "b64": "..."},
                          {"path": "b.pdf", "b64": "..."}],
                  approved=True)
# artifacts -> [{path, size, sha256, contents, contents_b64}]
```

`read_bytes(path, sha256)` returns exact bytes (e.g. `FileStore.get_bytes`).
Runs in a fresh `--network none` container; needs a Docker daemon.

## JS

```js
import { search, resolve, load, verify } from "./js/index.js";
const hits = search(items, "procurement");
const r = resolve(items, { tags: ["react"], officialOnly: true, auditedOnly: true });
const ctx = load(hits[0]);
verify(hits[0], hits[0].integrity.sha256); // true when pinned
```

## skills.sh token (Vercel OIDC)

```python
import os
token = os.environ["SKILLS_SH_TOKEN"]  # caller-supplied Vercel OIDC bearer
# getVercelOidcToken(): fetch a Vercel OIDC token for your team/project,
# export it as SKILLS_SH_TOKEN, then call import_ids(ids, base, token).
```

Windows hygiene: `print()` emits `\r\n` and shell `$()` strips only `\n`,
leaving a trailing `\r` that servers reject as an invalid header value.
Always `.strip()` the token and strip `\r` before use or export.
