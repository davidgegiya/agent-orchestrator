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
