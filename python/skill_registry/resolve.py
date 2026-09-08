"""Resolve: policy-constrained ranking with rationale. Deterministic, stdlib only."""
from .policy import decide
from .search import relevance

def _score(m: dict, task: str, tags: list[str], agent_class: str | None,
           modes: list[str]) -> tuple[float, list[str]]:
    s, why = relevance(m, task, tags)
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
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer from 1 to 100")
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
    gated = []
    for m in cands:
        verdict, _reason = decide(m, eff)
        if verdict == "deny":
            continue
        relevance_score, relevance_why = relevance(m, task, tags)
        has_hint = bool(task.strip() or tags)
        if has_hint and relevance_score <= 0:
            continue
        if verdict in ("require-review", "sandbox-only") and not require_review:
            gated.append({
                "skill_id": m["skill_id"], "version": m["version"],
                "verdict": verdict, "reason": _reason,
                "rationale": [*relevance_why, f"policy:{verdict}"],
            })
            continue
        s, why = _score(m, task, tags, agent_class, modes)
        if verdict != "allow":
            why = [*why, f"policy:{verdict}"]
        ranked.append((s, m["skill_id"], m, why))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    top = ranked[:limit]
    result = {
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
        "fallback": (None if top else
                     "matching skills require review" if gated else
                     "fall back to native reasoning"),
    }
    if gated:
        result["review_candidates"] = gated[:limit]
    return result
