"""Evidence needed to restart verification without repeating file changes."""

from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ReviewCheckpoint(BaseModel):
    version: Literal[1] = 1
    workspace: Path
    base_sha: str
    patch_sha256: str
    inputs_sha256: str
    round_number: int = Field(ge=1)
    plan: str
    implementer_report: str


def run_inputs_digest(planner_input: str, checks: list[list[str]], backend: str) -> str:
    data = json.dumps([planner_input, checks, backend], ensure_ascii=False)
    return digest_text(data)


def digest_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()
