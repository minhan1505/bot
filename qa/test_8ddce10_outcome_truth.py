"""Evidence failure must not rewrite actual dispatch status. No real actions."""
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from bot.action.base import ActionDispatchResult, ActionDispatchStatus
from bot.workflow.runner import BotRuntimeRunner
from qa.test_independent_regressions import run_scenario


@pytest.mark.parametrize("status", [ActionDispatchStatus.NOT_SENT,
    ActionDispatchStatus.FAIL_CLOSED, ActionDispatchStatus.UNCERTAIN])
def test_failed_outcome_does_not_record_unconfirmed_action_as_dispatched(status):
    telemetry = SimpleNamespace(log_event=lambda *a: None,
        reserve_critical_slots=lambda **kw: object(),
        consume=lambda token, event, data: event != "ACTION_OUTCOME",
        release=lambda *a: None)
    original = BotRuntimeRunner.__init__
    def init(runner, *args, **kwargs):
        original(runner, *args, **kwargs)
        runner.telemetry = telemetry
    with patch.object(BotRuntimeRunner, "__init__", init):
        inst, _, attempts = run_scenario(dispatch_ok=ActionDispatchResult(status, "QA injected result"))
    assert len(attempts) == 1
    assert not [e for e in inst.history if e["to"] == "ACTION_PENDING" and e["reason"] == "Action dispatched"], (
        f"Backend returned {status.value}, but state history falsely records Action dispatched")
    assert inst.last_action_at == 0
