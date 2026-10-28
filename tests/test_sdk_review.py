from agents import RunConfig, Runner
from agents.testing import ScriptedModel, assistant_message

from orchestrator.agents import build_reviewer_agent
from orchestrator.policies import ReviewerOutput, parse_reviewer_output


def test_sdk_runner_returns_a_typed_review_without_provider_requests():
    model = ScriptedModel([{
        "output": [assistant_message('{"verdict":"PASS","action":"CONTINUE","fixes":[]}')],
    }])
    reviewer = build_reviewer_agent().clone(model=model)
    result = Runner.run_sync(reviewer, "A reviewed patch and passing checks.",
                             run_config=RunConfig(tracing_disabled=True))
    model.assert_complete()
    assert isinstance(result.final_output, ReviewerOutput)
    decision = parse_reviewer_output(result.final_output.model_dump_json())
    assert (decision.verdict, decision.action, decision.fixes) == ("PASS", "CONTINUE", [])
