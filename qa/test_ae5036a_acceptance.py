"""Independent acceptance regressions for ae5036a. No real capture/clicks."""
import time
from types import SimpleNamespace
from unittest.mock import patch

from bot.action.base import ActionDispatchResult, ActionDispatchStatus
from bot.action.cdp_backend import CDPActionBackend
from bot.core.coordinates import Rect, ViewportContext
from bot.telemetry.logger import AsyncTelemetryLogger
from bot.workflow.runner import BotRuntimeRunner
from bot.workflow.state_machine import RegionState
from qa.test_independent_regressions import run_scenario


def make_logger(tmp_path, maxsize=4):
    # Deterministically simulate a stalled writer, no background thread/file output.
    with patch("threading.Thread.start"):
        return AsyncTelemetryLogger(str(tmp_path / "qa.jsonl"), maxsize=maxsize)


def run_with_telemetry(telemetry):
    original = BotRuntimeRunner.__init__
    def init(runner, *args, **kwargs):
        original(runner, *args, **kwargs)
        runner.telemetry = telemetry
    with patch.object(BotRuntimeRunner, "__init__", init):
        return run_scenario()


def test_real_logger_integrates_with_runner_without_crash(tmp_path):
    _, _, attempts = run_with_telemetry(make_logger(tmp_path, 100))
    assert len(attempts) == 1


def test_full_telemetry_enters_safe_pause_without_crash():
    telemetry = SimpleNamespace(log_event=lambda *a: None, reserve_critical_slots=lambda **kw: None)
    inst, _, attempts = run_with_telemetry(telemetry)
    assert not attempts
    assert inst.state.value == "SAFE_PAUSE"


def test_reserved_evidence_cannot_be_stolen_by_ordinary_producer(tmp_path):
    log = make_logger(tmp_path, 2)
    token = log.reserve_critical_slots(2)
    assert token is not None
    log.log_event("HEARTBEAT", {})
    log.log_event("HEARTBEAT", {})
    assert log.consume(token, "ACTION_INTENT", {}), "Reserved slot was stolen"
    assert log.consume(token, "ACTION_OUTCOME", {}), "Reserved outcome slot was stolen"


def test_uncertain_result_never_retries_click():
    result = ActionDispatchResult(ActionDispatchStatus.UNCERTAIN, "ACK lost after send")
    inst, _, attempts = run_scenario(dispatch_ok=result)
    assert len(attempts) == 1, f"Uncertain action retried: {len(attempts)} attempts"
    assert inst.state == RegionState.UNCERTAIN_HOLD


def test_valid_viewport_context_does_not_crash_backend():
    backend = CDPActionBackend()
    backend.ws_url = "ws://qa.invalid/mock"
    context = ViewportContext(window_rect=Rect(0, 0, 100, 100), client_rect=Rect(0, 0, 100, 100))
    with patch("bot.action.cdp_backend.websockets.connect", side_effect=RuntimeError("QA simulated offline")):
        assert not backend.dispatch_click(10, 10, {"viewport_context": context})


def test_deadline_expiring_during_fresh_verify_blocks_dispatch():
    original = BotRuntimeRunner.__init__
    def init(runner, *args, **kwargs):
        original(runner, *args, **kwargs)
        fresh = runner.capture_manager.grab_sub_roi
        def expire(*a):
            runner.ledger.get_instance("r").step_deadline = time.time() - 1
            return fresh(*a)
        runner.capture_manager.grab_sub_roi = expire
    with patch.object(BotRuntimeRunner, "__init__", init):
        _, _, attempts = run_scenario()
    assert not attempts, "Action dispatched after step deadline"


def test_generation_change_during_fresh_verify_blocks_stale_dispatch():
    original = BotRuntimeRunner.__init__
    def init(runner, *args, **kwargs):
        original(runner, *args, **kwargs)
        fresh = runner.capture_manager.grab_sub_roi
        def restart(*a):
            runner.ledger.get_instance("r").start_workflow()
            return fresh(*a)
        runner.capture_manager.grab_sub_roi = restart
    with patch.object(BotRuntimeRunner, "__init__", init):
        _, _, attempts = run_scenario()
    assert not attempts, "Old decision dispatched into new generation"
