import pytest

from orchestrator.policies import parse_reviewer_output


@pytest.mark.parametrize(("response", "action", "fix"), [
    ('{"verdict":"FAIL","action":"SKIP","fixes":["Start the local database."]}',
     "SKIP", "Start the local database."),
    ('{"verdict":"PASS","action":"SKIP","fixes":[]}',
     "CONTINUE", "Reviewer returned an inconsistent PASS."),
    ('{"verdict":"PASS","action":"CONTINUE","fixes":["Change the code."]}',
     "CONTINUE", "Reviewer returned an inconsistent PASS."),
    ('{"verdict":"FAIL","action":"SKIP","fixes":[]}',
     "CONTINUE", "Reviewer must provide an actionable correction."),
    ('{"verdict":"PASS","action":"CONTINUE","fixes":[],"approved":true}',
     "CONTINUE", "Reviewer returned an invalid decision."),
    ('{"verdict":"PASS","action":',
     "CONTINUE", "Reviewer returned an invalid decision."),
])
def test_review_policy_blocks_inconsistent_or_incomplete_decisions(response, action, fix):
    decision = parse_reviewer_output(response)
    assert (decision.verdict, decision.action, decision.fixes) == ("FAIL", action, [fix])
