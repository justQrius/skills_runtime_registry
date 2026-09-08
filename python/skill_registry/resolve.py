"""Resolve: policy-constrained ranking with rationale. Deterministic, stdlib only."""
from .policy import decide

def _score(m: dict, tags: list[str], agent_class: str | None, modes: list[str]) -> tuple[float, list[str]]:
    s = 0.0
    why: list[str] = []
    mt = [x.lower() for x in m.get("topics", []) + m.get("tags", [])]
    for t in tags:
        if t.lower() in mt:
            s += 2.0
            why.append(f"tag:{t}")
    if agent_class:
        classes = m.get("compatibility", {}).get("agent_classes", [])
        if not classes or agent_class in classes:
            s += 1.0
            why.append("compatible")
    for mode in modes:
        if mode in m.get("execution_modes", []):
            s += 1.0
            why.append(f"mode:{mode}")
            break
    t = m.get("trust", {})
    if t.get("official") or m.get("publisher", {}).get("official"):
        s += 1.5
        why.append("official")
    if t.get("audited"):
        s += 1.0
        why.append("audited")
    s += min(m.get("popularity", 0) / 100.0, 2.0)
    if m.get("deprecation", {}).get("deprecated"):
        s -= 5.0
        why.append("deprecated")
    return s, why


def resolve(
    items: list[dict],
    *,
    task: str = "",
    tags: list[str] | None = None,
    agent_class: str | None = None,
    allowed_modes: list[str] | None = None,
    allowlist: list[str] | None = None,
    denylist: list[str] | None = None,
    official_only: bool = False,
    audited_only: bool = False,
    policy: dict | None = None,
    require_review: bool = False,
    limit: int = 5,
) -> dict:
    tags = tags or []
    modes = allowed_modes or []
    # Hard policy filter first.
    cands = []
    for m in items:
        sid = m["skill_id"]
        if denylist and (sid in denylist or m.get("publisher", {}).get("id") in denylist):
            continue
        if allowlist and sid not in allowlist and m.get("publisher", {}).get("id") not in allowlist:
            continue
        t = m.get("trust", {})
        if t.get("revoked"):
            continue
        if official_only and not (t.get("official") or m.get("publisher", {}).get("official")):
            continue
        if audited_only and not t.get("audited"):
            continue
        if modes and not any(x in m.get("execution_modes", []) for x in modes):
            continue
        if agent_class:
            classes = m.get("compatibility", {}).get("agent_classes", [])
            if classes and agent_class not in classes:
                continue
        cands.append(m)
    hint_tags = [w.strip(".,").lower() for w in task.split() if len(w) > 3][:8]
    seen: set[str] = set()
    all_tags: list[str] = []
    for t in (tags + hint_tags):
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            all_tags.append(t)
    eff: dict = dict(policy) if policy else {}
    if allowlist is not None:
        eff.setdefault("allowlist", allowlist)
    if denylist is not None:
        eff.setdefault("denylist", denylist)
    if official_only:
        eff.setdefault("official_only", True)
    if audited_only:
        eff.setdefault("require_audited", True)
    ranked = []
    for m in cands:
        verdict, _reason = decide(m, eff)
        if verdict == "deny":
            continue
        if verdict in ("require-review", "sandbox-only") and not require_review:
            continue
        s, why = _score(m, all_tags, agent_class, modes)
        if verdict != "allow":
            why = [*why, f"policy:{verdict}"]
        ranked.append((s, m["skill_id"], m, why))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    top = ranked[:limit]
    return {
        "candidates": [
            {
                "skill_id": m["skill_id"],
                "version": m["version"],
                "score": round(s, 2),
                "rationale": why,
                "permissions": m.get("permissions", {}),
                "trust": m.get("trust", {}),
                "integrity": m.get("integrity", {}),
            }
            for s, _, m, why in top
        ],
        "fallback": "fall back to native reasoning" if not top else None,
    }
