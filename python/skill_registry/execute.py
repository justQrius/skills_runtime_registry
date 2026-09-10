"""Exact execution of script-bearing skills in throwaway Docker containers.

Stdlib only. Policy first (same `decide` gate as resolve): anything that is
not an unconditional `allow` needs explicit `approved=True`, else the call
returns `needs-approval` without touching Docker. Each run materializes the
pinned file tree fresh, runs one entrypoint with no network, then destroys
the container — no state survives between runs or agents.
"""
import base64
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .files import sha256_bytes
from .policy import decide
from .runtimes import RUNNERS, entrypoint_suffix

DEFAULT_TIMEOUT_S = 120
OUTPUT_LIMIT = 25_000_000
ARTIFACT_TEXT_LIMIT = 10_000_000
MAX_ARTIFACTS = 200
INPUT_FILE_LIMIT = 10_000_000
INPUT_BUNDLE_LIMIT = 50_000_000
MAX_INPUTS = 100


def _run_text(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command with locale-independent, loss-tolerant text decoding."""
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kwargs)


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
    return (not path) or "\\" in path or absolute or ".." in parts


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
    Same traversal rules as `materialize`; 10MB/file, 50MB total, 100 files.
    Raises ValueError on any violation.
    """
    if not inputs:
        return []
    if len(inputs) > MAX_INPUTS:
        raise ValueError(f"too many inputs: {len(inputs)} (max {MAX_INPUTS})")
    out: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    total = 0
    for entry in inputs:
        if not isinstance(entry, dict):
            raise ValueError("input must be {path, text|b64}")
        path = entry.get("path", "")
        if _unsafe_path(path):
            raise ValueError(f"unsafe input path: {path!r}")
        if path in seen:
            raise ValueError(f"duplicate input path: {path!r}")
        seen.add(path)
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
            raise ValueError("inputs too large (max 50MB total)")
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


EXEC_RECIPE = "4"  # bump when the generated Dockerfile changes (invalidates cache)


def image_tag(manifest: dict) -> str:
    base = manifest.get("integrity", {}).get("sha256") or sha256_bytes(
        repr(sorted(manifest.get("artifact", {}).get("files", []),
                    key=lambda f: f.get("path", ""))).encode())
    return "skill-exec-" + sha256_bytes(f"{base}|{EXEC_RECIPE}".encode())[:16]


def dependency_plan(tree: Path) -> dict:
    """Validate dependency declarations and report build reproducibility.

    Requirement files may name packages and version constraints, but cannot
    redirect indexes, include other files, or fetch URLs/VCS/local paths.
    """
    files = sorted(tree.rglob("requirements.txt"))
    requirements: list[str] = []
    unsafe_scheme = re.compile(r"(?:https?|git|file)://", re.IGNORECASE)
    for req in files:
        for line_no, raw_line in enumerate(req.read_text(encoding="utf-8").splitlines(), 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if (line.startswith("-") or unsafe_scheme.search(line) or " @ " in line or
                    line.startswith(("./", "../", "/", "\\")) or
                    (len(line) > 1 and line[1] == ":")):
                relative = req.relative_to(tree).as_posix()
                raise ValueError(f"unsafe requirements directive: {relative}:{line_no}")
            requirements.append(line)
    pinned = bool(requirements) and all(
        re.search(r"(?<![<>=!~])==(?!=)", line) is not None
        for line in requirements
    )
    return {
        "files": [path.relative_to(tree).as_posix() for path in files],
        "requirement_count": len(requirements),
        "network_required": bool(requirements),
        "reproducible": not requirements or pinned,
    }


def runner_for(entrypoint: str) -> tuple[str, list[str]]:
    ext = entrypoint_suffix(entrypoint)
    try:
        return RUNNERS[ext]
    except KeyError:
        raise ValueError(f"unsupported entrypoint: {entrypoint} "
                         f"(supported: {sorted(RUNNERS)})") from None

def ensure_image(manifest: dict, tree: Path, base_image: str) -> str:
    """Build (once per skill hash) the dependency image. Returns the tag."""
    tag = image_tag(manifest)
    have = _run_text(["docker", "images", "-q", tag], timeout=60)
    if have.stdout.strip():
        return tag
    # /scratch + /inputs are plain dirs on the container layer (NOT tmpfs:
    # `docker cp` cannot see tmpfs mounts, so artifacts would come back
    # empty). No `--read-only` flag for the same reason; the container is
    # still throwaway (`--network none`, capped, removed after).
    dependencies = dependency_plan(tree)
    dockerfile = (f"FROM {base_image}\nCOPY . /skill\nWORKDIR /skill\n"
                  f"RUN mkdir -p /scratch /inputs\n")
    for req in sorted(tree.rglob("requirements.txt")):
        dockerfile += ("RUN python -m pip install --disable-pip-version-check --no-input "
                       "--only-binary=:all: --no-cache-dir -r "
                       f"{req.relative_to(tree).as_posix()}\n")
    (tree / "Dockerfile.exec").write_text(dockerfile)
    build = _run_text(
        ["docker", "build", "--network",
         "default" if dependencies["network_required"] else "none",
         "-f", "Dockerfile.exec", "-t", tag, "."],
        cwd=str(tree), timeout=600)
    if build.returncode != 0:
        raise RuntimeError(f"image build failed: {build.stderr[-2000:]}")
    return tag


def collect_artifacts_with_receipt(scratch: Path) -> tuple[list[dict], dict]:
    out = []
    total = 0
    reasons: list[str] = []
    paths = sorted(p for p in scratch.rglob("*") if p.is_file())
    if len(paths) > MAX_ARTIFACTS:
        reasons.append("artifact-count")
    for p in paths[:MAX_ARTIFACTS]:
        raw = p.read_bytes()
        if total + len(raw) > OUTPUT_LIMIT:
            reasons.append("artifact-bytes")
            break
        total += len(raw)
        entry: dict = {"path": p.relative_to(scratch).as_posix(),
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
    return out, {"discovered": len(paths), "included": len(out),
                 "included_bytes": total, "truncated": bool(reasons),
                 "reasons": reasons}


def collect_artifacts(scratch: Path) -> list[dict]:
    """Backward-compatible artifact list without the execution receipt."""
    return collect_artifacts_with_receipt(scratch)[0]


def execute(manifest: dict, read_bytes, entrypoint: str,
            args: list[str] | None = None,
            policy: dict | None = None, approved: bool = False,
            timeout_s: int = DEFAULT_TIMEOUT_S,
            inputs: list[dict] | None = None,
            work_root: str | Path | None = None) -> dict:
    """Run `entrypoint` from the skill's pinned tree in a fresh container.

    `inputs` (optional): agent files staged to `/inputs`
    (`{path, text}` or `{path, b64}`; 10MB/file, 50MB total). Reference them
    from `args` as `/inputs/<path>`; write results to `/scratch/<path>` —
    everything under `/scratch` returns as `artifacts` (text in `contents`,
    exact bytes in `contents_b64` when <=10MB).
    """
    g = gate(manifest, policy, approved)
    if g["status"] != "ok":
        return {**g, "skill_id": manifest["skill_id"]}
    if (not isinstance(timeout_s, int) or isinstance(timeout_s, bool) or
            not 1 <= timeout_s <= 600):
        raise ValueError("timeout_s must be an integer from 1 to 600")
    if args is not None:
        if (not isinstance(args, list) or len(args) > 200 or
                not all(isinstance(arg, str) and len(arg) <= 32_000 for arg in args)):
            raise ValueError("args must be an array of at most 200 strings (32KB each)")
    if shutil.which("docker") is None:
        raise RuntimeError("docker unavailable: cannot execute skills")
    files = {f["path"] for f in manifest.get("artifact", {}).get("files", [])}
    if entrypoint not in files:
        raise ValueError(f"unknown entrypoint: {entrypoint}")
    declared = manifest.get("artifact", {}).get("entrypoints", [])
    if declared and entrypoint not in declared:
        raise ValueError(f"not a declared entrypoint: {entrypoint}")
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
        dependencies = dependency_plan(tree)
        base_tag = ensure_image(manifest, tree, image)
        # Agent inputs ride an ephemeral per-run layer over the cached
        # skill image (COPY, never a bind mount). Still
        # Docker-out-of-Docker safe.
        tag = base_tag
        run_tag: str | None = None
        if parsed:
            run_tag = f"skill-run-{int(time.time() * 1000)}-{os.getpid()}"
            (staged / "Dockerfile.run").write_text(f"FROM {base_tag}\nCOPY . /inputs\n")
            build = _run_text(
                ["docker", "build", "-f", "Dockerfile.run", "-t", run_tag, "."],
                cwd=str(staged), timeout=300)
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
        run = _run_text(create, timeout=60)
        if run.returncode != 0:
            raise RuntimeError(f"container create failed: {run.stderr[-2000:]}")
        try:
            start = _run_text(["docker", "start", name], timeout=30)
            if start.returncode != 0:
                raise RuntimeError(f"container start failed: {start.stderr[-2000:]}")
            try:
                wait = _run_text(["docker", "wait", name], timeout=timeout_s)
            except subprocess.TimeoutExpired:
                subprocess.run(["docker", "kill", name],
                               capture_output=True, timeout=30)
                raise RuntimeError(f"execution timed out after {timeout_s}s "
                                   f"(container removed)") from None
            exit_code = int((wait.stdout or "0").strip().split()[0])
            logs = _run_text(["docker", "logs", name], timeout=30)
            subprocess.run(["docker", "cp", f"{name}:/scratch/.", str(scratch)],
                           capture_output=True, timeout=60)
        finally:
            subprocess.run(["docker", "rm", "-f", name],
                           capture_output=True, timeout=30)
            if run_tag is not None:
                subprocess.run(["docker", "rmi", "-f", run_tag],
                               capture_output=True, timeout=120)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        stdout_raw = logs.stdout or ""
        stderr_raw = logs.stderr or ""
        stdout = stdout_raw[-OUTPUT_LIMIT:]
        stderr = stderr_raw[-OUTPUT_LIMIT:]
        artifacts, artifact_receipt = collect_artifacts_with_receipt(scratch)
        return {"status": "ok" if exit_code == 0 else "failed",
                "skill_id": manifest["skill_id"],
                "entrypoint": entrypoint,
                "verdict": g["verdict"],
                "exit_code": exit_code,
                "stdout": stdout, "stderr": stderr,
                "stdout_truncated": len(stdout_raw) > OUTPUT_LIMIT,
                "stderr_truncated": len(stderr_raw) > OUTPUT_LIMIT,
                "duration_ms": ms, "image": base_tag,
                "dependencies": dependencies,
                "artifacts": artifacts, "artifact_receipt": artifact_receipt}
    finally:
        if owned_root:
            shutil.rmtree(root, ignore_errors=True)
