"""Enterprise policy gate: decide per-candidate verdict. Stdlib only."""


def decide(m: dict, policy: dict) -> tuple[str, str]:
    """Return (verdict, reason).

    Verdicts: allow | deny | require-review | sandbox-only.
    Rules evaluated in order.
    """
    policy = policy or {}
    sid = m.get("skill_id")
    pub = m.get("publisher", {}).get("id")
    denylist = policy.get("denylist") or []
    if denylist and (sid in denylist or pub in denylist):
        return ("deny", "denylisted")
    allowlist = policy.get("allowlist")
    if allowlist and sid not in allowlist and pub not in allowlist:
        return ("deny", "not-allowlisted")
    if m.get("trust", {}).get("revoked"):
        return ("deny", "revoked")
    if policy.get("require_audited") or policy.get("audited_only"):
        if not m.get("trust", {}).get("audited"):
            return ("require-review", "unaudited")
    if policy.get("official_only"):
        t = m.get("trust", {})
        if not (t.get("official") or m.get("publisher", {}).get("official")):
            return ("require-review", "unofficial")
    if "executable" in m.get("execution_modes", []):
        return ("sandbox-only", "executable")
    return ("allow", "ok")
