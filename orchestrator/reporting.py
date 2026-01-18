from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .policies import ARTIFACTS_FILENAME, IMPLEMENTER_FILENAME, PLAN_FILENAME, REVIEWER_FILENAME, TECH_WRITER_FILENAME
from .utils import ensure_dir


@dataclass
class RunReport:
    run_dir: Path

    @property
    def plan_path(self) -> Path:
        return self.run_dir / PLAN_FILENAME

    @property
    def implementer_path(self) -> Path:
        return self.run_dir / IMPLEMENTER_FILENAME

    @property
    def reviewer_path(self) -> Path:
        return self.run_dir / REVIEWER_FILENAME

    @property
    def tech_writer_path(self) -> Path:
        return self.run_dir / TECH_WRITER_FILENAME

    @property
    def artifacts_path(self) -> Path:
        return self.run_dir / ARTIFACTS_FILENAME

    def write_plan(self, content: str) -> None:
        self.plan_path.write_text(content.strip() + "\n", encoding="utf-8")

    def append_implementer(self, round_idx: int, content: str) -> None:
        _append_round(self.implementer_path, round_idx, content)

    def append_reviewer(self, round_idx: int, content: str) -> None:
        _append_round(self.reviewer_path, round_idx, content)

    def write_tech_writer(self, content: str) -> None:
        self.tech_writer_path.write_text(content.strip() + "\n", encoding="utf-8")

    def write_artifacts(self, data: Dict[str, Any]) -> None:
        self.artifacts_path.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def create_run_dir(reports_root: Path) -> Path:
    ensure_dir(reports_root)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = reports_root / f"run-{timestamp}"
    ensure_dir(run_dir)
    return run_dir


def _append_round(path: Path, round_idx: int, content: str) -> None:
    header = f"=== ROUND {round_idx} ===\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(header)
        handle.write(content.strip() + "\n\n")
