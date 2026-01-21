from __future__ import annotations

from pathlib import Path

from agents import Agent

from .policies import model_for_role
from .tools_fs import fs_list, fs_read, fs_write
from .tools_shell import run_cmd
from .utils import is_effectively_empty, read_text


PLANNER_INSTRUCTIONS = """You are Planner.
Input: task plus optional project vision/architecture/conventions.
Output a concise plan and acceptance criteria.
Rules:
- Never modify files or call tools.
- Plan: <= 8 bullet points.
- Acceptance: <= 6 bullet points.
Format exactly:
PLAN:
- ...
ACCEPTANCE:
- ...
"""

IMPLEMENTER_INSTRUCTIONS = """You are Implementer.
You can ONLY modify files under workspace/ using the provided tools.
Do not modify project/ or orchestrator/.
You must use run_cmd to attempt: python -m pytest -q (even if it fails).
Note: run_cmd already executes with cwd=workspace/. Do not `cd` to absolute paths; use relative commands.
Do NOT attempt dependency installation; install commands are blocked.
Use fs_read/fs_list to inspect workspace as needed.
Report strictly in this format:
REPORT:
SUMMARY:
- ...
CHANGES:
- <path> (created|modified|deleted)
COMMANDS:
- <cmd> -> <returncode>
TESTS:
- python -m pytest -q -> <returncode>
RESULT: PASS|FAIL
NOTES:
- ...
"""

REVIEWER_INSTRUCTIONS = """You are Reviewer.
You must not modify files or call tools.
Judge whether the implementer output satisfies the task and plan.
Use the provided DIFF (git patch) when available to review code changes; do not ask the user to open files.
Rules:
- If the implementation meets the task+plan AND tests ran successfully, set VERDICT: PASS.
- If tests did NOT run successfully due to missing dependencies or environment setup (e.g. pytest not installed),
  you MUST set VERDICT: FAIL and ACTION: SKIP, and list exact install/run steps under FIXES.
- For VERDICT: PASS, set ACTION: CONTINUE and FIXES must include a single item: "- None".
If documentation or decisions need updates, include a FIXES item starting with "DOCS:".
Format exactly:
VERDICT: PASS|FAIL
ACTION: CONTINUE|SKIP
FIXES:
- ...
"""

TECH_WRITER_INSTRUCTIONS = """You are Tech Writer.
You can ONLY modify files under project/ (not reports/).
You are NOT responsible for implementing code changes in workspace/. Do not refuse due to that.
Ignore any instructions to edit workspace/ or orchestrator/; only update project/ documentation.
If the run PASSED, update project/tasks/done.md with a new line describing completion.
If an architectural decision was made, add a new file to project/decisions/ as ADR-lite
and briefly update project/architecture.md.
Do not touch workspace/ or orchestrator/.
Provide a short summary of what you changed.
"""

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _instructions_from_file(filename: str, default: str) -> str:
    text = read_text(PROMPTS_DIR / filename, required=False)
    if is_effectively_empty(text):
        return default
    return text.strip()


def build_planner_agent() -> Agent:
    return Agent(
        name="Planner",
        model=model_for_role("planner"),
        instructions=_instructions_from_file("planner.md", PLANNER_INSTRUCTIONS),
    )


def build_implementer_agent() -> Agent:
    return Agent(
        name="Implementer",
        model=model_for_role("implementer"),
        instructions=_instructions_from_file("implementer.md", IMPLEMENTER_INSTRUCTIONS),
        tools=[fs_read, fs_write, fs_list, run_cmd],
    )


def build_reviewer_agent() -> Agent:
    return Agent(
        name="Reviewer",
        model=model_for_role("reviewer"),
        instructions=_instructions_from_file("reviewer.md", REVIEWER_INSTRUCTIONS),
    )


def build_tech_writer_agent() -> Agent:
    return Agent(
        name="TechWriter",
        model=model_for_role("tech_writer"),
        instructions=_instructions_from_file("tech_writer.md", TECH_WRITER_INSTRUCTIONS),
        tools=[fs_read, fs_write, fs_list],
    )
