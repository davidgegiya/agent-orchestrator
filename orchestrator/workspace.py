"""Capture the exact Git changes and command evidence for a run."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from .utils import is_protected_path_part


def _run_local_cmd(
    args: List[str], *, cwd: Path, timeout_seconds: int, env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            stdin=subprocess.DEVNULL,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout_seconds,
            env=env,
        )
        return {
            "cmd": " ".join(args),
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    except OSError as exc:
        return {
            "cmd": " ".join(args),
            "returncode": 127,
            "stdout": "",
            "stderr": str(exc),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": " ".join(args),
            "returncode": 124,
            "stdout": (exc.stdout or b"").decode("utf-8", errors="replace"),
            "stderr": f"TIMEOUT after {timeout_seconds}s",
        }


def _escape_invalid_utf8(text: str) -> str:
    return text.encode("utf-8", errors="backslashreplace").decode("utf-8")


def _cmd_meta(result: Dict[str, Any], *, limit: int = 800) -> Dict[str, Any]:
    def _truncate_stdio(text: str, max_chars: int) -> Tuple[str, bool]:
        if text is None:
            return ("", False)
        if len(text) <= max_chars:
            return (text, False)
        return (text[:max_chars] + "...<truncated>", True)

    stdout_raw = _escape_invalid_utf8(result.get("stdout") or "")
    stderr_raw = _escape_invalid_utf8(result.get("stderr") or "")
    stdout_preview, stdout_truncated = _truncate_stdio(stdout_raw, limit)
    stderr_preview, stderr_truncated = _truncate_stdio(stderr_raw, limit)
    return {
        "cmd": result.get("cmd"),
        "returncode": result.get("returncode"),
        "stdout": stdout_preview,
        "stderr": stderr_preview,
        "stdout_len": len(stdout_raw),
        "stderr_len": len(stderr_raw),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def _compute_workspace_diff(workspace_dir: Path, base_sha: str = "HEAD") -> Tuple[str, Dict[str, Any]]:
    """
    Produces a patch-like diff for workspace/ using git.

    Includes:
    - tracked changes via `git diff`
    - untracked new files via `git diff --no-index /dev/null <file>`
    """
    meta: Dict[str, Any] = {"available": False, "commands": [], "probes": []}

    def _git_toplevel(cwd: Path, *, label: str) -> Path | None:
        probe = _run_local_cmd(["git", "rev-parse", "--show-toplevel"], cwd=cwd, timeout_seconds=5)
        meta["commands"].append({**_cmd_meta(probe), "tool": "local_cmd", "cwd": str(cwd)})
        meta["probes"].append({"label": label, "cwd": str(cwd), **_cmd_meta(probe)})
        if probe["returncode"] != 0:
            return None
        raw = (probe.get("stdout") or "").strip()
        if not raw:
            return None
        return Path(raw).resolve()

    # Prefer probing from workspace/ because it supports both layouts:
    # - workspace/ is its own git repo (product repo lives inside workspace/)
    # - repo_root is the git repo and workspace/ is a subdirectory
    workspace_top = _git_toplevel(workspace_dir, label="workspace")
    if workspace_top is None:
        return ("- None", meta)

    git_cwd = workspace_top
    if base_sha != "HEAD":
        head = _run_local_cmd(["git", "rev-parse", "--verify", "HEAD"], cwd=git_cwd, timeout_seconds=5)
        if head["returncode"] != 0 or head["stdout"].strip() != base_sha:
            meta["reason"] = "Checkout HEAD no longer matches the run base commit."
            return ("- None", meta)
    try:
        workspace_rel = os.path.relpath(workspace_dir.resolve(), workspace_top.resolve())
    except ValueError:
        return ("- None", meta)

    if workspace_rel == ".":
        pathspec: List[str] = ["."]
        meta["git_base"] = "workspace_repo"
    else:
        pathspec = [workspace_rel]
        meta["git_base"] = "repo_root_repo"
    meta["git_toplevel"] = str(workspace_top)
    meta["workspace_pathspec"] = workspace_rel

    diff_parts: List[str] = []

    status = _run_local_cmd(["git", "-c", "core.fsmonitor=false", "status", "--porcelain", "--", *pathspec], cwd=git_cwd, timeout_seconds=10)
    meta["commands"].append({**_cmd_meta(status), "tool": "local_cmd", "cwd": str(git_cwd)})
    status_out = (status.get("stdout") or "").rstrip()
    if status_out:
        diff_parts.append("# GIT STATUS (workspace)\n" + status_out)

    changed = _run_local_cmd(
        ["git", "diff", "--name-only", "-z", "--no-renames", "--no-ext-diff", base_sha, "--", *pathspec],
        cwd=git_cwd, timeout_seconds=30,
    )
    untracked = _run_local_cmd(
        ["git", "ls-files", "-z", "--others", "--exclude-standard", "--", *pathspec],
        cwd=git_cwd, timeout_seconds=10,
    )
    for command in (changed, untracked):
        meta["commands"].append({**_cmd_meta(command), "tool": "local_cmd", "cwd": str(git_cwd)})
    if any(command["returncode"] != 0 for command in (status, changed, untracked)):
        return ("- None", meta)

    untracked_files = sorted(filter(None, untracked["stdout"].split("\0")))
    changed_files = [*changed["stdout"].split("\0"), *untracked_files]
    if any(is_protected_path_part(part) for name in changed_files for part in Path(name).parts):
        meta["reason"] = "A changed file contains protected Git metadata or local environment data."
        return ("- None", meta)

    tracked = _run_local_cmd(
        ["git", "diff", "--no-color", "--binary", "--no-ext-diff", "--no-textconv", base_sha, "--", *pathspec],
        cwd=git_cwd, timeout_seconds=30,
    )
    meta["commands"].append({**_cmd_meta(tracked, limit=0), "tool": "local_cmd", "cwd": str(git_cwd)})
    if tracked["returncode"] != 0:
        return ("- None", meta)
    tracked_out = tracked["stdout"].rstrip()
    if tracked_out:
        diff_parts.append("# GIT DIFF (workspace)\n" + tracked_out)

    null_path = "/dev/null" if Path("/dev/null").exists() else "NUL"
    for rel_path in untracked_files:
        patch = _run_local_cmd(
            ["git", "diff", "--no-color", "--binary", "--no-index", "--no-ext-diff", "--no-textconv", "--", null_path, rel_path],
            cwd=git_cwd,
            timeout_seconds=30,
        )
        meta["commands"].append({**_cmd_meta(patch, limit=0), "tool": "local_cmd", "cwd": str(git_cwd)})
        if patch["returncode"] not in (0, 1):
            return ("- None", meta)
        patch_out = (patch.get("stdout") or "").rstrip()
        if patch_out:
            diff_parts.append("# NEW FILE (untracked)\n" + patch_out)

    meta["available"] = True
    if not diff_parts:
        return ("- None", meta)

    patch_text = "\n\n".join(diff_parts).rstrip() + "\n"
    try:
        patch_text.encode("utf-8")
    except UnicodeEncodeError:
        meta["available"] = False
        meta["reason"] = "The patch contains text that is not valid UTF-8."
        return ("- None", meta)
    return (patch_text, meta)


def create_detached_worktree(source: Path, destination: Path) -> None:
    """Materialize the source HEAD without bringing along its local changes."""
    toplevel = _run_local_cmd(["git", "rev-parse", "--show-toplevel"], cwd=source, timeout_seconds=5)
    if toplevel["returncode"] != 0:
        raise ValueError("worktree source must be a Git checkout")
    source_root = Path(toplevel["stdout"].strip()).resolve()
    if destination.exists() or destination.is_relative_to(source_root):
        raise ValueError("worktree destination must be a new path outside the source checkout")
    result = _run_local_cmd(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false",
         "worktree", "add", "--detach", "--", str(destination), "HEAD"],
        cwd=source, timeout_seconds=120,
    )
    if result["returncode"] != 0:
        raise ValueError(_escape_invalid_utf8(result["stderr"]).strip() or "Could not create the Git worktree.")
