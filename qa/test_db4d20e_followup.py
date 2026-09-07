"""QA regressions: lost press ACK and missing action evidence; no real I/O."""
import json
from types import SimpleNamespace
from unittest.mock import patch
from bot.action.base import ActionDispatchStatus
from bot.action.cdp_backend import CDPActionBackend
from bot.workflow.runner import BotRuntimeRunner
from qa.test_independent_regressions import run_scenario


def test_press_sent_but_ack_lost_is_uncertain():
    class Socket:
        def __init__(self):
            self.sent = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def send(self, value):
            self.sent.append(json.loads(value))
        async def recv(self):
            raise TimeoutError("QA: ACK lost after successful send")
    socket = Socket()
    backend = CDPActionBackend()
    backend.ws_url = "ws://qa.invalid/simulated"
    with patch("bot.action.cdp_backend.websockets.connect", return_value=socket):
        result = backend.dispatch_click(10, 10, {})
    assert socket.sent[0]["params"]["type"] == "mousePressed"
    assert result.status == ActionDispatchStatus.UNCERTAIN, result


def test_outcome_evidence_failure_blocks_new_actions():
    consumed = []
    def consume(token, event, data):
        consumed.append(event)
        return event != "ACTION_OUTCOME"
    telemetry = SimpleNamespace(log_event=lambda *a: None,
        reserve_critical_slots=lambda **kw: object(), consume=consume, release=lambda *a: None)
    original = BotRuntimeRunner.__init__
    def init(runner, *args, **kwargs):
        original(runner, *args, **kwargs)
        runner.telemetry = telemetry
    with patch.object(BotRuntimeRunner, "__init__", init):
        inst, _, attempts = run_scenario(steps=2, cooldown_ms=0)
    assert "ACTION_OUTCOME" in consumed
    assert len(attempts) == 1, f"Continued dispatch after evidence failure: {len(attempts)} actions, {inst.state}"
