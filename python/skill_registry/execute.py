"""Exact execution of script-bearing skills in throwaway Docker containers.

Stdlib only. Policy first (same `decide` gate as resolve): anything that is
not an unconditional `allow` needs explicit `approved=True`, else the call
returns `needs-approval` without touching Docker. Each run materializes the
pinned file tree fresh, runs one entrypoint with no network, then destroys
the container — no state survives between runs or agents.
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .files import sha256_bytes
from .policy import decide

# entrypoint suffix -> (base image, argv prefix inside the container)
RUNNERS = {
    ".py": ("python:3.12-slim", ["python"]),
    ".sh": ("python:3.12-slim", ["sh"]),
}

DEFAULT_TIMEOUT_S = 120
OUTPUT_LIMIT = 500_000
ARTIFACT_TEXT_LIMIT = 100_000
MAX_ARTIFACTS = 50


def gate(manifest: dict, policy: dict | None, approved: bool = False) -> dict:
    """Policy verdict for an execution request. Pure; never touches Docker."""
    verdict, reason = decide(manifest, policy or {})
    if verdict == "deny":
        return {"status": "refused", "verdict": verdict, "reason": reason}
    if verdict != "allow" and not approved:
        return {"status": "needs-approval", "verdict": verdict, "reason": reason}
    return {"status": "ok", "verdict": verdict, "reason": reason}


def materialize(manifest: dict, read_bytes, dest: str | Path) -> list[str]:
    """Write the pinned file tree to `dest`, preserving relative paths.

    `read_bytes(path, sha256)` returns exact bytes or None. Raises
    ValueError on missing files, hash mismatch, or path traversal.
    """
    dest = Path(dest)
    wanted = {f["path"]: f for f in manifest.get("artifact", {}).get("files", [])}
    if not wanted:
        raise ValueError("skill has no files to materialize")
    written = []
    for path, meta in sorted(wanted.items()):
        parts = Path(path).parts
        if Path(path).is_absolute() or ".." in parts:
            raise ValueError(f"unsafe path: {path}")
        raw = read_bytes(path, meta.get("sha256"))
        if raw is None:
            raise ValueError(f"contents unavailable or hash mismatch: {path}")
        target = dest.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        written.append(path)
    return written


EXEC_RECIPE = "2"  # bump when the generated Dockerfile changes (invalidates cache)


def image_tag(manifest: dict) -> str:
    base = manifest.get("integrity", {}).get("sha256") or sha256_bytes(
        repr(sorted(manifest.get("artifact", {}).get("files", []),
                    key=lambda f: f.get("path", ""))).encode())
    return "skill-exec-" + sha256_bytes(f"{base}|{EXEC_RECIPE}".encode())[:16]


def runner_for(entrypoint: str) -> tuple[str, list[str]]:
    ext = "." + entrypoint.rsplit(".", 1)[-1].lower() if "." in entrypoint else ""
    try:
        return RUNNERS[ext]
    except KeyError:
        raise ValueError(f"unsupported entrypoint: {entrypoint} "
                         f"(supported: {sorted(RUNNERS)})") from None

def ensure_image(manifest: dict, tree: Path, base_image: str) -> str:
    """Build (once per skill hash) the dependency image. Returns the tag."""
    tag = image_tag(manifest)
    have = subprocess.run(["docker", "images", "-q", tag],
                          capture_output=True, text=True, timeout=60)
    if have.stdout.strip():
        return tag
    dockerfile = f"FROM {base_image}\nCOPY . /skill\nWORKDIR /skill\n"
    for req in sorted(tree.rglob("requirements.txt")):
        dockerfile += f"RUN pip install --no-cache-dir -r {req.relative_to(tree).as_posix()}\n"
    (tree / "Dockerfile.exec").write_text(dockerfile)
    build = subprocess.run(
        ["docker", "build", "-f", "Dockerfile.exec", "-t", tag, "."],
        cwd=str(tree), capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        raise RuntimeError(f"image build failed: {build.stderr[-2000:]}")
    return tag


def collect_artifacts(scratch: Path) -> list[dict]:
    out = []
    total = 0
    paths = sorted(p for p in scratch.rglob("*") if p.is_file())[:MAX_ARTIFACTS + 1]
    for p in paths[:MAX_ARTIFACTS]:
        raw = p.read_bytes()
        total += len(raw)
        if total > OUTPUT_LIMIT:
            break
        entry: dict = {"path": str(p.relative_to(scratch)),
                       "size": len(raw),
                       "sha256": sha256_bytes(raw)}
        try:
            text = raw.decode("utf-8")
            entry["contents"] = (text if len(raw) <= ARTIFACT_TEXT_LIMIT
                                 else text[:ARTIFACT_TEXT_LIMIT] + "\n…[truncated]")
        except UnicodeDecodeError:
            entry["contents"] = None
            entry["note"] = "binary omitted"
        out.append(entry)
    return out


def execute(manifest: dict, read_bytes, entrypoint: str,
            args: list[str] | None = None,
            policy: dict | None = None, approved: bool = False,
            timeout_s: int = DEFAULT_TIMEOUT_S,
            work_root: str | Path | None = None) -> dict:
    """Run `entrypoint` from the skill's pinned tree in a fresh container."""
    g = gate(manifest, policy, approved)
    if g["status"] != "ok":
        return {**g, "skill_id": manifest["skill_id"]}
    if shutil.which("docker") is None:
        raise RuntimeError("docker unavailable: cannot execute skills")
    files = {f["path"] for f in manifest.get("artifact", {}).get("files", [])}
    if entrypoint not in files:
        raise ValueError(f"unknown entrypoint: {entrypoint}")
    image, prefix = runner_for(entrypoint)

    owned_root = work_root is None
    root = Path(work_root) if work_root else Path(tempfile.mkdtemp(prefix="skill-exec-"))
    try:
        tree = root / "tree"
        scratch = root / "scratch"
        tree.mkdir(parents=True, exist_ok=True)
        scratch.mkdir(parents=True, exist_ok=True)
        materialize(manifest, read_bytes, tree)
        tag = ensure_image(manifest, tree, image)
        name = f"skill-exec-{int(time.time() * 1000)}-{os.getpid()}"
        # Copy-free (no bind mounts): the per-hash image already contains the
        # exact tree via COPY; scratch is a tmpfs. Works whether the server
        # runs on the host or in a container (Docker-out-of-Docker safe).
        create = ["docker", "create", "--name", name,
                  "--network", "none", "--read-only",
                  "--memory", "512m", "--cpus", "1",
                  "--tmpfs", "/scratch:rw,size=64m",
                  "-w", "/skill",
                  tag, *(prefix + [f"/skill/{entrypoint}", *(args or [])])]
        t0 = time.perf_counter()
        run = subprocess.run(create, capture_output=True, text=True,
                             errors="replace", timeout=60)
        if run.returncode != 0:
            raise RuntimeError(f"container create failed: {run.stderr[-2000:]}")
        try:
            start = subprocess.run(["docker", "start", name],
                                   capture_output=True, text=True,
                                   errors="replace", timeout=30)
            if start.returncode != 0:
                raise RuntimeError(f"container start failed: {start.stderr[-2000:]}")
            try:
                wait = subprocess.run(["docker", "wait", name],
                                      capture_output=True, text=True,
                                      errors="replace", timeout=timeout_s)
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "kill", name],
                               capture_output=True, timeout=30)
                raise RuntimeError(f"execution timed out after {timeout_s}s "
                                   f"(container removed)") from None
            exit_code = int((wait.stdout or "0").strip().split()[0])
            logs = subprocess.run(["docker", "logs", name],
                                  capture_output=True, text=True,
                                  errors="replace", timeout=30)
            subprocess.run(["docker", "cp", f"{name}:/scratch/.", str(scratch)],
                           capture_output=True, timeout=60)
        finally:
            subprocess.run(["docker", "rm", "-f", name],
                           capture_output=True, timeout=30)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        # `docker logs` merges streams; stdout carries the combined output.
        combined = (logs.stdout or "")[-OUTPUT_LIMIT:]
        return {"status": "ok" if exit_code == 0 else "failed",
                "skill_id": manifest["skill_id"],
                "entrypoint": entrypoint,
                "verdict": g["verdict"],
                "exit_code": exit_code,
                "stdout": combined, "stderr": "",
                "duration_ms": ms, "image": tag,
                "artifacts": collect_artifacts(scratch)}
    finally:
        if owned_root:
            shutil.rmtree(root, ignore_errors=True)
