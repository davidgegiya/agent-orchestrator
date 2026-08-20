"""Adapter for the non-interactive Codex CLI."""

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory


def run_codex(prompt: str, workspace: Path, timeout: int) -> tuple[str, str | None]:
    with TemporaryDirectory(prefix="orchestrator-codex-") as temp:
        message = Path(temp) / "message.txt"
        command = [
            "codex", "--ask-for-approval", "never", "exec",
            "--sandbox", "workspace-write", "--ephemeral",
            "-c", "sandbox_workspace_write.network_access=false",
            "--output-last-message", str(message), "-",
        ]
        try:
            result = subprocess.run(
                command, cwd=workspace, input=prompt, text=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return "", f"Codex exceeded the {timeout}s limit."
        except OSError as exc:
            return "", f"Could not start Codex: {exc}"
        if result.returncode != 0:
            return "", f"Codex exited with status {result.returncode}."
        try:
            output = message.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            return "", "Codex did not write a readable final message."
        return (output, None) if output else ("", "Codex wrote an empty final message.")
