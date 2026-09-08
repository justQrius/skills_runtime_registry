"""Search: keyword + topic/pack/publisher/compatibility/trust filters."""


def search(
    items: list[dict],
    query: str = "",
    *,
    topic: str | None = None,
    pack: str | None = None,
    publisher: str | None = None,
    agent_class: str | None = None,
    execution_mode: str | None = None,
    official_only: bool = False,
    audited_only: bool = False,
    include_revoked: bool = False,
    include_deprecated: bool = False,
) -> list[dict]:
    q = query.lower().strip()
    out = []
    for m in items:
        t = m.get("trust", {})
        d = m.get("deprecation", {})
        if t.get("revoked") and not include_revoked:
            continue
        if d.get("deprecated") and not include_deprecated:
            continue
        if official_only and not (t.get("official") or m.get("publisher", {}).get("official")):
            continue
        if audited_only and not t.get("audited"):
            continue
        if topic and topic.lower() not in [x.lower() for x in m.get("topics", []) + m.get("tags", [])]:
            continue
        if pack and m.get("pack") != pack:
            continue
        if publisher and m.get("publisher", {}).get("id") != publisher:
            continue
        if agent_class:
            classes = m.get("compatibility", {}).get("agent_classes", [])
            if classes and agent_class not in classes:
                continue
        if execution_mode and execution_mode not in m.get("execution_modes", []):
            continue
        if q:
            hay = " ".join([
                m.get("skill_id", ""), m.get("name", ""), m.get("description", ""),
                *m.get("topics", []), *m.get("tags", []),
            ]).lower()
            if q not in hay:
                continue
        out.append(m)
    # Freshness/popularity: popularity desc, then skill_id for determinism.
    out.sort(key=lambda m: (-m.get("popularity", 0), m["skill_id"]))
    return out
