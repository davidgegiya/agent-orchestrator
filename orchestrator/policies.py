from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

MAX_ROUNDS = 8

MAX_TURNS = {
    "planner": 6,
    "implementer": 40,
    "reviewer": 10,
    "tech_writer": 10,
}

MODEL_DEFAULT = "gpt-5.1-codex-mini"

DEFAULT_ROLE_MODELS = {
    "planner": MODEL_DEFAULT,
    "implementer": "gpt-5.1-codex-mini",
    "reviewer": MODEL_DEFAULT,
    "tech_writer": MODEL_DEFAULT,
}

ROLE_MODEL_ENVS = {
    "planner": "ORCH_MODEL_PLANNER",
    "implementer": "ORCH_MODEL_IMPLEMENTER",
    "reviewer": "ORCH_MODEL_REVIEWER",
    "tech_writer": "ORCH_MODEL_TECH_WRITER",
}


def model_for_role(role: str) -> str:
    env_var = ROLE_MODEL_ENVS.get(role)
    if env_var:
        value = os.environ.get(env_var)
        if value and value.strip():
            return value.strip()
    return DEFAULT_ROLE_MODELS.get(role, MODEL_DEFAULT)


DEFAULT_RETRY_MAX_ATTEMPTS = {
    "planner": 3,
    "implementer": 3,
    "reviewer": 3,
    "tech_writer": 3,
}

ROLE_RETRY_MAX_ATTEMPTS_ENVS = {
    "planner": "ORCH_RETRY_PLANNER_MAX_ATTEMPTS",
    "implementer": "ORCH_RETRY_IMPLEMENTER_MAX_ATTEMPTS",
    "reviewer": "ORCH_RETRY_REVIEWER_MAX_ATTEMPTS",
    "tech_writer": "ORCH_RETRY_TECH_WRITER_MAX_ATTEMPTS",
}


def retry_max_attempts_for_role(role: str) -> int:
    env_var = ROLE_RETRY_MAX_ATTEMPTS_ENVS.get(role)
    if env_var:
        value = os.environ.get(env_var)
        if value and value.strip().isdigit():
            return max(1, int(value.strip()))
    return int(os.environ.get("ORCH_RETRY_MAX_ATTEMPTS", DEFAULT_RETRY_MAX_ATTEMPTS.get(role, 3)))


def retry_base_delay_seconds() -> float:
    value = os.environ.get("ORCH_RETRY_BASE_DELAY_SECONDS", "1")
    try:
        return max(0.0, float(value))
    except ValueError:
        return 1.0


def retry_max_delay_seconds() -> float:
    value = os.environ.get("ORCH_RETRY_MAX_DELAY_SECONDS", "8")
    try:
        return max(0.0, float(value))
    except ValueError:
        return 8.0

PLAN_FILENAME = "plan.txt"
IMPLEMENTER_FILENAME = "implementer.txt"
REVIEWER_FILENAME = "reviewer.txt"
TECH_WRITER_FILENAME = "tech_writer.txt"
ARTIFACTS_FILENAME = "artifacts.json"


@dataclass
class ReviewDecision:
    verdict: str
    action: str
    fixes: List[str]
    raw: str


def normalize_review(text: str) -> str:
    return "\n".join(line.strip() for line in text.strip().splitlines() if line.strip())


def parse_reviewer_output(text: str) -> ReviewDecision:
    verdict = "FAIL"
    action = "CONTINUE"
    fixes: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.upper().startswith("VERDICT:"):
            verdict = line.split(":", 1)[1].strip().upper()
        elif line.upper().startswith("ACTION:"):
            action = line.split(":", 1)[1].strip().upper()
        elif line.startswith("-"):
            fixes.append(line.lstrip("- ").strip())
    return ReviewDecision(verdict=verdict, action=action, fixes=fixes, raw=text)


def is_stuck(prev_review: str, current_review: str) -> bool:
    if not prev_review:
        return False
    return normalize_review(prev_review) == normalize_review(current_review)
