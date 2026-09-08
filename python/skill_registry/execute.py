"""Exact execution of script-bearing skills in throwaway Docker containers.

Stdlib only. Policy first (same `decide` gate as resolve): anything that is
not an unconditional `allow` needs explicit `approved=True`, else the call
returns `needs-approval` without touching Docker. Each run materializes the
pinned file tree fresh, runs one entrypoint with no network, then destroys
the container — no state survives between runs or agents.
"""
import base64
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
INPUT_FILE_LIMIT = 1_000_000
INPUT_BUNDLE_LIMIT = 10_000_000
MAX_INPUTS = 50


def gate(manifest: dict, policy: dict | None, approved: bool = False) -> dict:
    """Policy verdict for an execution request. Pure; never touches Docker."""
    verdict, reason = decide(manifest, policy or {})
    if verdict == "deny":
        return {"status": "refused", "verdict": verdict, "reason": reason}
    if verdict != "allow" and not approved:
        return {"status": "needs-approval", "verdict": verdict, "reason": reason}
    return {"status": "ok", "verdict": verdict, "reason": reason}


def _unsafe_path(path: str) -> bool:
    """True for absolute (POSIX, Windows, drive-letter) or `..`-escaping paths."""
    parts = Path(path).parts
    absolute = (Path(path).is_absolute() or path.startswith(("/", "\\"))
                or (len(path) > 1 and path[1] == ":"))
    return (not path) or absolute or ".." in parts


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
        if _unsafe_path(path):
            raise ValueError(f"unsafe path: {path}")
        raw = read_bytes(path, meta.get("sha256"))
        if raw is None:
            raise ValueError(f"contents unavailable or hash mismatch: {path}")
        target = dest.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        written.append(path)
    return written

def parse_inputs(inputs: list[dict] | None) -> list[tuple[str, bytes]]:
    """Validate agent-supplied input files into (relpath, bytes) pairs.

    Each entry is {"path": rel, "text": str} or {"path": rel, "b64": str}.
    Same traversal rules as `materialize`; 1MB/file, 10MB total, 50 files.
    Raises ValueError on any violation.
    """
    if not inputs:
        return []
    if len(inputs) > MAX_INPUTS:
        raise ValueError(f"too many inputs: {len(inputs)} (max {MAX_INPUTS})")
    out: list[tuple[str, bytes]] = []
    total = 0
    for entry in inputs:
        if not isinstance(entry, dict):
            raise ValueError("input must be {path, text|b64}")
        path = entry.get("path", "")
        if _unsafe_path(path):
            raise ValueError(f"unsafe input path: {path!r}")
        has_text = "text" in entry
        has_b64 = "b64" in entry
        if has_text == has_b64:
            raise ValueError(f"input {path!r} needs exactly one of text|b64")
        if has_b64:
            try:
                raw = base64.b64decode(entry["b64"], validate=True)
            except Exception:
                raise ValueError(f"input {path!r}: invalid base64") from None
        else:
            text = entry["text"]
            if not isinstance(text, str):
                raise ValueError(f"input {path!r}: text must be a string")
            raw = text.encode("utf-8")
        if len(raw) > INPUT_FILE_LIMIT:
            raise ValueError(f"input too large: {path} ({len(raw)} bytes)")
        total += len(raw)
        if total > INPUT_BUNDLE_LIMIT:
            raise ValueError("inputs too large (max 10MB total)")
        out.append((path, raw))
    return out


def stage_inputs(parsed: list[tuple[str, bytes]], dest: str | Path) -> list[str]:
    """Write validated inputs under `dest`, preserving relative paths."""
    dest = Path(dest)
    written = []
    for path, raw in parsed:
        target = dest.joinpath(*Path(path).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        written.append(path)
    return written


EXEC_RECIPE = "3"  # bump when the generated Dockerfile changes (invalidates cache)


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
    # /scratch + /inputs are plain dirs on the container layer (NOT tmpfs:
    # `docker cp` cannot see tmpfs mounts, so artifacts would come back
    # empty). No `--read-only` flag for the same reason; the container is
    # still throwaway (`--network none`, capped, removed after).
    dockerfile = (f"FROM {base_image}\nCOPY . /skill\nWORKDIR /skill\n"
                  f"RUN mkdir -p /scratch /inputs\n")
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
        if len(raw) <= ARTIFACT_TEXT_LIMIT:
            entry["contents_b64"] = base64.b64encode(raw).decode()
        else:
            entry["note"] = "artifact too large: b64 omitted"
        out.append(entry)
    return out


def execute(manifest: dict, read_bytes, entrypoint: str,
            args: list[str] | None = None,
            policy: dict | None = None, approved: bool = False,
            timeout_s: int = DEFAULT_TIMEOUT_S,
            inputs: list[dict] | None = None,
            work_root: str | Path | None = None) -> dict:
    """Run `entrypoint` from the skill's pinned tree in a fresh container.

    `inputs` (optional): agent files staged to `/inputs`
    (`{path, text}` or `{path, b64}`; 1MB/file, 10MB total). Reference them
    from `args` as `/inputs/<path>`; write results to `/scratch/<path>` —
    everything under `/scratch` returns as `artifacts` (text in `contents`,
    exact bytes in `contents_b64` when <=100KB).
    """
    g = gate(manifest, policy, approved)
    if g["status"] != "ok":
        return {**g, "skill_id": manifest["skill_id"]}
    if shutil.which("docker") is None:
        raise RuntimeError("docker unavailable: cannot execute skills")
    files = {f["path"] for f in manifest.get("artifact", {}).get("files", [])}
    if entrypoint not in files:
        raise ValueError(f"unknown entrypoint: {entrypoint}")
    image, prefix = runner_for(entrypoint)
    parsed = parse_inputs(inputs)

    owned_root = work_root is None
    root = Path(work_root) if work_root else Path(tempfile.mkdtemp(prefix="skill-exec-"))
    try:
        tree = root / "tree"
        scratch = root / "scratch"
        staged = root / "staged-inputs"
        tree.mkdir(parents=True, exist_ok=True)
        scratch.mkdir(parents=True, exist_ok=True)
        staged.mkdir(parents=True, exist_ok=True)
        materialize(manifest, read_bytes, tree)
        stage_inputs(parsed, staged)
        base_tag = ensure_image(manifest, tree, image)
        # Agent inputs ride an ephemeral per-run layer over the cached
        # skill image (COPY, never a bind mount). Still
        # Docker-out-of-Docker safe.
        tag = base_tag
        run_tag: str | None = None
        if parsed:
            run_tag = f"skill-run-{int(time.time() * 1000)}-{os.getpid()}"
            (staged / "Dockerfile.run").write_text(f"FROM {base_tag}\nCOPY . /inputs\n")
            build = subprocess.run(
                ["docker", "build", "-f", "Dockerfile.run", "-t", run_tag, "."],
                cwd=str(staged), capture_output=True, text=True, timeout=300)
            if build.returncode != 0:
                raise RuntimeError(f"input layer build failed: {build.stderr[-2000:]}")
            tag = run_tag
        name = f"skill-exec-{int(time.time() * 1000)}-{os.getpid()}"
        # Copy-free (no bind mounts): the per-hash image already contains the
        # exact tree via COPY; /scratch + /inputs are plain container-layer
        # dirs so `docker cp` can retrieve outputs (it cannot see tmpfs).
        # Works whether the server runs on the host or in a container
        # (Docker-out-of-Docker safe).
        create = ["docker", "create", "--name", name,
                  "--network", "none",
                  "--memory", "512m", "--cpus", "1",
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
            if run_tag is not None:
                subprocess.run(["docker", "rmi", "-f", run_tag],
                               capture_output=True, timeout=120)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        # `docker logs` splits streams across stdout/stderr; surface both so
        # tracebacks (stderr) are never silently dropped.
        combined = ((logs.stdout or "") + (logs.stderr or ""))[-OUTPUT_LIMIT:]
        return {"status": "ok" if exit_code == 0 else "failed",
                "skill_id": manifest["skill_id"],
                "entrypoint": entrypoint,
                "verdict": g["verdict"],
                "exit_code": exit_code,
                "stdout": combined, "stderr": "",
                "duration_ms": ms, "image": base_tag,
                "artifacts": collect_artifacts(scratch)}
    finally:
        if owned_root:
            shutil.rmtree(root, ignore_errors=True)
