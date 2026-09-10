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


def _with_capabilities(m: dict, out: dict) -> dict:
    modes = list(m.get("execution_modes", []))
    out["execution_modes"] = modes
    out["entrypoints"] = list(m.get("artifact", {}).get("entrypoints", []))
    out["requires_approval"] = "executable" in modes
    return out


def load(m: dict, session: dict | None = None) -> dict:
    modes = m.get("execution_modes", [])
    if "instruction" in modes:
        text = _instruction(m)
        out = {"kind": "instruction", "context": text}
        if session is not None:
            session.setdefault("context_blocks", []).append(text)
            out["session_bound"] = True
        return _with_capabilities(m, out)
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
        return _with_capabilities(m, out)
    if "workflow" in modes:
        return _with_capabilities(m, {"kind": "workflow", "deferred": False,
                                      "skill_id": m["skill_id"],
                                      "workflow": m.get("artifact", {}).get("workflow")})
    return _with_capabilities(m, {
        "kind": "executable", "deferred": True, "skill_id": m["skill_id"],
        "note": "use the execute tool with a declared entrypoint",
    })


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
