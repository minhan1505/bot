"""QA acceptance counterexamples. Expected to FAIL on audited commit 3f5dc65.

No real capture, browser connection, cursor operation, or production modification.
Run: python -m pytest qa/test_independent_regressions.py -q
"""
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from bot.core.models import (
    ActionType, CalibrationProfile, DecisionClass, DecisionResult, Profile,
    RegionModel, Target, Workflow, WorkflowStep,
)
from bot.workflow.ledger import SessionLedger
from bot.workflow.runner import BotRuntimeRunner
from bot.workflow.scheduler import TwoTierScheduler


def run_scenario(action_type=ActionType.CLICK, dispatch_ok=True, fresh_ok=True,
                 steps=1, cooldown_ms=500):
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    region = RegionModel(region_id="r", name="R", x=0, y=0, w=32, h=32)
    target = Target(target_id="t", name="T", reference_image_paths=["qa-mock.png"],
                    calibration=CalibrationProfile(model_sha256="mock", t_g=.8, t_e=.8, m_safe=.1))
    workflow = Workflow(workflow_id="w", name="W", steps=[
        WorkflowStep(step_index=i, target_id="t", action_type=action_type,
                     cooldown_ms=cooldown_ms, timeout_ms=1000) for i in range(steps)])
    profile = Profile(profile_id="p", name="P", regions={"r": region}, targets={"t": target})
    ledger = SessionLedger()
    inst = ledger.register_region(region, workflow)
    inst.start_workflow()
    decision = DecisionResult(target_id="t", region_id="r", candidate_rect=(8, 8, 16, 16),
        geometry_score=1, geometry_pass=True, embedding_similarity=1, embedding_pass=True,
        identity_margin=1, margin_pass=True, decision=DecisionClass.MATCH, reason="QA controlled match")
    proposal = SimpleNamespace(region_id="r")
    proposal_engine = SimpleNamespace(
        generate_proposals_for_region=lambda *a: [proposal],
        apply_same_frame_escalation=lambda *a: ([proposal], {}))
    vision = SimpleNamespace(proposal_engine=proposal_engine,
        evaluate_candidates=lambda *a: [decision],
        geo_verifier=SimpleNamespace(compute_geometry_score=lambda *a: 1 if fresh_ok else 0))
    attempts = []
    def dispatch(*args):
        attempts.append((time.perf_counter(), args))
        return dispatch_ok
    action = SimpleNamespace(dispatch_action=dispatch)
    capture = SimpleNamespace(grab_sub_roi=lambda *a: (image, time.perf_counter()))
    runner = BotRuntimeRunner(profile, capture, vision, action, ledger)
    runner.scheduler = TwoTierScheduler(tier2_group_count=1)
    count = 0
    def grab():
        nonlocal count
        count += 1
        if count > 4:
            runner._stop_event.set()
            return None, time.perf_counter()
        return image, time.perf_counter()
    capture.grab = grab
    with patch("cv2.imread", return_value=image):
        runner._run_loop()
    return inst, ledger, attempts


def test_detect_only_step_must_not_click():
    _, _, attempts = run_scenario(action_type=ActionType.DETECT_ONLY)
    assert not attempts, "DETECT_ONLY dispatched a physical-effect action request"


def test_cooldown_is_enforced_between_steps():
    _, _, attempts = run_scenario(steps=2, cooldown_ms=10000)
    assert len(attempts) == 1, "Second action dispatched during configured 10-second cooldown"


def test_failed_dispatch_does_not_leave_immortal_verified_state():
    inst, ledger, attempts = run_scenario(dispatch_ok=False)
    timed_out = ledger.timeout_stuck_regions(inst.state_entered_at + 60)
    assert len(attempts) > 1 or "r" in timed_out, (
        f"No retry and no timeout after dispatch failure; state={inst.state.value}")


def test_overlap_cannot_steal_explicit_candidate_region():
    ledger = SessionLedger()
    wf = Workflow(workflow_id="w", name="W", steps=[WorkflowStep(step_index=0, target_id="t")])
    for region_id, x in [("A", 0), ("B", 50)]:
        ledger.register_region(RegionModel(region_id=region_id, name=region_id,
            x=x, y=0, w=100, h=100), wf).start_workflow()
    # Runner has a decision tagged B, but drops that identity at association.
    candidate_region_id = "B"
    owner = ledger.associate_candidate(75, 50, "t")
    assert owner == candidate_region_id, f"Candidate B reassigned to {owner}"


def test_preprocessing_change_invalidates_calibration():
    calibration = CalibrationProfile(model_sha256="model", preprocessing_version="obsolete",
                                     t_g=.8, t_e=.8, m_safe=.1)
    assert not calibration.is_valid_for("model", "FP32", (64, 64))


def test_shadow_mode_never_enables_action_dispatch():
    from bot.ui.main_window import MainWindow
    profile = Profile(profile_id="p", name="P", regions={
        "r": RegionModel(region_id="r", name="R", x=0, y=0, w=32, h=32)})
    ui = SimpleNamespace(runner=None, combo_mode=MagicMock(), action_manager=MagicMock(),
        active_profile=profile, capture_manager=MagicMock(), vision_engine=MagicMock(),
        decision_received_signal=MagicMock(), region_state_signal=MagicMock(),
        btn_start=MagicMock(), tray_manager=MagicMock())
    ui.combo_mode.currentText.return_value = "Shadow Mode"
    ui.action_manager.is_supported = True
    with patch("bot.ui.main_window.BotRuntimeRunner") as runner_class:
        MainWindow._toggle_bot(ui)
    assert runner_class.call_args.kwargs["is_dry_run"] is True, (
        "Shadow Mode enables the same dispatch branch as Production")
