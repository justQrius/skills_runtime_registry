"""TTL cache with version pinning, sha256 verify, revocation invalidation."""
import hashlib
import json
import time


def digest(m: dict) -> str:
    body = json.dumps({k: v for k, v in m.items() if k != "integrity"}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class Cache:
    def __init__(self):
        self._c: dict[str, tuple[float, dict, str | None, bool]] = {}  # key -> (exp, manifest, version, audited)

    def put(self, m: dict, ttl: int | None = None) -> None:
        ttl = m.get("cache", {}).get("ttl_seconds", 3600) if ttl is None else ttl
        self._c[m["skill_id"]] = (time.time() + ttl, m, m["version"], bool(m.get("trust", {}).get("audited")))

    def get(self, skill_id: str, pin: str | None = None) -> dict | None:
        row = self._c.get(skill_id)
        if not row:
            return None
        if len(row) == 3:
            exp, m, _ver = row
            audited = bool(m.get("trust", {}).get("audited"))
        else:
            exp, m, _ver, audited = row
        if time.time() > exp:
            del self._c[skill_id]
            return None
        if m.get("trust", {}).get("revoked"):
            del self._c[skill_id]
            return None
        if audited and not m.get("trust", {}).get("audited"):
            # audit-withdrawn: stored audited True, live object flipped to False
            del self._c[skill_id]
            return None
        if pin and m["version"] != pin:
            return None
        return m

    def verify(self, m: dict) -> bool:
        want = m.get("integrity", {}).get("sha256")
        if not want:
            return True  # nothing pinned to check against
        return digest(m) == want

    def get_pinned(self, skill_id: str, sha256: str) -> dict | None:
        m = self.get(skill_id)
        if m is None:
            return None
        return m if digest(m) == sha256 else None
