"""Basic telemetry: in-memory events + optional JSONL sink."""
import json
import time
from pathlib import Path

EVENTS = (
    "search.requested",
    "candidates.returned",
    "skill.selected",
    "skill.rejected",
    "fetch.ok",
    "fetch.fail",
    "integrity.ok",
    "integrity.fail",
    "execution.ok",
    "execution.fail",
    "cache.hit",
    "cache.miss",
    "override",
)


class Telemetry:
    def __init__(self, sink: str | Path | None = None):
        self.events: list[dict] = []
        self.sink = Path(sink) if sink else None

    def emit(self, name: str, **fields) -> dict:
        if name == "skill_selected":
            name = "skill.selected"
        if name not in EVENTS:
            raise ValueError(f"unknown event: {name}")
        ev = {"event": name, "ts": time.time(), **fields}
        self.events.append(ev)
        if self.sink:
            with self.sink.open("a") as f:
                f.write(json.dumps(ev) + "\n")
        return ev
