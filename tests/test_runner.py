"""
tests/test_runner.py
~~~~~~~~~~~~~~~~~~~~
Unit tests for BotRuntimeRunner live background execution thread.
"""

import os
import time
import pytest
import numpy as np
import cv2

from bot.core.models import Profile, Target, RegionModel, Workflow, WorkflowStep, CalibrationProfile
from bot.capture.base import BaseCapture
from bot.capture.manager import CaptureManager
from bot.vision.geometry import GeometryVerifier
from bot.vision.onnx_verifier import ONNXVerifier
from bot.vision.proposal import CandidateProposalEngine
from bot.vision.engine import VisionEngine
from bot.workflow.ledger import SessionLedger
from bot.workflow.runner import BotRuntimeRunner
from bot.action.manager import ActionManager
from tests.test_action_and_safety import MockWorkingBackend
from tests.test_geometry import make_glyph_image

MODEL_PATH = os.path.abspath("models/ui_vision_encoder.onnx")


class MockScreenCapture(BaseCapture):
    def __init__(self, frame: np.ndarray):
        self.frame = frame

    def grab(self):
        return self.frame.copy(), time.perf_counter()

    def get_desktop_offset(self):
        return (0, 0)

    def get_dimensions(self):
        return (self.frame.shape[1], self.frame.shape[0])

    def close(self):
        pass


def test_bot_runtime_runner_executes_live_cycle(tmp_path):
    target_img = make_glyph_image("CHECK", bg_color=(200, 50, 50))
    ref_path = str(tmp_path / "ref_check.png")
    cv2.imwrite(ref_path, target_img)

    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    proposal_engine = CandidateProposalEngine(k_base_per_region=4, max_batch_limit=32)
    vision_engine = VisionEngine(onnx_verifier, geo_verifier, proposal_engine)

    calib = CalibrationProfile(
        model_sha256=onnx_verifier.model_sha256,
        precision="FP32",
        canonical_size=(64, 64),
        t_g=0.60,
        t_e=0.70,
        m_safe=0.05
    )

    target = Target(target_id="target_check", name="Check", reference_image_paths=[ref_path], calibration=calib)
    reg = RegionModel(region_id="r1", name="Region 1", x=50, y=50, w=200, h=200)
    wf = Workflow(workflow_id="wf1", name="WF", steps=[WorkflowStep(step_index=0, target_id="target_check")])

    prof = Profile(
        profile_id="runner_test_prof",
        name="Runner Test",
        regions={"r1": reg},
        targets={"target_check": target},
        workflows={"default_workflow": wf},
        scan_interval_ms=10
    )

    # Synthetic frame with target at (80, 80)
    frame = np.zeros((400, 400, 3), dtype=np.uint8)
    frame[80:128, 80:128] = target_img

    capture_mgr = CaptureManager()
    capture_mgr.set_mock_backend(MockScreenCapture(frame))

    action_backend = MockWorkingBackend()
    action_mgr = ActionManager()
    action_mgr.probe_and_bind(action_backend, {})

    ledger = SessionLedger()
    inst = ledger.register_region(reg, wf)
    inst.start_workflow()

    runner = BotRuntimeRunner(
        profile=prof,
        capture_manager=capture_mgr,
        vision_engine=vision_engine,
        action_manager=action_mgr,
        ledger=ledger,
        is_dry_run=False
    )

    # Start runner
    runner.start()
    assert runner.is_running

    # Wait for runner to process at least 2 cycles
    time.sleep(0.150)

    # Stop runner
    runner.stop()
    assert not runner.is_running

    # Assert that match was found and action was dispatched
    assert runner.total_evaluations > 0
    assert runner.total_matches > 0
    assert len(action_backend.dispatched_clicks) >= 1


from types import SimpleNamespace
from unittest.mock import patch
from bot.action.base import ActionDispatchResult, ActionDispatchStatus
from bot.workflow.state_machine import RegionState
from qa.test_independent_regressions import run_scenario


@pytest.mark.parametrize("status", [
    ActionDispatchStatus.DISPATCHED,
    ActionDispatchStatus.UNCERTAIN,
    ActionDispatchStatus.FAIL_CLOSED,
    ActionDispatchStatus.NOT_SENT,
])
@pytest.mark.parametrize("outcome_ok", [True, False])
def test_dispatch_status_truth_and_outcome_evidence_matrix(status, outcome_ok):
    telemetry = SimpleNamespace(
        log_event=lambda *a: None,
        reserve_critical_slots=lambda **kw: object(),
        consume=lambda token, event, data: outcome_ok if event == "ACTION_OUTCOME" else True,
        release=lambda *a: None
    )
    original_init = BotRuntimeRunner.__init__

    def init_with_telemetry(runner, *args, **kwargs):
        original_init(runner, *args, **kwargs)
        runner.telemetry = telemetry

    result = ActionDispatchResult(status, f"Injected-{status.value}")
    with patch.object(BotRuntimeRunner, "__init__", init_with_telemetry):
        inst, _, attempts = run_scenario(steps=2, cooldown_ms=0, dispatch_ok=result)

    if outcome_ok:
        if status == ActionDispatchStatus.DISPATCHED:
            assert len(attempts) == 2, f"Expected both steps to dispatch, got {len(attempts)}"
            assert inst.last_action_at > 0
            assert any(e["to"] == "ACTION_PENDING" and e["reason"] == "Action dispatched" for e in inst.history)
            assert inst.state == RegionState.DONE
        elif status == ActionDispatchStatus.NOT_SENT:
            assert len(attempts) > 1, f"Expected retries for NOT_SENT within deadline, got {len(attempts)}"
            assert inst.last_action_at == 0
            assert not any(e["to"] == "ACTION_PENDING" and e["reason"] == "Action dispatched" for e in inst.history)
        elif status == ActionDispatchStatus.UNCERTAIN:
            assert len(attempts) == 1, f"Uncertain must never retry, got {len(attempts)}"
            assert inst.last_action_at == 0
            assert not any(e["to"] == "ACTION_PENDING" and e["reason"] == "Action dispatched" for e in inst.history)
            assert inst.state == RegionState.UNCERTAIN_HOLD
        elif status == ActionDispatchStatus.FAIL_CLOSED:
            assert len(attempts) == 1, f"Fail-closed must never retry, got {len(attempts)}"
            assert inst.last_action_at == 0
            assert not any(e["to"] == "ACTION_PENDING" and e["reason"] == "Action dispatched" for e in inst.history)
            assert inst.state == RegionState.REJECTED
    else:
        # When telemetry outcome evidence fails, ALL new actions and retries MUST be blocked
        assert len(attempts) == 1, f"Telemetry evidence failure must halt at 1 attempt, got {len(attempts)}"
        assert inst.state == RegionState.SAFE_PAUSE
        if status == ActionDispatchStatus.DISPATCHED:
            assert inst.last_action_at > 0
            assert any(e["to"] == "ACTION_PENDING" and e["reason"] == "Action dispatched" for e in inst.history)
            assert inst.current_step_index == 0  # Step 1 never advanced
        else:
            assert inst.last_action_at == 0
            assert not any(e["to"] == "ACTION_PENDING" and e["reason"] == "Action dispatched" for e in inst.history)

