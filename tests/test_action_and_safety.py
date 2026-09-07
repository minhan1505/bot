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


def test_telemetry_atomic_reservation_and_release(tmp_path):
    from bot.telemetry.logger import AsyncTelemetryLogger
    log_file = tmp_path / "test_telemetry.jsonl"
    logger = AsyncTelemetryLogger(log_filepath=str(log_file), maxsize=5)

    # Buffer maxsize is 5. Reserve 2 slots -> should succeed
    tok1 = logger.reserve_critical_slots(count=2)
    assert tok1 is not None
    assert tok1.slots_reserved == 2
    assert tok1.state == "RESERVED"

    # Reserve another 2 slots -> should succeed (total 4 reserved)
    tok2 = logger.reserve_critical_slots(count=2)
    assert tok2 is not None

    # Try to reserve 2 more slots -> only 1 available, must return None
    tok3 = logger.reserve_critical_slots(count=2)
    assert tok3 is None

    # Consume 1 slot on tok1
    assert logger.consume(tok1, "INTENT", {"action": "click"})
    assert tok1.slots_consumed == 1

    # Release tok2 without consuming
    logger.release(tok2)
    assert tok2.state == "RELEASED"

    # Now slots are freed, reservation of 2 slots must succeed again
    tok4 = logger.reserve_critical_slots(count=2)
    assert tok4 is not None

    logger.close()


def test_action_manager_per_region_quota():
    from bot.core.models import SafetyConfig
    cfg = SafetyConfig(max_clicks_per_region=2, max_clicks_per_second=10.0)
    manager = ActionManager(safety_config=cfg)
    backend = MockWorkingBackend()
    manager.probe_and_bind(backend, {})

    # 2 clicks for region_A succeed
    assert manager.dispatch_action(10, 10, {"region_id": "region_A"})
    assert manager.dispatch_action(10, 10, {"region_id": "region_A"})

    # 3rd click for region_A blocked by region quota
    assert not manager.dispatch_action(10, 10, {"region_id": "region_A"})

    # But region_B can still click!
    assert manager.dispatch_action(20, 20, {"region_id": "region_B"})


def test_action_manager_total_quota():
    from bot.core.models import SafetyConfig
    cfg = SafetyConfig(max_total_clicks=3, max_clicks_per_second=10.0, circuit_breaker_threshold=10)
    manager = ActionManager(safety_config=cfg)
    backend = MockWorkingBackend()
    manager.probe_and_bind(backend, {})

    assert manager.dispatch_action(10, 10, {})
    assert manager.dispatch_action(10, 10, {})
    assert manager.dispatch_action(10, 10, {})

    # 4th click exceeds total quota -> blocked and trips breaker
    assert not manager.dispatch_action(10, 10, {})
    assert manager._circuit_breaker_tripped


def test_onnx_verifier_cache_invalidation_by_content():
    import numpy as np
    from bot.vision.onnx_verifier import ONNXVerifier
    verifier = ONNXVerifier(model_path="models/ui_vision_encoder.onnx")

    img_a = np.zeros((64, 64, 3), dtype=np.uint8)
    img_b = np.ones((64, 64, 3), dtype=np.uint8) * 255

    # Cache target_1 with img_a
    verifier.cache_target_embedding("target_1", [img_a])
    cached_a = verifier.get_cached_target_embedding("target_1", [img_a])
    assert cached_a is not None

    # Query with img_b (different profile/content under same target_1 ID) -> Miss!
    cached_miss = verifier.get_cached_target_embedding("target_1", [img_b])
    assert cached_miss is None

    # Clear cache
    verifier.clear_target_cache()
    assert verifier.get_cached_target_embedding("target_1", [img_a]) is None

