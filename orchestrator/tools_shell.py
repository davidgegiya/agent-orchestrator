from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Dict

from agents import function_tool
from agents.tool_context import ToolContext

from .models import RunContext

BLOCK_PATTERNS = [
    r"\bpip\s+install\b",
    r"\bpip3\s+install\b",
    r"\bpython\s+-m\s+pip\s+install\b",
    r"\bpython3\s+-m\s+pip\s+install\b",
    r"\buv\s+pip\s+install\b",
    r"\bpoetry\s+install\b",
    r"\bpoetry\s+add\b",
    r"\bpipx\s+install\b",
    r"\bnpm\s+install\b",
    r"\byarn\s+add\b",
    r"\bpnpm\s+install\b",
    r"\bconda\s+install\b",
    r"\bbrew\s+install\b",
    r"\bapt-get\s+install\b",
]

BLOCK_RE = re.compile("|".join(BLOCK_PATTERNS), re.IGNORECASE)

HEREDOC_START_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z0-9_]+)\1")

ESCAPE_PATTERNS = [
    r"(^|[;&|]\s*|\s)cd\s",  # cwd changes (can escape workspace/)
    r"\.\./",  # parent traversal
    r"\.\.\\",  # parent traversal (windows style)
    r"(?:^|\s)/",  # absolute paths
    r"(?:^|\s)~",  # home expansion
]

ESCAPE_RE = re.compile("|".join(ESCAPE_PATTERNS), re.IGNORECASE)

STDIO_LIMIT = 4000


def _truncate(text: str) -> str:
    if text is None:
        return ""
    if len(text) <= STDIO_LIMIT:
        return text
    return text[:STDIO_LIMIT] + "...<truncated>"


def _log_event(ctx: RunContext, payload: Dict[str, Any]) -> None:
    ctx.tool_events.append(payload)


def _strip_heredoc_bodies(cmd: str) -> str:
    """
    Remove heredoc bodies from the command string so safety regex checks don't
    falsely match inside the heredoc content (which isn't interpreted by the shell).
    """
    lines = cmd.splitlines()
    out_lines: list[str] = []
    active_delim: str | None = None

    for line in lines:
        if active_delim is not None:
            if line.strip() == active_delim:
                active_delim = None
            continue

        out_lines.append(line)
        match = HEREDOC_START_RE.search(line)
        if match:
            active_delim = match.group(2)

    return "\n".join(out_lines)


@function_tool
def run_cmd(context: ToolContext[RunContext], cmd: str, timeout_seconds: int = 30) -> str:
    ctx = context.context
    if BLOCK_RE.search(cmd):
        payload = {"cmd": cmd, "returncode": 126, "stdout": "", "stderr": "BLOCKED"}
        _log_event(ctx, {"tool": "run_cmd", **payload, "blocked": True, "blocked_reason": "install"})
        return json.dumps(payload)

    scan_cmd = _strip_heredoc_bodies(cmd)
    if ESCAPE_RE.search(scan_cmd):
        payload = {"cmd": cmd, "returncode": 126, "stdout": "", "stderr": "BLOCKED"}
        _log_event(ctx, {"tool": "run_cmd", **payload, "blocked": True, "blocked_reason": "escape"})
        return json.dumps(payload)

    cwd = ctx.shell_cwd
    if not cwd.exists():
        raise FileNotFoundError(f"Workspace does not exist: {cwd}")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        payload = {
            "cmd": cmd,
            "returncode": result.returncode,
            "stdout": _truncate(result.stdout),
            "stderr": _truncate(result.stderr),
        }
    except subprocess.TimeoutExpired as exc:
        payload = {
            "cmd": cmd,
            "returncode": 124,
            "stdout": _truncate(exc.stdout or ""),
            "stderr": _truncate(f"TIMEOUT after {timeout_seconds}s"),
        }
    _log_event(ctx, {"tool": "run_cmd", **payload, "blocked": False})
    return json.dumps(payload)
