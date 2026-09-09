# Client SDK

## Python

```python
from skill_registry import Registry, search, resolve, load, unload

reg = Registry()
reg.load_dir("examples/seeds")
items = reg.all()

hits = search(items, "react")  # [acme/react-review]
res = resolve(items, task="approve procurement", official_only=True,
              audited_only=True, agent_class="workflow-agent")
top = reg.get("acme/react-review")

session: dict = {}
out = load(top, session)  # instruction -> session["context_blocks"]
unload(top, session)

artifact = reg.get_artifact("acme/react-review")
```

Enterprise gates (`official_only`, `audited_only`, allow/deny lists) run before
ranking. Relevance runs before trust, so unrelated but highly trusted skills do
not leak into results. When a matching skill is policy-gated and
`require_review=False`, it appears under `review_candidates` with its verdict
instead of silently disappearing; enable `require_review` to promote it into
`candidates`.

## Exact execution

```python
from skill_registry import execute_skill

r = execute_skill(manifest, read_bytes, "scripts/run.py", args=["--help"],
                  approved=True)  # sandbox-only verdicts need approved=True
# r -> {status, exit_code, stdout, stderr, duration_ms, image,
#       dependencies, artifacts, artifact_receipt}
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
Runs have no network. A cached image build gets network only when a declared
`requirements.txt` must be fetched; unsafe directives, URLs, VCS/local
dependencies, and source-only distributions are rejected. The dependency
receipt says whether every requirement was exactly pinned. A Docker daemon is
required.

## JS

```js
import { search, resolve, resolveDetailed, load, digest, verify } from "./js/index.js";
const hits = search(items, "procurement");
const r = resolve(items, { tags: ["procurement"], officialOnly: true, auditedOnly: true });
const detail = resolveDetailed(items, { task: "approve procurement" });
const binding = load(hits[0]); // tool binding for the procurement seed
verify(hits[0], digest(hits[0])); // true
```

## skills.sh token (Vercel OIDC)

```python
import os
token = os.environ["SKILLS_SH_TOKEN"]  # caller-supplied Vercel OIDC bearer
# getVercelOidcToken(): fetch a Vercel OIDC token for your team/project,
# export it as SKILLS_SH_TOKEN, then call import_ids(ids, base, token).
```

Vercel OIDC tokens are short-lived. Obtain or rotate them only in a trusted,
Vercel-linked administrator session; an upstream `401 invalid_token` means the
token must be renewed. Do not ask an end user to paste the token into an agent
conversation.

Windows hygiene: `print()` emits `\r\n` and shell `$()` strips only `\n`,
leaving a trailing `\r` that servers reject as an invalid header value.
Always `.strip()` the token and strip `\r` before use or export.
