"""
tests/test_action_and_safety.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Unit tests for ActionManager, Anti-Runaway guard, emergency stop, and Fail-Closed behavior.
"""

import pytest
import time
from bot.action.base import BaseActionBackend
from bot.action.manager import ActionManager


class MockFailingBackend(BaseActionBackend):
    def probe_capability(self, context):
        return False, "PROBE_FAILED: Target surface not responsive."

    def dispatch_click(self, screen_x, screen_y, context):
        return False

    def close(self):
        pass


class MockWorkingBackend(BaseActionBackend):
    def __init__(self):
        self.dispatched_clicks = []

    def probe_capability(self, context):
        return True, "MOCK_SUPPORTED"

    def dispatch_click(self, screen_x, screen_y, context):
        self.dispatched_clicks.append((screen_x, screen_y))
        return True

    def close(self):
        pass


def test_action_manager_fail_closed_on_unsupported_backend():
    manager = ActionManager()
    backend = MockFailingBackend()

    supported, reason = manager.probe_and_bind(backend, {})
    assert not supported
    assert "PROBE_FAILED" in reason
    assert not manager.is_supported

    # Attempt to dispatch click must fail immediately without touching mouse
    res = manager.dispatch_action(100, 200, {})
    assert not res
    assert manager.total_clicks == 0


def test_action_manager_emergency_stop_halts_dispatch():
    manager = ActionManager()
    backend = MockWorkingBackend()
    manager.probe_and_bind(backend, {})
    assert manager.is_supported

    # Trigger emergency stop (e.g. F12 hotkey)
    manager.trigger_emergency_stop()
    assert manager.emergency_stop_triggered

    # Clicks must be rejected
    res = manager.dispatch_action(100, 200, {})
    assert not res
    assert len(backend.dispatched_clicks) == 0

    # Reset
    manager.reset_emergency_stop()
    res = manager.dispatch_action(100, 200, {})
    assert res
    assert len(backend.dispatched_clicks) == 1


def test_action_manager_anti_runaway_rate_limit():
    # Max 3 clicks per second
    manager = ActionManager(max_clicks_per_second=3.0, circuit_breaker_threshold=5)
    backend = MockWorkingBackend()
    manager.probe_and_bind(backend, {})

    # Dispatch 3 clicks -> Success
    for _ in range(3):
        assert manager.dispatch_action(100, 100, {})

    # 4th click within 1 second -> Must be dropped by Anti-Runaway
    assert not manager.dispatch_action(100, 100, {})
    assert len(backend.dispatched_clicks) == 3


def test_action_manager_anti_runaway_circuit_breaker():
    manager = ActionManager(max_clicks_per_second=10.0, circuit_breaker_threshold=4)
    backend = MockWorkingBackend()
    manager.probe_and_bind(backend, {})

    # Simulate 4 rapid clicks reaching circuit breaker threshold
    for _ in range(4):
        manager.dispatch_action(100, 100, {})

    # Circuit breaker must trip
    assert manager._circuit_breaker_tripped

    # Any subsequent click must be blocked
    assert not manager.dispatch_action(100, 100, {})
