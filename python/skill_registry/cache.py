"""TTL cache with version pinning, sha256 verify, revocation invalidation."""
import hashlib
import json
import time

from .store import _semver_key


def digest(m: dict) -> str:
    body = json.dumps({k: v for k, v in m.items() if k != "integrity"}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()


class Cache:
    def __init__(self):
        self._c: dict[tuple[str, str], tuple[float, dict, str | None, bool]] = {}

    def put(self, m: dict, ttl: int | None = None) -> None:
        ttl = m.get("cache", {}).get("ttl_seconds", 3600) if ttl is None else ttl
        self._c[(m["skill_id"], m["version"])] = (
            time.time() + ttl, m, m["version"],
            bool(m.get("trust", {}).get("audited")),
        )

    def get(self, skill_id: str, pin: str | None = None) -> dict | None:
        if pin is not None:
            return self.get_version(skill_id, pin)
        versions = [version for sid, version in self._c if sid == skill_id]
        for version in sorted(versions, key=_semver_key, reverse=True):
            if (manifest := self.get_version(skill_id, version)) is not None:
                return manifest
        return None

    def get_version(self, skill_id: str, version: str) -> dict | None:
        key = (skill_id, version)
        row = self._c.get(key)
        if not row:
            return None
        if len(row) == 3:
            exp, m, _ver = row
            audited = bool(m.get("trust", {}).get("audited"))
        else:
            exp, m, _ver, audited = row
        if time.time() > exp:
            del self._c[key]
            return None
        if m.get("trust", {}).get("revoked"):
            del self._c[key]
            return None
        if audited and not m.get("trust", {}).get("audited"):
            # audit-withdrawn: stored audited True, live object flipped to False
            del self._c[key]
            return None
        return m

    def verify(self, m: dict) -> bool:
        want = m.get("integrity", {}).get("sha256")
        if not want:
            return True  # nothing pinned to check against
        return digest(m) == want

    def get_pinned(self, skill_id: str, sha256: str) -> dict | None:
        versions = [version for sid, version in self._c if sid == skill_id]
        for version in sorted(versions, key=_semver_key, reverse=True):
            m = self.get_version(skill_id, version)
            if m is not None and digest(m) == sha256:
                return m
        return None
