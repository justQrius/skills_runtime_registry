"""Live skills.sh discovery: public search API -> discovery cards.

Stdlib only. `discover` does the HTTP GET; no token required — the
`/api/search` endpoint is public (same one the `skills` CLI uses with
`SEARCH_API_BASE = SKILLS_API_URL || "https://skills.sh"`).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "https://skills.sh"
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def discover(
    base: str,
    query: str,
    *,
    limit: int = DEFAULT_LIMIT,
    owner: str | None = None,
    timeout: int = 30,
    opener=urllib.request.urlopen,
) -> dict:
    """Query skills.sh live search and return discovery cards.

    Cards carry the skills.sh id (`skill_id`), install count, and source —
    enough for a caller to choose ids and pass them to `refresh`.
    """
    base = (base or DEFAULT_BASE).rstrip("/")
    if not query or not str(query).strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(limit, int) or isinstance(limit, bool) \
            or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {MAX_LIMIT}")

    params: dict[str, str] = {"q": str(query), "limit": str(min(limit, 50))}
    if owner:
        params["owner"] = owner
    url = f"{base}/api/search?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "skill-registry-discover/1.0"})
    try:
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:200]
        if e.code == 429:
            raise RuntimeError(
                f"skills.sh discovery rate-limited (429); retry later") from e
        raise RuntimeError(f"skills.sh discovery {e.code} {url}: {body}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"skills.sh discovery unreachable: {e}") from e

    skills = payload.get("skills")
    if not isinstance(skills, list):
        raise RuntimeError("skills.sh discovery returned an unexpected payload")

    cards = []
    for s in skills:
        if not isinstance(s, dict):
            continue
        sid = s.get("id") or s.get("slug") or ""
        if not sid:
            continue
        try:
            installs = int(s.get("installs") or 0)
        except (TypeError, ValueError):
            installs = 0
        cards.append({
            "skill_id": sid,
            "name": str(s.get("name") or sid),
            "source": str(s.get("source") or ""),
            "installs": installs,
        })
    cards.sort(key=lambda c: c["installs"], reverse=True)
    return {"query": str(query), "base": base, "count": len(cards),
            "skills": cards}