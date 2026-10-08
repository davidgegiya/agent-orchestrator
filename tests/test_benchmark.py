from hashlib import sha256
import json

import pytest

from orchestrator.benchmark.catalog import Scenario, load_catalog
from orchestrator.benchmark.results import Outcome, compare_outcomes, record_outcome
from orchestrator.benchmark.source import verify_source_row


@pytest.fixture
def case():
    return Scenario(
        id="owner__repo-123", repository="owner/repo", base_commit="a" * 40,
        topic="Handle malformed input", upstream_created_at="2020-07-11T00:00:00Z",
        dataset_row=0, problem_sha256=sha256(b"Fix it.").hexdigest(),
        regression_checks=["test_bad_input"], passing_checks=1,
    )


def test_source_verification_rejects_modified_task(case):
    source = {
        "instance_id": case.id, "repo": case.repository, "base_commit": case.base_commit,
        "created_at": case.upstream_created_at.isoformat(),
        "problem_statement": "Fix it.", "FAIL_TO_PASS": ["test_bad_input"], "PASS_TO_PASS": ["test_good_input"],
    }
    verify_source_row(case, source)
    source["problem_statement"] = "Another task."
    with pytest.raises(ValueError, match="problem statement"):
        verify_source_row(case, source)


def test_catalog_rejects_a_scenario_filed_under_the_wrong_id(tmp_path, case):
    folder = tmp_path / "owner__repo"
    folder.mkdir()
    (folder / "owner__repo-456.json").write_text(case.model_dump_json())
    with pytest.raises(ValueError, match="path does not match"):
        load_catalog(tmp_path)


def test_run_result_requires_a_checked_patch(case, tmp_path):
    (tmp_path / "artifacts.json").write_text(json.dumps({
        "base_sha": case.base_commit, "task_sha256": case.problem_sha256,
        "ended_at": "complete", "final_verdict": "PASS",
        "required_checks": [["python", "-m", "pytest"]],
        "rounds": [{"round": 1, "checks": [{"returncode": 0}]}],
    }))
    with pytest.raises(ValueError, match="verified patch"):
        record_outcome(case, "review-on", tmp_path)


def test_comparison_excludes_unpaired_cases(case):
    base = {"base_commit": case.base_commit, "checks_sha256": "a" * 64, "rounds": 1, "checks": 1}
    values = [
        Outcome(case_id=case.id, variant="a", result="rejected", **base),
        Outcome(case_id=case.id, variant="b", result="accepted", **base),
        Outcome(case_id="owner__repo-234", variant="b", result="accepted", **base),
        Outcome(case_id=case.id, variant="b", result="accepted", **{**base, "checks_sha256": "b" * 64}),
    ]
    summary = compare_outcomes(values, "a", "b")
    assert summary["paired_cases"] == 1 and summary["right_only"] == 2
    assert summary["transitions"] == {"rejected -> accepted": 1}
