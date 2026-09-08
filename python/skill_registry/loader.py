"""Runtime loading for instruction + tool skills (MVP scope)."""


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def _instruction(m: dict) -> str:
    raw = m.get("artifact", {}).get("instruction") or m.get("description", "")
    return _strip_frontmatter(raw)


def load(m: dict, session: dict | None = None) -> dict:
    modes = m.get("execution_modes", [])
    if "instruction" in modes:
        text = _instruction(m)
        out = {"kind": "instruction", "context": text}
        if session is not None:
            session.setdefault("context_blocks", []).append(text)
            out["session_bound"] = True
        return out
    if "tool" in modes:
        tool_name = m.get("artifact", {}).get("tool_ref") or m["skill_id"]
        out = {
            "kind": "tool",
            "tool": tool_name,
            "input_schema": m.get("input_schema", {}),
            "output_schema": m.get("output_schema", {}),
        }
        if session is not None:
            session.setdefault("tools", {})[tool_name] = {
                "input_schema": m.get("input_schema", {}),
                "output_schema": m.get("output_schema", {}),
            }
            out["session_bound"] = True
        return out
    if "workflow" in modes:
        return {"kind": "workflow", "deferred": True, "skill_id": m["skill_id"]}
    return {"kind": "executable", "deferred": True, "skill_id": m["skill_id"],
            "note": "executable out of MVP scope — sandbox required"}


def unload(m: dict, session: dict) -> None:
    modes = m.get("execution_modes", [])
    if "instruction" in modes:
        text = _instruction(m)
        blocks = session.get("context_blocks", [])
        try:
            blocks.remove(text)
        except ValueError:
            pass
        return
    if "tool" in modes:
        tool_name = m.get("artifact", {}).get("tool_ref") or m["skill_id"]
        session.get("tools", {}).pop(tool_name, None)
        return
