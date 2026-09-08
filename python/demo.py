"""MVP verification demo: exercises seeds + conformance fixtures end to end."""
import json
import time
from pathlib import Path

from skill_registry import Registry, search, resolve, Cache, Telemetry, load, unload
from skill_registry.cache import digest

ROOT = Path(__file__).resolve().parent.parent
SINK = ROOT / "telemetry-demo.jsonl"


def main() -> None:
    if SINK.exists():
        SINK.unlink()
    tel = Telemetry(sink=SINK)
    reg = Registry()
    reg.load_dir(ROOT / "examples" / "seeds")
    for f in ("valid-instruction.json", "valid-tool.json"):
        reg.add(json.loads((ROOT / "examples" / "conformance" / f).read_text()))
    revoked = json.loads((ROOT / "examples" / "conformance" / "revoked.json").read_text())
    all_items = reg.all()

    # (a) keyword search
    tel.emit("search.requested", query="react")
    got = search(all_items, "react")
    tel.emit("candidates.returned", count=len(got))
    assert [m["skill_id"] for m in got] == ["acme/react-review"], got

    # (b) enterprise resolve
    res = resolve(
        all_items,
        official_only=True,
        audited_only=True,
        agent_class="workflow-agent",
    )
    assert res["candidates"], "no enterprise candidates"
    top = res["candidates"][0]
    assert top["skill_id"] == "official/procurement-flow", top
    assert "official" in top["rationale"] and "audited" in top["rationale"], top
    tel.emit("skill.selected", skill_id=top["skill_id"], score=top["score"])

    # (c) revoked never surfaces
    assert search([revoked], "react") == []
    assert search([*all_items, revoked], "react") == got
    mixed = resolve([*all_items, revoked], task="react review")
    r_only = resolve([revoked], task="react review")
    assert r_only["candidates"] == [] and r_only["fallback"] is not None
    assert any(c["skill_id"] == "acme/react-review" for c in mixed["candidates"]), mixed
    tel.emit("skill.rejected", skill_id=revoked["skill_id"], reason="revoked")

    # (d) digest -> verify round-trip + pinned fetch
    cache = Cache()
    m = reg.get("acme/react-review")
    cache.put(m)
    t0 = time.perf_counter()
    fetched = cache.get("acme/react-review")
    fetch_ms = (time.perf_counter() - t0) * 1000
    assert fetched is not None
    tel.emit("fetch.ok", skill_id=m["skill_id"], latency_ms=round(fetch_ms, 3))
    t1 = time.perf_counter()
    ok = cache.verify(fetched)
    integ_ms = (time.perf_counter() - t1) * 1000
    assert ok is True
    tel.emit("integrity.ok", skill_id=m["skill_id"], latency_ms=round(integ_ms, 3))
    assert digest(m) == m["integrity"]["sha256"]
    tel.emit("cache.hit", skill_id=m["skill_id"])
    assert cache.get_pinned(m["skill_id"], "0" * 64) is None
    tel.emit("cache.miss", skill_id=m["skill_id"], reason="pin-mismatch")

    # (e) load/unload round-trips
    sess: dict = {}
    out = load(m, sess)
    assert out.get("session_bound") is True and sess["context_blocks"] == [out["context"]]
    unload(m, sess)
    assert sess["context_blocks"] == [], sess
    m2 = reg.get("official/procurement-flow")
    sess2: dict = {}
    out2 = load(m2, sess2)
    assert out2.get("session_bound") is True and out2["tool"] in sess2["tools"]
    unload(m2, sess2)
    assert sess2["tools"] == {}, sess2

    # (f) 7-event chain with latency_ms on fetch/integrity
    rows = [json.loads(line) for line in SINK.read_text().splitlines()]
    names = [r["event"] for r in rows]
    chain = ["search.requested", "candidates.returned", "skill.selected",
             "fetch.ok", "integrity.ok", "cache.hit", "cache.miss"]
    it = iter(names)
    assert all(any(n == c for n in it) for c in chain), names
    by_name = {r["event"]: r for r in rows}
    assert "latency_ms" in by_name["fetch.ok"] and "latency_ms" in by_name["integrity.ok"]
    print("demo ok")


if __name__ == "__main__":
    main()
