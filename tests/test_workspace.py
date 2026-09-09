import subprocess

import pytest

from orchestrator.workspace import _compute_workspace_diff, create_detached_worktree


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "app.txt").write_text("baseline\n")
    git(root, "add", ".")
    commit(root)
    return root


def commit(repo):
    git(repo, "-c", "commit.gpgsign=false", "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture")


def test_blocks_dotenv_patch_before_reading_its_content(repo):
    (repo / ".env.local").write_text("PRIVATE=synthetic")
    patch, meta = _compute_workspace_diff(repo)
    assert not meta["available"]
    assert "protected" in meta["reason"]
    assert "synthetic" not in patch and "synthetic" not in str(meta)


def test_rejects_checkout_head_changed_after_start(repo):
    base = git(repo, "rev-parse", "HEAD")
    git(repo, "-c", "commit.gpgsign=false", "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "commit", "--allow-empty", "-qm", "Moved")
    _, meta = _compute_workspace_diff(repo, base)
    assert not meta["available"]
    assert "HEAD" in meta["reason"]


def test_detached_worktree_uses_committed_state(repo):
    (repo / "app.txt").write_text("local edit\n")
    destination = repo.with_name("isolated")
    create_detached_worktree(repo, destination)
    assert (destination / "app.txt").read_text() == "baseline\n"
    assert (repo / "app.txt").read_text() == "local edit\n"
    assert git(destination, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    with pytest.raises(ValueError, match="new path outside"):
        create_detached_worktree(repo, repo / "nested")


def test_cannot_create_worktree_inside_source_from_a_subdirectory(repo):
    subdir = repo / "nested-source"
    subdir.mkdir()
    with pytest.raises(ValueError, match="new path outside"):
        create_detached_worktree(subdir, repo / "nested-destination")
