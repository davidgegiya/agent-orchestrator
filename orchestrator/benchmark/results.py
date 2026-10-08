"""Compare paired workflow outcomes without treating them as model scores."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.inspect_run import inspect_report

from .catalog import Scenario


class Outcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    case_id: str
    base_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    checks_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    variant: str = Field(min_length=1, max_length=100)
    result: Literal["accepted", "rejected", "incomplete"]
    rounds: int = Field(ge=0)
    checks: int = Field(ge=0)


def record_outcome(case: Scenario, variant: str, run_dir: Path) -> Outcome:
    report = inspect_report(run_dir)
    if report.get("base_sha") != case.base_commit:
        raise ValueError("run did not use the scenario's pinned base commit")
    if report.get("task_sha256") != case.problem_sha256:
        raise ValueError("run did not use the pinned scenario problem statement")
    check_commands = report.get("required_checks")
    if not check_commands:
        raise ValueError("run does not identify its required check commands")
    checks_sha256 = sha256(json.dumps(check_commands, ensure_ascii=False).encode("utf-8")).hexdigest()
    rounds = report["rounds"]
    if any(round_info["patch_verified"] is False for round_info in rounds):
        raise ValueError("a saved patch differs from its recorded hash")
    verdict = report.get("final_verdict")
    if report["completed"] and verdict == "PASS":
        final_round = rounds[-1] if rounds else {}
        checks = final_round.get("check_exit_codes", [])
        if not final_round.get("patch_verified") or not checks or any(code != 0 for code in checks):
            raise ValueError("accepted run lacks a verified patch and passing checks")
        result = "accepted"
    elif report["completed"] and verdict == "FAIL":
        result = "rejected"
    else:
        result = "incomplete"
    return Outcome(
        case_id=case.id, base_commit=case.base_commit, checks_sha256=checks_sha256,
        variant=variant, result=result,
        rounds=len(rounds), checks=sum(len(round_info["check_exit_codes"]) for round_info in rounds),
    )


def compare_outcomes(outcomes: list[Outcome], left: str, right: str) -> dict:
    if left == right:
        raise ValueError("comparison requires two distinct variants")
    variants: dict[str, dict[tuple[str, str, str], Outcome]] = {left: {}, right: {}}
    for outcome in outcomes:
        records = variants.get(outcome.variant)
        if records is None:
            continue
        key = (outcome.case_id, outcome.base_commit, outcome.checks_sha256)
        if key in records:
            raise ValueError(f"duplicate result for {outcome.case_id} in {outcome.variant}")
        records[key] = outcome
    left_cases, right_cases = variants[left], variants[right]
    pairs = left_cases.keys() & right_cases.keys()
    if not pairs:
        raise ValueError("no common cases with the same base commit and required checks")
    transitions = Counter((left_cases[key].result, right_cases[key].result) for key in pairs)
    return {
        "left": left, "right": right, "paired_cases": len(pairs),
        "left_only": len(left_cases) - len(pairs), "right_only": len(right_cases) - len(pairs),
        "transitions": {f"{a} -> {b}": n for (a, b), n in sorted(transitions.items())},
    }


def read_outcomes(path: Path) -> list[Outcome]:
    return [Outcome.model_validate(json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
