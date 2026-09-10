"""Search: keyword + topic/pack/publisher/compatibility/trust filters."""
import re


_STOP_WORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "how", "in", "into",
    "of", "on", "or", "the", "to", "use", "using", "with",
}


def _tokens(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", str(text).lower())
            if token not in _STOP_WORDS]


def _variants(token: str) -> set[str]:
    out = {token}
    if len(token) > 4:
        if token.endswith(("ating", "ation")):
            out.add(token[:-5] + "ate")
        if token.endswith("ing"):
            out.update((token[:-3], token[:-3] + "e"))
        if token.endswith("ed"):
            out.update((token[:-2], token[:-2] + "e"))
        if token.endswith("ies"):
            out.add(token[:-3] + "y")
        if token.endswith("al"):
            out.update((token[:-2], token[:-2] + "e"))
        if token.endswith("es"):
            out.update((token[:-1], token[:-2]))
        elif token.endswith("s"):
            out.add(token[:-1])
    return {item for item in out if item}


def _document_text(m: dict) -> str:
    artifact = m.get("artifact", {}) if isinstance(m.get("artifact", {}), dict) else {}
    files = artifact.get("files", []) if isinstance(artifact.get("files", []), list) else []
    file_paths = [f.get("path", "") for f in files if isinstance(f, dict)]
    publisher = m.get("publisher", {}) if isinstance(m.get("publisher", {}), dict) else {}
    return " ".join(str(value or "") for value in [
        m.get("skill_id"), m.get("name"), m.get("description"), m.get("pack"),
        publisher.get("id"), *(m.get("topics") or []), *(m.get("tags") or []),
        artifact.get("instruction"), *file_paths,
    ])


def relevance(m: dict, query: str = "", tags: list[str] | None = None) -> tuple[float, list[str]]:
    """Return lexical relevance and machine-readable reasons.

    All meaningful query terms must match. Lightweight morphology keeps the
    implementation dependency-free while covering common skill-search forms.
    Explicit tags are capability hints and must also match.
    """
    doc_original = set(_tokens(_document_text(m)))
    doc_variants = set().union(*(_variants(token) for token in doc_original)) if doc_original else set()
    query_tokens = _tokens(query)
    tag_tokens = [token for tag in (tags or []) for token in _tokens(tag)]
    score = 0.0
    reasons: list[str] = []
    for kind, tokens in (("term", query_tokens), ("tag", tag_tokens)):
        for token in tokens:
            variants = _variants(token)
            if not variants.intersection(doc_variants):
                return (0.0, [])
            exact = token in doc_original
            score += 2.0 if kind == "tag" else (1.5 if exact else 1.0)
            reasons.append(f"{kind}:{token}")
    return (score, reasons)


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
    q = query.strip()
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
            score, _ = relevance(m, q)
            if score <= 0:
                continue
        else:
            score = 0.0
        out.append((score, m))
    # Relevance first, then popularity and deterministic id.
    out.sort(key=lambda row: (-row[0], -row[1].get("popularity", 0), row[1]["skill_id"]))
    return [m for _, m in out]
