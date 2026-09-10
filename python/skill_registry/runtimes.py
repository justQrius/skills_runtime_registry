"""Execution runtime declarations shared by ingestion and the sandbox runner."""
from pathlib import PurePosixPath


RUNNERS = {
    ".py": ("python:3.12-slim", ["python"]),
    ".sh": ("python:3.12-slim", ["sh"]),
    ".js": ("node:22-slim", ["node"]),
}


def entrypoint_suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def is_supported_entrypoint(path: str) -> bool:
    return entrypoint_suffix(path) in RUNNERS
