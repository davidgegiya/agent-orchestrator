"""Load benchmark scenarios from a versioned local catalog."""

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-[1-9][0-9]*$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    base_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    topic: str = Field(min_length=1, max_length=140)
    upstream_created_at: datetime
    dataset_row: int = Field(ge=0)
    regression_checks: tuple[str, ...] = Field(min_length=1)
    passing_checks: int = Field(ge=0)

    @model_validator(mode="after")
    def verify_source(self):
        prefix, number = self.id.rsplit("-", 1)
        if prefix != self.repository.replace("/", "__") or any(
            part in {".", ".."} for part in self.repository.split("/")
        ):
            raise ValueError("scenario ID must match its repository")
        if len(set(self.regression_checks)) != len(self.regression_checks):
            raise ValueError("regression checks must be unique")
        if any(not check.strip() for check in self.regression_checks):
            raise ValueError("regression checks must be nonempty")
        if self.upstream_created_at.tzinfo is None:
            raise ValueError("the upstream timestamp must include its time zone")
        return self

    @property
    def pull_request_url(self) -> str:
        return f"https://github.com/{self.repository}/pull/{self.id.rsplit('-', 1)[1]}"

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.repository}.git"


def load_catalog(root: Path) -> dict[str, Scenario]:
    cases = {}
    rows = set()
    for path in sorted(root.glob("*/*.json")):
        try:
            case = Scenario.model_validate_json(path.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid scenario {path}: {exc}") from exc
        owner_and_repo = case.repository.replace("/", "__")
        if path.stem != case.id or path.parent.name != owner_and_repo:
            raise ValueError(f"scenario path does not match its ID: {path}")
        if case.id in cases or case.dataset_row in rows:
            raise ValueError(f"duplicate scenario or dataset row: {case.id}")
        cases[case.id] = case
        rows.add(case.dataset_row)
    if not cases:
        raise ValueError(f"no scenarios found in {root}")
    return cases
