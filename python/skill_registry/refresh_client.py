"""Trusted HTTP client for request-scoped skills.sh refresh credentials."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    loopback = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("refresh URL must use HTTPS or a loopback HTTP host")
    if not parsed.hostname:
        raise ValueError("refresh URL must include a host")
    return url


def refresh_registry(
    url: str,
    ids: list[str],
    *,
    token: str,
    admin_key: str,
    official_ids: list[str] | None = None,
    timeout: int = 120,
    opener=urllib.request.urlopen,
) -> dict:
    """Refresh IDs using ephemeral credentials carried only in HTTP headers."""
    _safe_url(url)
    token = token.strip()
    admin_key = admin_key.strip()
    if not token:
        raise ValueError("a non-empty Vercel OIDC token is required")
    if not admin_key:
        raise ValueError("a non-empty registry admin key is required")
    if not ids:
        raise ValueError("at least one skill id is required")

    body = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "refresh",
            "arguments": {
                "ids": ids,
                "official_ids": official_ids or [],
                "audited_only": True,
            },
        },
    }).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Admin-Key": admin_key,
        },
        method="POST",
    )
    with opener(request, timeout=timeout) as response:
        payload = json.loads(response.read())
    if "error" in payload:
        error = payload["error"]
        raise RuntimeError(f"registry refresh failed: {error.get('message', error)}")
    try:
        return json.loads(payload["result"]["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("registry returned an invalid refresh response") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Refresh a registry with a request-scoped Vercel OIDC token.")
    parser.add_argument("ids", nargs="+", help="skills.sh IDs to refresh")
    parser.add_argument("--url", default="http://127.0.0.1:8125/mcp")
    parser.add_argument("--admin-key-file",
                        default=str(Path.home() / ".skill-registry" / "admin_key"))
    parser.add_argument("--official-id", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    token = os.environ.get("VERCEL_OIDC_TOKEN") or os.environ.get("SKILLS_SH_TOKEN")
    if not token:
        parser.error("VERCEL_OIDC_TOKEN or SKILLS_SH_TOKEN is required")
    admin_key = Path(args.admin_key_file).read_text(encoding="utf-8").strip()
    result = refresh_registry(
        args.url,
        args.ids,
        token=token,
        admin_key=admin_key,
        official_ids=args.official_id,
        timeout=args.timeout,
    )
    summary = {
        "status": result.get("status"),
        "imported": [item.get("skill_id") for item in result.get("imported", [])],
    }
    json.dump(summary, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
