from hashlib import sha256
import json
from pathlib import Path
from shlex import join
import subprocess
import sys
from types import SimpleNamespace

from agents import Runner
import pytest

from orchestrator import main
from orchestrator.policies import ReviewerOutput


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    product = tmp_path / "product"
    product.mkdir()
    git(product, "init", "-q")
    (product / "implementation.txt").write_text("before\n")
    git(product, "add", ".")
    git(product, "-c", "commit.gpgsign=false", "-c", "user.name=Fixture",
        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Initial state")
    task = tmp_path / "task.md"
    task.write_text("Change implementation.txt to the accepted value.\n")
    reports = tmp_path / "reports"
    create_run_dir = main.create_run_dir
    monkeypatch.setattr(main, "create_run_dir", lambda _: create_run_dir(reports))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test")
    return product, task, reports


def run(workspace, monkeypatch, check_code, *, change_while_reviewing=False):
    product, task, reports = workspace

    def fake_run(agent, **kwargs):
        if agent.name == "Implementer":
            (product / "implementation.txt").write_text("accepted\n")
            git(product, "add", "implementation.txt")
        if agent.name == "Reviewer":
            assert "+accepted" in kwargs["input"]
            if change_while_reviewing:
                (product / "implementation.txt").write_text("untested\n")
            return SimpleNamespace(final_output=ReviewerOutput(verdict="PASS", action="CONTINUE", fixes=[]))
        return SimpleNamespace(final_output="Implement the change.")

    monkeypatch.setattr(Runner, "run_sync", fake_run)
    command = f'{sys.executable} -c "{check_code}"'
    code = main.main(["--workspace", str(product), "--task", str(task), "--check", command])
    return code, json.loads(next(reports.glob("*/artifacts.json")).read_text())


def test_accepts_review_of_checked_staged_patch(workspace, monkeypatch):
    code, result = run(workspace, monkeypatch, "import os; assert 'OPENAI_API_KEY' not in os.environ")
    assert code == 0 and result["final_verdict"] == "PASS"
    round_result = result["rounds"][0]
    assert round_result["checks"][0]["returncode"] == 0
    assert sha256(Path(round_result["diff"]["path"]).read_bytes()).hexdigest() == round_result["diff"]["sha256"]


def test_failed_check_blocks_reviewer_pass(workspace, monkeypatch):
    code, result = run(workspace, monkeypatch, "raise SystemExit(3)")
    assert code == 1 and result["final_verdict"] == "FAIL"
    assert result["rounds"][0]["checks"][0]["returncode"] == 3


def test_workspace_change_during_review_blocks_pass(workspace, monkeypatch):
    code, result = run(workspace, monkeypatch, "pass", change_while_reviewing=True)
    assert code == 1 and result["final_verdict"] == "FAIL"
    assert "changed during review" in result["error"]


def test_resume_rechecks_saved_patch_without_rerunning_writer(workspace, monkeypatch):
    product, task, reports = workspace
    roles = []
    check_marker = product.parent / "check-runs.txt"
    check_script = product.parent / "verify.py"
    check_script.write_text(
        f"from pathlib import Path\nwith Path({str(check_marker)!r}).open('a') as stream:\n"
        "    stream.write('checked\\n')\n"
    )

    def fake_run(agent, **kwargs):
        roles.append(agent.name)
        if agent.name == "Implementer":
            (product / "implementation.txt").write_text("accepted\n")
        if agent.name == "Reviewer":
            if roles.count("Reviewer") == 1:
                raise RuntimeError("offline interruption")
            assert "+accepted" in kwargs["input"]
            return SimpleNamespace(final_output=ReviewerOutput(verdict="PASS", action="CONTINUE", fixes=[]))
        return SimpleNamespace(final_output="Implement the change.")

    monkeypatch.setattr(Runner, "run_sync", fake_run)
    args = ["--workspace", str(product), "--task", str(task), "--check", join([sys.executable, str(check_script)])]
    assert main.main(args) == 1
    source = next(reports.iterdir())
    assert main.main([*args, "--resume", str(source)]) == 0
    assert roles == ["Planner", "Implementer", "Reviewer", "Reviewer"]
    assert check_marker.read_text().splitlines() == ["checked", "checked"]
    resumed = next(path for path in reports.iterdir() if path != source)
    assert json.loads((resumed / "artifacts.json").read_text())["resumed_from"] == str(source)

    (product / "implementation.txt").write_text("different patch\n")
    with pytest.raises(SystemExit) as error:
        main.main([*args, "--resume", str(source)])
    assert error.value.code == 2
