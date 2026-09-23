from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shlex
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from filelock import FileLock, Timeout

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for local dev
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

from .checkpoint import ReviewCheckpoint, digest_text, run_inputs_digest
from .codex_runner import run_codex
from .models import RunContext
from .workspace import _cmd_meta, _compute_workspace_diff, _run_local_cmd, create_detached_worktree
from .policies import (
    MAX_ROUNDS,
    ReviewDecision,
    ReviewerOutput,
    is_stuck,
    max_turns_for_role,
    model_for_role,
    parse_reviewer_output,
    retry_base_delay_seconds,
    retry_max_attempts_for_role,
    retry_max_delay_seconds,
)
from .reporting import RunReport, create_run_dir
from .utils import is_effectively_empty, read_text

DEMO_TASK = """Create a tiny Python package inside workspace/:
- app/greeter.py with a greet(name: str) -> str function
- pytest tests for greet
- workspace/requirements.txt listing pytest
- workspace/README.md with usage and test instructions
"""


def _load_optional(path: Path) -> str:
    return read_text(path, required=False)


def _truncate_for_prompt(text: str, max_chars: int, *, label: str) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n\n...[{label} truncated to {max_chars} chars]"


def _build_planner_input(task: str, backlog: str, vision: str, architecture: str, conventions: str) -> str:
    return (
        "VISION:\n"
        f"{vision.strip()}\n\n"
        "ARCHITECTURE:\n"
        f"{architecture.strip()}\n\n"
        "CONVENTIONS:\n"
        f"{conventions.strip()}\n\n"
        "BACKLOG:\n"
        f"{backlog.strip()}\n\n"
        "TASK:\n"
        f"{task.strip()}\n"
    )


def _build_implementer_input(
    task: str,
    plan: str,
    fixes: Optional[List[str]],
    architecture: str,
    conventions: str,
) -> str:
    fixes_block = "\n".join(f"- {fix}" for fix in fixes) if fixes else "- None"
    return (
        "ARCHITECTURE:\n"
        f"{architecture.strip()}\n\n"
        "CONVENTIONS:\n"
        f"{conventions.strip()}\n\n"
        "TASK:\n"
        f"{task.strip()}\n\n"
        "PLAN:\n"
        f"{plan.strip()}\n\n"
        "REVIEW_FIXES:\n"
        f"{fixes_block}\n"
    )


def _build_reviewer_input(
    architecture: str,
    conventions: str,
    task: str,
    plan: str,
    red_flags: str,
    diff_text: str,
    tool_outputs: str,
    implementer_report: str,
) -> str:
    return (
        "ARCHITECTURE:\n"
        f"{architecture.strip()}\n\n"
        "CONVENTIONS:\n"
        f"{conventions.strip()}\n\n"
        "TASK:\n"
        f"{task.strip()}\n\n"
        "PLAN:\n"
        f"{plan.strip()}\n\n"
        "RED_FLAGS:\n"
        f"{red_flags.strip()}\n\n"
        "DIFF:\n"
        f"{diff_text.strip()}\n\n"
        "TOOL_OUTPUTS:\n"
        f"{tool_outputs.strip()}\n\n"
        "IMPLEMENTER_REPORT:\n"
        f"{implementer_report.strip()}\n"
    )


def _build_tech_writer_input(vision: str, architecture: str, conventions: str, task: str, plan: str, reviewer: ReviewDecision) -> str:
    return (
        "VISION:\n"
        f"{vision.strip()}\n\n"
        "ARCHITECTURE:\n"
        f"{architecture.strip()}\n\n"
        "CONVENTIONS:\n"
        f"{conventions.strip()}\n\n"
        "TASK:\n"
        f"{task.strip()}\n\n"
        "PLAN:\n"
        f"{plan.strip()}\n\n"
        "REVIEW:\n"
        f"{reviewer.raw.strip()}\n"
    )


def _collect_tool_events(ctx: RunContext) -> Dict[str, Any]:
    commands = [event for event in ctx.tool_events if event.get("tool") == "run_cmd"]
    files_written = [event for event in ctx.tool_events if event.get("tool") == "fs_write"]
    return {
        "commands": commands,
        "files_written": files_written,
        "events": ctx.tool_events,
    }


def _needs_docs_update(decision: ReviewDecision) -> bool:
    return any(fix.strip().upper().startswith("DOCS:") for fix in decision.fixes)


def _format_tool_outputs(events: List[Dict[str, Any]]) -> str:
    run_cmd_events = [e for e in events if e.get("tool") == "run_cmd"]
    fs_write_events = [e for e in events if e.get("tool") == "fs_write"]

    lines: List[str] = []
    if fs_write_events:
        lines.append("FILES_WRITTEN:")
        for e in fs_write_events:
            lines.append(f"- {e.get('path')}")
    if run_cmd_events:
        lines.append("COMMAND_RESULTS:")
        for e in run_cmd_events:
            cmd = (e.get("cmd") or "").strip()
            rc = e.get("returncode")
            blocked = bool(e.get("blocked"))
            lines.append(f"- {cmd} -> {rc}{' (BLOCKED)' if blocked else ''}")
            stderr = (e.get("stderr") or "").strip()
            if stderr:
                snippet = stderr if len(stderr) <= 400 else (stderr[:400] + "...<truncated>")
                lines.append(f"  stderr: {snippet}")
    if not lines:
        return "- None"
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n...[truncated to {limit} chars]"


_RED_FLAG_PATTERNS = [
    ("InMemoryClass", re.compile(r"\bInMemory[A-Za-z0-9_]*\b")),
    ("SQLiteMemory", re.compile(r":memory:")),
    ("MockOrFake", re.compile(r"\b(Mock|Fake)[A-Za-z0-9_]*\b")),
]


def _scan_red_flags(workspace_dir: Path) -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {"files_scanned": 0, "matches": []}
    hits: List[str] = []

    exclude_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    max_file_bytes = 512_000
    code_exts = {".py", ".pyi"}

    def is_test_path(rel_path: str) -> bool:
        norm = rel_path.replace("\\", "/")
        return (
            "/tests/" in norm
            or norm.startswith("tests/")
            or norm.endswith("_test.py")
            or "/test_" in norm
        )

    for root, dirs, files in os.walk(workspace_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dirs and not d.startswith(".")]
        for filename in files:
            if filename.startswith("."):
                continue
            path = Path(root) / filename
            if path.suffix not in code_exts:
                continue
            try:
                if path.stat().st_size > max_file_bytes:
                    continue
            except FileNotFoundError:
                continue
            try:
                if path.is_symlink():
                    continue
                content = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            meta["files_scanned"] += 1
            rel_path = str(path.relative_to(workspace_dir))
            scope = "tests" if is_test_path(rel_path) else "code"
            for name, pattern in _RED_FLAG_PATTERNS:
                for match in pattern.finditer(content):
                    line_no = content.count("\n", 0, match.start()) + 1
                    entry = f"- {name} ({scope}): {rel_path}:{line_no}"
                    hits.append(entry)
                    meta["matches"].append(
                        {
                            "pattern": name,
                            "path": rel_path,
                            "line": line_no,
                            "scope": scope,
                        }
                    )
                    break

    if not hits:
        return ("- None", meta)

    return ("\n".join(hits), meta)


def _safe_run(
    agent,
    input_text: str,
    max_turns: int,
    context: Optional[RunContext] = None,
    *,
    role: str,
) -> Tuple[str, Optional[str], Dict[str, Any]]:
    max_attempts = retry_max_attempts_for_role(role)
    base_delay = retry_base_delay_seconds()
    max_delay = retry_max_delay_seconds()

    errors: List[Dict[str, Any]] = []

    def is_retryable(exc: Exception) -> bool:
        try:
            import openai
        except Exception:  # noqa: BLE001
            openai = None  # type: ignore[assignment]
        try:
            import httpx
        except Exception:  # noqa: BLE001
            httpx = None  # type: ignore[assignment]
        try:
            import httpcore
        except Exception:  # noqa: BLE001
            httpcore = None  # type: ignore[assignment]

        if openai is not None:
            retryable = (
                openai.APIConnectionError,
                openai.APITimeoutError,
                openai.RateLimitError,
                openai.InternalServerError,
            )
            if isinstance(exc, retryable):
                return True

        if httpx is not None:
            if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
                return True

        if httpcore is not None:
            if isinstance(exc, (httpcore.TimeoutException, httpcore.NetworkError, httpcore.RemoteProtocolError)):
                return True

        return False

    last_error: Optional[str] = None

    for attempt in range(1, max_attempts + 1):
        try:
            from agents import RunConfig, Runner

            result = Runner.run_sync(
                agent,
                input=input_text,
                max_turns=max_turns,
                context=context,
                run_config=RunConfig(trace_include_sensitive_data=False),
            )
            meta = {
                "role": role,
                "attempts": attempt,
                "max_attempts": max_attempts,
                "base_delay_seconds": base_delay,
                "max_delay_seconds": max_delay,
                "errors": errors,
            }
            output = result.final_output
            if isinstance(output, ReviewerOutput):
                output = output.model_dump_json()
            return (output or "", None, meta)
        except Exception as exc:  # noqa: BLE001
            retryable = is_retryable(exc)
            last_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            errors.append(
                {
                    "attempt": attempt,
                    "retryable": retryable,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            # A new model run would replay tools against a workspace already changed
            # by the failed attempt. Keep that attempt's evidence for inspection.
            if context is not None and context.allow_write:
                errors[-1]["retry_suppressed"] = "role_can_modify_files"
                break
            if (not retryable) or attempt >= max_attempts:
                break

            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            errors[-1]["sleep_seconds"] = delay
            time.sleep(delay)

    meta = {
        "role": role,
        "attempts": len(errors),
        "max_attempts": max_attempts,
        "base_delay_seconds": base_delay,
        "max_delay_seconds": max_delay,
        "errors": errors,
    }
    return ("", last_error, meta)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Implement and review a repository task")
    parser.add_argument("--project-dir", type=Path, help="Directory containing project context and reports")
    parser.add_argument("--workspace", type=Path, help="Product Git checkout to edit")
    parser.add_argument("--worktree", type=Path, help="Create a detached checkout from --workspace HEAD")
    parser.add_argument("--task", type=Path, help="Markdown task to run")
    parser.add_argument("--resume", type=Path, help="Verify the saved patch from an interrupted run")
    parser.add_argument("--implementer", choices=("sdk", "codex"), default="sdk")
    parser.add_argument("--codex-timeout", type=int, default=900)
    parser.add_argument("--write-project-notes", action="store_true",
                        help="Allow the documentation role to update this project's notes")
    parser.add_argument("--check-timeout", type=int, default=120)
    parser.add_argument("--check", action="append", help="Required check command (repeatable; no shell)")
    args = parser.parse_args(argv)
    if args.codex_timeout < 1 or args.check_timeout < 1:
        parser.error("command timeouts must be positive")
    if args.implementer == "codex" and not args.workspace:
        parser.error("--implementer codex requires an explicit --workspace checkout")
    try:
        checks = [shlex.split(command) for command in args.check] if args.check else [
            [sys.executable, "-m", "pytest", "-q"]
        ]
        if not all(checks):
            raise ValueError("check command cannot be empty")
    except ValueError as exc:
        parser.error(str(exc))

    package_root = Path(__file__).resolve().parents[1]
    default_root = package_root if (package_root / "project" / "tasks").is_dir() else Path.cwd()
    repo_root = (args.project_dir or default_root).resolve()
    if not repo_root.is_dir():
        parser.error(f"project directory does not exist: {repo_root}")
    workspace = (args.workspace or (repo_root / "workspace")).resolve()
    if not workspace.is_dir():
        parser.error(f"workspace is not a directory: {workspace}")
    if args.resume and not args.workspace:
        parser.error("--resume requires an explicit --workspace checkout")
    if args.workspace and not args.task:
        parser.error("--workspace requires --task")
    if args.worktree:
        if not args.workspace or args.resume:
            parser.error("--worktree requires --workspace and a new run")
        try:
            destination = args.worktree.resolve()
            create_detached_worktree(workspace, destination)
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        workspace = destination
    git_dir = _run_local_cmd(["git", "rev-parse", "--absolute-git-dir"], cwd=workspace, timeout_seconds=5)
    if git_dir["returncode"] != 0:
        parser.error("workspace must be in a Git checkout")
    lock_path = Path(git_dir["stdout"].strip()) / "agent-orchestrator.lock"
    lock = FileLock(lock_path, timeout=0)
    try:
        lock.acquire()
    except Timeout:
        parser.error("another orchestrator run is using this checkout")
    try:
        return _run_workflow(parser, args, checks, repo_root, workspace)
    finally:
        lock.release()


def _run_workflow(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
    checks: list[list[str]],
    repo_root: Path,
    workspace: Path,
) -> int:
    base = _run_local_cmd(["git", "rev-parse", "--verify", "HEAD"], cwd=workspace, timeout_seconds=5)
    if base["returncode"] != 0:
        parser.error("workspace must be in a Git checkout with a base commit")
    base_sha = base["stdout"].strip()
    if args.workspace and not args.resume:
        status = _run_local_cmd(
            ["git", "-c", "core.fsmonitor=false", "status", "--porcelain", "--untracked-files=all", "--", "."],
            cwd=workspace, timeout_seconds=5,
        )
        if status["returncode"] != 0 or status["stdout"]:
            parser.error("--workspace must be clean before starting; use a separate Git worktree")
    load_dotenv(repo_root / ".env")

    limit_defaults = {
        "ORCH_PLANNER_BACKLOG_MAX_CHARS": 8000,
        "ORCH_REVIEWER_DIFF_MAX_CHARS": 12000,
        "ORCH_REVIEWER_RED_FLAGS_MAX_CHARS": 4000,
    }
    limits = {}
    for name, default in limit_defaults.items():
        raw_value = os.environ.get(name, str(default))
        try:
            value = int(raw_value)
        except ValueError:
            parser.error(f"{name} must be an integer")
        if value < 0 or (name == "ORCH_REVIEWER_DIFF_MAX_CHARS" and value == 0):
            parser.error(f"{name} is outside its supported range")
        limits[name] = value

    run_dir = create_run_dir(repo_root / "project" / "reports")
    report = RunReport(run_dir)

    print(f"Reports: {run_dir}")

    artifacts: Dict[str, Any] = {
        "run_dir": str(run_dir),
        "workspace": str(workspace),
        "base_sha": base_sha,
        "implementer_backend": args.implementer,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "rounds": [],
        "final_verdict": "FAIL",
        "final_action": "SKIP",
        "models": {
            "planner": model_for_role("planner"),
            "implementer": model_for_role("implementer"),
            "reviewer": model_for_role("reviewer"),
            "tech_writer": model_for_role("tech_writer"),
        },
        "max_turns": {
            "planner": max_turns_for_role("planner"),
            "implementer": max_turns_for_role("implementer"),
            "reviewer": max_turns_for_role("reviewer"),
            "tech_writer": max_turns_for_role("tech_writer"),
        },
    }

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        artifacts["error"] = "Missing OPENAI_API_KEY"
        report.write_artifacts(artifacts)
        print("Verdict: FAIL")
        print("Action: SKIP")
        return 1

    try:
        importlib.import_module("agents")
    except ImportError as exc:
        artifacts["error"] = f"Missing dependency: {exc}"
        report.write_artifacts(artifacts)
        print("Verdict: FAIL")
        print("Action: SKIP")
        return 1

    from .agents import (
        build_implementer_agent,
        build_planner_agent,
        build_reviewer_agent,
        build_tech_writer_agent,
    )

    task_path = args.task or (repo_root / "project" / "tasks" / "current.md")
    try:
        task_text = read_text(task_path, required=True)
    except (OSError, UnicodeError) as exc:
        parser.error(str(exc))
    task_source = str(task_path)
    if is_effectively_empty(task_text):
        if args.task:
            parser.error("--task must contain a task")
        task_text = DEMO_TASK
        task_source = "demo"
    artifacts["task_source"] = task_source
    artifacts["task_sha256"] = digest_text(task_text.rstrip())

    vision = _load_optional(repo_root / "project" / "vision.md")
    architecture = _load_optional(repo_root / "project" / "architecture.md")
    conventions = _load_optional(repo_root / "project" / "conventions.md")
    backlog_raw = _load_optional(repo_root / "project" / "tasks" / "backlog.md")
    backlog_max_chars = limits["ORCH_PLANNER_BACKLOG_MAX_CHARS"]
    backlog = "" if is_effectively_empty(backlog_raw) else _truncate_for_prompt(backlog_raw, backlog_max_chars, label="BACKLOG")
    artifacts["backlog_included"] = bool(backlog.strip())
    artifacts["backlog_max_chars"] = backlog_max_chars

    planner_input = _build_planner_input(task_text, backlog, vision, architecture, conventions)
    inputs_sha256 = run_inputs_digest(planner_input, checks, args.implementer)
    checkpoint = None
    if args.resume:
        source_report = RunReport(args.resume.resolve())
        try:
            checkpoint = source_report.read_checkpoint()
            if source_report.artifacts_path.exists():
                previous_run = json.loads(source_report.artifacts_path.read_text(encoding="utf-8"))
                if previous_run.get("final_verdict") == "PASS" and previous_run.get("ended_at"):
                    parser.error("the source run has already passed")
        except (OSError, ValueError) as exc:
            parser.error(f"could not load review checkpoint: {exc}")
        if checkpoint.workspace != workspace or checkpoint.base_sha != base_sha:
            parser.error("workspace or base commit differs from the checkpoint")
        if checkpoint.inputs_sha256 != inputs_sha256:
            parser.error("task context, check commands or implementer differs from the checkpoint")
        if checkpoint.round_number > MAX_ROUNDS:
            parser.error("checkpoint exceeds the configured round limit")
        patch_text, patch_meta = _compute_workspace_diff(workspace, base_sha)
        if not patch_meta["available"] or digest_text(patch_text) != checkpoint.patch_sha256:
            parser.error("workspace changes differ from the checkpoint")
        plan_text = checkpoint.plan
        artifacts["resumed_from"] = str(source_report.run_dir)
    else:
        planner = build_planner_agent()
        plan_text, plan_error, plan_meta = _safe_run(
            planner, planner_input, artifacts["max_turns"]["planner"], role="planner",
        )
        artifacts["planner_run"] = plan_meta
        if plan_error or not plan_text.strip():
            artifacts["error"] = f"Planner failed: {plan_error or 'empty plan'}"
            report.write_artifacts(artifacts)
            print("Verdict: FAIL")
            print("Action: SKIP")
            return 1
    report.write_plan(plan_text)
    artifacts["plan_path"] = str(report.plan_path)
    report.write_artifacts(artifacts)

    implementer = build_implementer_agent() if args.implementer == "sdk" else None
    reviewer = build_reviewer_agent()
    write_project_notes = not args.workspace or args.write_project_notes

    previous_reviewer_text = ""
    review_decision: Optional[ReviewDecision] = None

    loop_exhausted = True
    start_round = checkpoint.round_number if checkpoint is not None else 1
    for round_idx in range(start_round, MAX_ROUNDS + 1):
        implementer_ctx = RunContext(
            role="implementer",
            repo_root=repo_root,
            fs_base=workspace,
            allow_write=True,
            shell_cwd=workspace,
        )
        implementer_input = _build_implementer_input(
            task_text,
            plan_text,
            review_decision.fixes if review_decision else None,
            architecture,
            conventions,
        )
        if checkpoint is not None and round_idx == start_round:
            implementer_report, impl_error = checkpoint.implementer_report, None
            implementer_meta = {"backend": args.implementer, "resumed": True}
        elif args.implementer == "codex":
            implementer_report, impl_error = run_codex(
                "Implement the task in this checkout. Leave changes for review and do not commit.\n\n"
                + implementer_input, workspace, args.codex_timeout,
            )
            implementer_meta = {"backend": "codex", "timeout_seconds": args.codex_timeout}
        else:
            implementer_report, impl_error, implementer_meta = _safe_run(
                implementer,
                implementer_input,
                artifacts["max_turns"]["implementer"],
                context=implementer_ctx,
                role="implementer",
            )
        round_record: Dict[str, Any] = {
            "round": round_idx,
            "implementer_run": implementer_meta,
            "tool_events": _collect_tool_events(implementer_ctx),
        }
        if impl_error:
            artifacts["error"] = f"Implementer failed: {impl_error}"
            artifacts["rounds"].append(round_record)
            report.write_artifacts(artifacts)
            print(f"Round {round_idx}: FAIL SKIP")
            print("Verdict: FAIL")
            print("Action: SKIP")
            return 1
        report.append_implementer(round_idx, implementer_report)

        before_checks, before_meta = _compute_workspace_diff(workspace, base_sha)
        if checkpoint is not None and round_idx == start_round and digest_text(before_checks) != checkpoint.patch_sha256:
            parser.error("workspace changes differ from the checkpoint")
        if not before_meta["available"]:
            artifacts["error"] = before_meta.get("reason", "Could not collect the workspace patch before checks.")
            artifacts["rounds"].append(round_record)
            report.write_artifacts(artifacts)
            print(f"Round {round_idx}: FAIL SKIP ({artifacts['error']})")
            return 1
        report.write_checkpoint(ReviewCheckpoint(
            workspace=workspace, base_sha=base_sha, patch_sha256=digest_text(before_checks),
            inputs_sha256=inputs_sha256, round_number=round_idx,
            plan=plan_text, implementer_report=implementer_report,
        ))
        check_env = {key: value for key, value in os.environ.items() if key != "OPENAI_API_KEY"}
        check_results = [
            _run_local_cmd(command, cwd=workspace, timeout_seconds=args.check_timeout, env=check_env)
            for command in checks
        ]
        round_record["checks"] = [_cmd_meta(check, limit=4000) for check in check_results]
        tool_outputs = _format_tool_outputs([
            *implementer_ctx.tool_events, *({"tool": "run_cmd", **check} for check in round_record["checks"]),
        ])

        red_flags_text, red_flags_meta = _scan_red_flags(workspace)
        red_flags_max_chars = limits["ORCH_REVIEWER_RED_FLAGS_MAX_CHARS"]
        reviewer_red_flags = _truncate(red_flags_text, red_flags_max_chars) if red_flags_text.strip() != "- None" else "- None"

        diff_text, diff_meta = _compute_workspace_diff(workspace, base_sha)
        diff_max_chars = limits["ORCH_REVIEWER_DIFF_MAX_CHARS"]
        reviewer_diff = diff_text
        diff_path = run_dir / f"diff_round_{round_idx}.patch"
        diff_path.write_text(diff_text, encoding="utf-8")
        round_record["diff"] = {
            "path": str(diff_path),
            "sha256": digest_text(diff_text),
            "max_chars": diff_max_chars,
            "included": reviewer_diff.strip() != "- None",
            "meta": diff_meta,
        }
        if not diff_meta["available"] or len(diff_text) > diff_max_chars or diff_text != before_checks:
            round_record["error"] = diff_meta.get("reason", "Review requires a complete patch that stays unchanged during checks.")
            artifacts["rounds"].append(round_record)
            report.write_artifacts(artifacts)
            print(f"Round {round_idx}: FAIL SKIP ({round_record['error']})")
            return 1
        round_record["red_flags"] = {
            "max_chars": red_flags_max_chars,
            "included": reviewer_red_flags.strip() != "- None",
            "meta": red_flags_meta,
        }

        reviewer_input = _build_reviewer_input(
            architecture,
            conventions,
            task_text,
            plan_text,
            reviewer_red_flags,
            reviewer_diff,
            tool_outputs,
            implementer_report,
        )
        reviewer_report, review_error, reviewer_meta = _safe_run(
            reviewer,
            reviewer_input,
            artifacts["max_turns"]["reviewer"],
            role="reviewer",
        )
        round_record["reviewer_run"] = reviewer_meta
        if review_error:
            artifacts["error"] = f"Reviewer failed: {review_error}"
            artifacts["rounds"].append(round_record)
            report.write_artifacts(artifacts)
            print(f"Round {round_idx}: FAIL SKIP")
            print("Verdict: FAIL")
            print("Action: SKIP")
            return 1
        report.append_reviewer(round_idx, reviewer_report)

        review_decision = parse_reviewer_output(reviewer_report)
        if review_decision.verdict == "PASS" and any(check["returncode"] != 0 for check in check_results):
            review_decision = ReviewDecision(
                "FAIL", "CONTINUE",
                ["Fix the required checks before approving the patch."],
                reviewer_report,
            )
        round_record.update({
            "implementer_report": str(report.implementer_path),
            "reviewer_report": str(report.reviewer_path),
            "review_decision": {
                "verdict": review_decision.verdict,
                "action": review_decision.action,
                "fixes": review_decision.fixes,
            },
        })
        artifacts["rounds"].append(round_record)
        report.write_artifacts(artifacts)

        review_fingerprint = f"{round_record['diff']['sha256']}\n{reviewer_report}"
        if is_stuck(previous_reviewer_text, review_fingerprint):
            review_decision = ReviewDecision(
                verdict="FAIL",
                action="SKIP",
                fixes=review_decision.fixes,
                raw=reviewer_report,
            )
            print(f"Round {round_idx}: FAIL SKIP")
            artifacts["stuck"] = True
            artifacts["final_verdict"] = "FAIL"
            artifacts["final_action"] = "SKIP"
            loop_exhausted = False
            break

        previous_reviewer_text = review_fingerprint

        if review_decision.verdict == "PASS":
            current_patch, current_meta = _compute_workspace_diff(workspace, base_sha)
            if not current_meta["available"] or current_patch != diff_text:
                artifacts["error"] = "Workspace changed during review; rerun the checks and review."
                report.write_artifacts(artifacts)
                print(f"Round {round_idx}: FAIL SKIP ({artifacts['error']})")
                return 1
            print(f"Round {round_idx}: PASS")
            artifacts["final_verdict"] = "PASS"
            artifacts["final_action"] = "PASS"
            loop_exhausted = False
            break

        if review_decision.action == "SKIP":
            print(f"Round {round_idx}: FAIL SKIP")
            artifacts["final_verdict"] = "FAIL"
            artifacts["final_action"] = "SKIP"
            loop_exhausted = False
            break

        print(f"Round {round_idx}: FAIL CONTINUE")

    if review_decision and loop_exhausted and review_decision.action == "CONTINUE":
        artifacts["final_verdict"] = "FAIL"
        artifacts["final_action"] = "SKIP"
        artifacts["reason"] = "max_rounds"

    docs_update = review_decision is not None and _needs_docs_update(review_decision)
    if write_project_notes and review_decision and (review_decision.verdict == "PASS" or docs_update):
        tech_writer = build_tech_writer_agent()
        tech_ctx = RunContext(
            role="tech_writer",
            repo_root=repo_root,
            fs_base=repo_root / "project",
            allow_write=True,
            shell_cwd=workspace,
        )
        tech_input = _build_tech_writer_input(vision, architecture, conventions, task_text, plan_text, review_decision)
        tech_report, tech_error, tech_meta = _safe_run(
            tech_writer,
            tech_input,
            artifacts["max_turns"]["tech_writer"],
            context=tech_ctx,
            role="tech_writer",
        )
        artifacts["tech_writer_run"] = tech_meta
        if tech_error:
            artifacts["error"] = f"Tech writer failed: {tech_error}"
            artifacts["review_verdict"] = artifacts["final_verdict"]
            artifacts["final_verdict"] = "FAIL"
            artifacts["final_action"] = "SKIP"
            artifacts["ended_at"] = datetime.now().isoformat(timespec="seconds")
            report.write_artifacts(artifacts)
            print("Verdict: FAIL")
            print("Action: SKIP")
            return 1
        report.write_tech_writer(tech_report)
        artifacts["docs_updated"] = True

    artifacts["ended_at"] = datetime.now().isoformat(timespec="seconds")
    report.write_artifacts(artifacts)

    final_verdict = artifacts.get("final_verdict", "FAIL")
    final_action = artifacts.get("final_action", "SKIP")
    print(f"Verdict: {final_verdict}")
    if final_verdict == "PASS":
        print("Action: PASS")
    else:
        print(f"Action: {final_action}")

    return 0 if final_verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
