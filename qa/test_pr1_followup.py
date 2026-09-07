"""Independent counterexamples for PR #1 at 522790b; no real clicks."""
from unittest.mock import patch

from bot.core.models import RegionModel, Workflow, WorkflowStep
from bot.workflow.ledger import SessionLedger
from qa.test_independent_regressions import run_scenario


def make_ledger():
    ledger = SessionLedger()
    workflow = Workflow(workflow_id="w", name="W", steps=[WorkflowStep(step_index=0, target_id="t")])
    for identity, x in [("A", 0), ("B", 50)]:
        ledger.register_region(RegionModel(region_id=identity, name=identity,
            x=x, y=0, w=100, h=100), workflow).start_workflow()
    return ledger


def test_explicit_region_mismatch_must_reject_instead_of_fallback():
    ledger = make_ledger()
    assert ledger.associate_candidate(25, 50, "t", candidate_region_id="B") is None


def test_completed_region_must_not_transfer_detection_to_neighbor():
    ledger = make_ledger()
    ledger.advance_region("B")
    assert ledger.associate_candidate(75, 50, "t", candidate_region_id="B") is None


def test_unknown_region_identity_must_reject():
    ledger = make_ledger()
    assert ledger.associate_candidate(75, 50, "t", candidate_region_id="deleted") is None


def test_ownership_does_not_depend_on_callers_local_variable_names():
    ledger = make_ledger()
    def through_wrapper():
        return ledger.associate_candidate(75, 50, "t")
    candidate_region_id = "B"
    direct = ledger.associate_candidate(75, 50, "t")
    wrapped = through_wrapper()
    assert direct == wrapped, (direct, wrapped)


def test_zero_retry_limit_is_respected_on_dispatch_failure():
    def step_without_retries(*args, **kwargs):
        kwargs["retry_limit"] = 0
        return WorkflowStep(*args, **kwargs)
    with patch("qa.test_independent_regressions.WorkflowStep", side_effect=step_without_retries):
        _, _, attempts = run_scenario(dispatch_ok=False)
    assert len(attempts) == 1, f"retry_limit=0 but dispatched {len(attempts)} attempts"
