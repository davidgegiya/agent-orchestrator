"""Verify task provenance against the pinned SWE-bench data."""

from hashlib import sha256
from pathlib import Path

from .catalog import Scenario

DATASET_REVISION = "c6fe717fd7a4c3ac1daa4055a4fd082c6a1d28a2"
DATASET_URL = (
    f"https://huggingface.co/datasets/SWE-bench/SWE-bench/resolve/{DATASET_REVISION}"
    "/data/test-00000-of-00001.parquet"
)
COLUMNS = ["instance_id", "repo", "base_commit", "problem_statement", "FAIL_TO_PASS", "PASS_TO_PASS"]


def problem_digest(problem: str) -> str:
    return sha256(problem.encode("utf-8")).hexdigest()


def verify_source_row(case: Scenario, row: dict) -> None:
    source_fields = (row["instance_id"], row["repo"], row["base_commit"])
    if source_fields != (case.id, case.repository, case.base_commit):
        raise ValueError(f"source identity mismatch for {case.id}")
    if problem_digest(row["problem_statement"]) != case.problem_sha256:
        raise ValueError(f"problem statement does not match the pinned revision for {case.id}")
    if tuple(dict.fromkeys(row["FAIL_TO_PASS"])) != case.regression_checks:
        raise ValueError(f"regression checks differ from the pinned revision for {case.id}")
    if len(set(row["PASS_TO_PASS"])) != case.passing_checks:
        raise ValueError(f"baseline check count differs from the pinned revision for {case.id}")


def read_source_rows(source: Path, cases: list[Scenario]) -> dict[str, str]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise ValueError("install the benchmark extra to read the SWE-bench Parquet file") from exc

    table = parquet.read_table(source, columns=COLUMNS)
    for case in cases:
        if case.dataset_row >= table.num_rows:
            raise ValueError(f"dataset row {case.dataset_row} does not exist")
    rows = table.take([case.dataset_row for case in cases]).to_pylist()
    problems = {}
    for case, row in zip(cases, rows):
        verify_source_row(case, row)
        problems[case.id] = row["problem_statement"]
    return problems
