from __future__ import annotations

import importlib
import os
import subprocess
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for local dev
    def load_dotenv(*_args, **_kwargs) -> bool:
        return False

from .models import RunContext
from .policies import (
    MAX_ROUNDS,
    ReviewDecision,
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
        "TASK:\n"
        f"{task.strip()}\n\n"
        "BACKLOG:\n"
        f"{backlog.strip()}\n\n"
        "VISION:\n"
        f"{vision.strip()}\n\n"
        "ARCHITECTURE:\n"
        f"{architecture.strip()}\n\n"
        "CONVENTIONS:\n"
        f"{conventions.strip()}\n"
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
        "TASK:\n"
        f"{task.strip()}\n\n"
        "PLAN:\n"
        f"{plan.strip()}\n\n"
        "ARCHITECTURE:\n"
        f"{architecture.strip()}\n\n"
        "CONVENTIONS:\n"
        f"{conventions.strip()}\n\n"
        "REVIEW_FIXES:\n"
        f"{fixes_block}\n"
    )


def _build_reviewer_input(task: str, plan: str, tool_outputs: str, diff_text: str, implementer_report: str) -> str:
    return (
        "TASK:\n"
        f"{task.strip()}\n\n"
        "PLAN:\n"
        f"{plan.strip()}\n\n"
        "TOOL_OUTPUTS:\n"
        f"{tool_outputs.strip()}\n\n"
        "DIFF:\n"
        f"{diff_text.strip()}\n\n"
        "IMPLEMENTER_REPORT:\n"
        f"{implementer_report.strip()}\n"
    )


def _build_tech_writer_input(task: str, plan: str, reviewer: ReviewDecision) -> str:
    return (
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


def _run_local_cmd(args: List[str], *, cwd: Path, timeout_seconds: int) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return {
            "cmd": " ".join(args),
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }
    except FileNotFoundError:
        return {
            "cmd": " ".join(args),
            "returncode": 127,
            "stdout": "",
            "stderr": "COMMAND_NOT_FOUND",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "cmd": " ".join(args),
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": f"TIMEOUT after {timeout_seconds}s",
        }


def _cmd_meta(result: Dict[str, Any], *, limit: int = 800) -> Dict[str, Any]:
    def _truncate_stdio(text: str, max_chars: int) -> Tuple[str, bool]:
        if text is None:
            return ("", False)
        if len(text) <= max_chars:
            return (text, False)
        return (text[:max_chars] + "...<truncated>", True)

    stdout_raw = result.get("stdout") or ""
    stderr_raw = result.get("stderr") or ""
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


def _compute_workspace_diff(repo_root: Path) -> Tuple[str, Dict[str, Any]]:
    """
    Produces a patch-like diff for workspace/ using git.

    Includes:
    - tracked changes via `git diff`
    - untracked new files via `git diff --no-index /dev/null <file>`
    """
    meta: Dict[str, Any] = {"available": False, "commands": []}
    workspace_rel = "workspace"

    probe = _run_local_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_root, timeout_seconds=5)
    meta["commands"].append({**_cmd_meta(probe), "tool": "local_cmd"})
    if probe["returncode"] != 0 or "true" not in (probe["stdout"] or "").strip().lower():
        return ("- None", meta)

    meta["available"] = True
    diff_parts: List[str] = []

    status = _run_local_cmd(["git", "status", "--porcelain", "--", workspace_rel], cwd=repo_root, timeout_seconds=10)
    meta["commands"].append({**_cmd_meta(status), "tool": "local_cmd"})
    status_out = (status.get("stdout") or "").rstrip()
    if status_out:
        diff_parts.append("# GIT STATUS (workspace)\n" + status_out)

    tracked = _run_local_cmd(["git", "diff", "--no-color", "--", workspace_rel], cwd=repo_root, timeout_seconds=30)
    meta["commands"].append({**_cmd_meta(tracked), "tool": "local_cmd"})
    tracked_out = (tracked.get("stdout") or "").rstrip()
    if tracked_out:
        diff_parts.append("# GIT DIFF (workspace)\n" + tracked_out)

    untracked = _run_local_cmd(
        ["git", "ls-files", "--others", "--exclude-standard", "--", workspace_rel],
        cwd=repo_root,
        timeout_seconds=10,
    )
    meta["commands"].append({**_cmd_meta(untracked), "tool": "local_cmd"})
    untracked_files = sorted([line.strip() for line in (untracked.get("stdout") or "").splitlines() if line.strip()])

    null_path = "/dev/null" if Path("/dev/null").exists() else "NUL"
    for rel_path in untracked_files:
        patch = _run_local_cmd(
            ["git", "diff", "--no-color", "--no-index", "--", null_path, rel_path],
            cwd=repo_root,
            timeout_seconds=30,
        )
        meta["commands"].append({**_cmd_meta(patch), "tool": "local_cmd"})
        patch_out = (patch.get("stdout") or "").rstrip()
        if patch_out:
            diff_parts.append("# NEW FILE (untracked)\n" + patch_out)

    if not diff_parts:
        return ("- None", meta)

    return ("\n\n".join(diff_parts).rstrip() + "\n", meta)


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
            from agents import Runner

            result = Runner.run_sync(
                agent,
                input=input_text,
                max_turns=max_turns,
                context=context,
            )
            meta = {
                "role": role,
                "attempts": attempt,
                "max_attempts": max_attempts,
                "base_delay_seconds": base_delay,
                "max_delay_seconds": max_delay,
                "errors": errors,
            }
            return (result.final_output or "", None, meta)
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


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    run_dir = create_run_dir(repo_root / "project" / "reports")
    report = RunReport(run_dir)

    print(f"Reports: {run_dir}")

    artifacts: Dict[str, Any] = {
        "run_dir": str(run_dir),
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

    task_path = repo_root / "project" / "tasks" / "current.md"
    task_text = read_text(task_path, required=True)
    task_source = "current.md"
    if is_effectively_empty(task_text):
        task_text = DEMO_TASK
        task_source = "demo"
    artifacts["task_source"] = task_source

    vision = _load_optional(repo_root / "project" / "vision.md")
    architecture = _load_optional(repo_root / "project" / "architecture.md")
    conventions = _load_optional(repo_root / "project" / "conventions.md")
    backlog_raw = _load_optional(repo_root / "project" / "tasks" / "backlog.md")
    backlog_max_chars = int(os.environ.get("ORCH_PLANNER_BACKLOG_MAX_CHARS", "8000"))
    backlog = "" if is_effectively_empty(backlog_raw) else _truncate_for_prompt(backlog_raw, backlog_max_chars, label="BACKLOG")
    artifacts["backlog_included"] = bool(backlog.strip())
    artifacts["backlog_max_chars"] = backlog_max_chars

    planner = build_planner_agent()
    planner_input = _build_planner_input(task_text, backlog, vision, architecture, conventions)
    plan_text, plan_error, plan_meta = _safe_run(
        planner,
        planner_input,
        artifacts["max_turns"]["planner"],
        role="planner",
    )
    artifacts["planner_run"] = plan_meta
    if plan_error:
        artifacts["error"] = f"Planner failed: {plan_error}"
        report.write_artifacts(artifacts)
        print("Verdict: FAIL")
        print("Action: SKIP")
        return 1
    report.write_plan(plan_text)
    artifacts["plan_path"] = str(report.plan_path)

    implementer = build_implementer_agent()
    reviewer = build_reviewer_agent()
    tech_writer = build_tech_writer_agent()

    previous_reviewer_text = ""
    review_decision: Optional[ReviewDecision] = None

    loop_exhausted = True
    for round_idx in range(1, MAX_ROUNDS + 1):
        implementer_ctx = RunContext(
            role="implementer",
            repo_root=repo_root,
            fs_base=repo_root / "workspace",
            allow_write=True,
            shell_cwd=repo_root / "workspace",
        )
        implementer_input = _build_implementer_input(
            task_text,
            plan_text,
            review_decision.fixes if review_decision else None,
            architecture,
            conventions,
        )
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

        tool_outputs = _format_tool_outputs(implementer_ctx.tool_events)

        diff_text, diff_meta = _compute_workspace_diff(repo_root)
        diff_max_chars = int(os.environ.get("ORCH_REVIEWER_DIFF_MAX_CHARS", "12000"))
        reviewer_diff = _truncate(diff_text, diff_max_chars) if diff_text.strip() != "- None" else "- None"
        diff_path = run_dir / f"diff_round_{round_idx}.patch"
        diff_path.write_text(diff_text, encoding="utf-8")
        round_record["diff"] = {
            "path": str(diff_path),
            "max_chars": diff_max_chars,
            "included": reviewer_diff.strip() != "- None",
            "meta": diff_meta,
        }

        reviewer_input = _build_reviewer_input(
            task_text,
            plan_text,
            tool_outputs,
            reviewer_diff,
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

        if is_stuck(previous_reviewer_text, reviewer_report):
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

        previous_reviewer_text = reviewer_report

        if review_decision.verdict == "PASS":
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
    if review_decision and (review_decision.verdict == "PASS" or docs_update):
        tech_ctx = RunContext(
            role="tech_writer",
            repo_root=repo_root,
            fs_base=repo_root / "project",
            allow_write=True,
            shell_cwd=repo_root / "workspace",
        )
        tech_input = _build_tech_writer_input(task_text, plan_text, review_decision)
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
