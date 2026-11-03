from pathlib import Path
import subprocess

from orchestrator import codex_runner


def test_runs_in_workspace_and_reads_final_message(tmp_path, monkeypatch):
    def fake_exec(command, *, cwd, input, timeout, **kwargs):
        assert cwd == tmp_path and input == "Implement the task" and timeout == 45
        assert command[command.index("--sandbox") + 1] == "workspace-write"
        assert "sandbox_workspace_write.network_access=false" in command
        assert command[command.index("--ask-for-approval") + 1] == "never"
        output_path = Path(command[command.index("--output-last-message") + 1])
        output_path.write_text("Implemented.", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(codex_runner.subprocess, "run", fake_exec)
    assert codex_runner.run_codex("Implement the task", tmp_path, 45) == ("Implemented.", None)


def test_fails_when_cli_never_writes_a_result(tmp_path, monkeypatch):
    def fake_exec(command, **kwargs):
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(codex_runner.subprocess, "run", fake_exec)
    output, error = codex_runner.run_codex("Implement the task", tmp_path, 45)
    assert not output
    assert "final message" in error
