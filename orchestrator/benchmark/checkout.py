"""Materialize a pinned upstream commit on explicit request."""

import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from .catalog import Scenario


def git_call(cwd: Path, *arguments: str, timeout: int = 60) -> str:
    result = subprocess.run(
        ["git", "-c", f"core.hooksPath={os.devnull}", "-c", "core.fsmonitor=false", *arguments],
        cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
    )
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or f"git {arguments[0]} failed")
    return result.stdout.strip()


def prepare_checkout(case: Scenario, destination: Path) -> Path:
    """Fetch exactly one commit into a new detached checkout."""
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("checkout destination already exists")
    if not destination.parent.is_dir():
        raise ValueError("checkout destination parent must exist")
    with TemporaryDirectory(prefix=".benchmark-", dir=destination.parent) as work:
        source = Path(work) / "checkout"
        source.mkdir()
        git_call(source, "init", "--quiet")
        git_call(source, "fetch", "--quiet", "--depth=1", "--no-tags",
                 case.clone_url, case.base_commit, timeout=180)
        fetched = git_call(source, "rev-parse", "--verify", "FETCH_HEAD")
        if fetched != case.base_commit:
            raise ValueError("fetched commit differs from the catalog base commit")
        git_call(source, "checkout", "--quiet", "--detach", fetched, "--")
        destination.mkdir()
        # Keep ownership of the newly created destination until the rename succeeds.
        try:
            source.rename(destination)
        except OSError:
            destination.rmdir()
            raise
    return destination
