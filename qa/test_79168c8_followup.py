"""Counterexamples for 79168c8; all I/O and browser actions are simulated."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from bot.action.base import ActionDispatchResult, ActionDispatchStatus
from bot.action.cdp_backend import CDPActionBackend
from bot.core.models import Workflow, WorkflowStep
from bot.workflow.state_machine import RegionInstance, RegionState
from qa.test_ae5036a_acceptance import make_logger, run_with_telemetry


def test_uncertain_hold_still_obeys_step_deadline():
    wf = Workflow(workflow_id="w", name="W", steps=[WorkflowStep(step_index=0, target_id="t")])
    inst = RegionInstance("r", wf)
    inst.start_workflow()
    inst.transition_to(RegionState.UNCERTAIN_HOLD)
    assert inst.check_timeout(inst.step_deadline + 60)
    assert inst.state == RegionState.TIMEOUT


def test_failed_intent_enqueue_blocks_action():
    telemetry = SimpleNamespace(log_event=lambda *a: None,
        reserve_critical_slots=lambda **kw: object(), consume=lambda *a: False)
    _, _, attempts = run_with_telemetry(telemetry)
    assert not attempts, "Action sent even though critical ACTION_INTENT was rejected"


def test_reservation_between_normal_check_and_enqueue_cannot_be_stolen(tmp_path):
    log = make_logger(tmp_path, 2)
    original_put = log._queue.put_nowait
    tokens = []
    # Deterministic interleaving: normal producer has passed its capacity check,
    # then another producer reserves all capacity before normal enqueue executes.
    def interleaved_put(entry):
        if log._lock.locked():
            # A corrected implementation keeps check+enqueue in one critical section.
            original_put(entry)
            return
        tokens.append(log.reserve_critical_slots(2))
        original_put(entry)
    with patch.object(log._queue, "put_nowait", side_effect=interleaved_put):
        log.log_event("HEARTBEAT", {})
    if not tokens:
        assert log.reserve_critical_slots(2) is None
        return
    token = tokens[0]
    assert token is not None
    assert log.consume(token, "ACTION_INTENT", {})
    assert log.consume(token, "ACTION_OUTCOME", {}), "Normal producer stole reserved outcome capacity"


def test_cdp_partial_send_returns_uncertain_not_retryable_false():
    class FakeSocket:
        def __init__(self):
            self.messages = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def send(self, value):
            self.messages.append(json.loads(value))
        async def recv(self):
            message = self.messages[-1]
            if message["params"]["type"] == "mousePressed":
                return json.dumps({"id": message["id"], "result": {}})
            return json.dumps({"id": message["id"], "error": {"code": -32000, "message": "QA up failed"}})
    backend = CDPActionBackend()
    backend.ws_url = "ws://qa.invalid/simulated"
    with patch("bot.action.cdp_backend.websockets.connect", return_value=FakeSocket()):
        result = backend.dispatch_click(10, 10, {})
    assert isinstance(result, ActionDispatchResult), f"Lost partial-send status: {result!r}"
    assert result.status == ActionDispatchStatus.UNCERTAIN
