"""Type-aware conformance checks for fetched skill packages."""
import re
from pathlib import PurePosixPath


STRICT_SECTIONS = ("Contract", "Anti-Patterns", "Output Format")


def _frontmatter(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else None


def _references(text: str) -> set[str]:
    candidates = set(re.findall(r"`([^`\n]+\.md)`", text, re.IGNORECASE))
    candidates.update(re.findall(r"\]\(([^)]+\.md)(?:#[^)]*)?\)", text, re.IGNORECASE))
    return {str(PurePosixPath(path)) for path in candidates
            if "://" not in path and not path.startswith(("/", "#"))}


def validate_package(manifest: dict, read_bytes, profile: str = "ecosystem") -> dict:
    """Validate package structure without executing it."""
    if profile not in ("ecosystem", "strict"):
        raise ValueError("profile must be ecosystem or strict")
    artifact = manifest.get("artifact", {})
    declared = {f["path"] for f in artifact.get("files", [])}
    issues: list[str] = []
    checked: list[str] = []
    if declared:
        if "SKILL.md" not in declared:
            issues.append("missing file: SKILL.md")
        else:
            raw = read_bytes("SKILL.md")
            if raw is None:
                issues.append("unavailable file: SKILL.md")
            else:
                checked.append("SKILL.md")
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                    issues.append("SKILL.md must be UTF-8")
                frontmatter = _frontmatter(text)
                if frontmatter is None:
                    issues.append("SKILL.md missing YAML frontmatter")
                else:
                    for field in ("name", "description"):
                        if not re.search(rf"(?im)^{field}\s*:\s*\S+", frontmatter):
                            issues.append(f"SKILL.md frontmatter missing {field}")
                for reference in sorted(_references(text)):
                    if reference not in declared:
                        issues.append(f"missing reference: {reference}")
                    elif read_bytes(reference) is None:
                        issues.append(f"unavailable reference: {reference}")
                    else:
                        checked.append(reference)
                if profile == "strict":
                    for section in STRICT_SECTIONS:
                        if not re.search(rf"(?im)^##?\s+{re.escape(section)}\s*$", text):
                            issues.append(f"missing section: {section}")
    for entrypoint in artifact.get("entrypoints", []):
        if entrypoint not in declared:
            issues.append(f"missing entrypoint: {entrypoint}")
        elif read_bytes(entrypoint) is None:
            issues.append(f"unavailable entrypoint: {entrypoint}")
        else:
            checked.append(entrypoint)
    return {"skill_id": manifest["skill_id"], "version": manifest["version"],
            "profile": profile, "valid": not issues, "issues": issues,
            "checked_files": sorted(set(checked))}
