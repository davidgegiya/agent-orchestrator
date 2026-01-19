from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from agents import function_tool
from agents.tool_context import ToolContext

from .models import RunContext


def _resolve_path(base: Path, rel_path: str) -> Path:
    if rel_path is None or rel_path == "":
        raise ValueError("Path is required")
    if rel_path.startswith("~"):
        raise ValueError("Tilde paths are not allowed")
    candidate = Path(rel_path)
    if candidate.parts and candidate.parts[0] == base.name:
        candidate = Path(*candidate.parts[1:]) if len(candidate.parts) > 1 else Path(".")
    if candidate.is_absolute():
        raise ValueError("Absolute paths are not allowed")
    resolved = (base / candidate).resolve()
    base_resolved = base.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError as exc:
        raise ValueError("Path escapes the allowed base directory") from exc
    return resolved


def _log_event(ctx: RunContext, payload: Dict[str, Any]) -> None:
    ctx.tool_events.append(payload)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@function_tool
def fs_read(context: ToolContext[RunContext], path: str) -> str:
    ctx = context.context
    target = _resolve_path(ctx.fs_base, path)
    content = target.read_text(encoding="utf-8")
    _log_event(ctx, {"tool": "fs_read", "path": str(target)})
    return content


@function_tool
def fs_write(context: ToolContext[RunContext], path: str, content: str) -> str:
    ctx = context.context
    if not ctx.allow_write:
        raise PermissionError("Write access is not allowed for this role")
    target = _resolve_path(ctx.fs_base, path)
    if ctx.role == "tech_writer":
        reports_dir = (ctx.repo_root / "project" / "reports").resolve()
        if _is_within(target.resolve(), reports_dir):
            raise PermissionError("project/reports is write-protected for this role")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _log_event(ctx, {"tool": "fs_write", "path": str(target), "bytes": len(content.encode("utf-8"))})
    return json.dumps({"ok": True, "path": str(target)})


@function_tool
def fs_list(context: ToolContext[RunContext], path: str = ".") -> str:
    ctx = context.context
    target = _resolve_path(ctx.fs_base, path)
    if not target.exists():
        raise FileNotFoundError(f"Path does not exist: {target}")
    if target.is_file():
        listing = [target.name]
    else:
        listing = sorted([child.name for child in target.iterdir()])
    _log_event(ctx, {"tool": "fs_list", "path": str(target), "count": len(listing)})
    return json.dumps({"path": str(target), "entries": listing})
