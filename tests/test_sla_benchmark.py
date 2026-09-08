"""
tests/test_sla_benchmark.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
End-to-End Latency Benchmark & Hard SLA Verification.
Tests the complete pipeline:
  Capture -> Proposal -> Geometry/ONNX Tri-Condition -> Association -> Action Dispatch
Across the Supported Workload: 19 active regions.
Enforces:
  max_latency <= 700.0 ms
  count_gt_700ms == 0
"""

import os
import time
import pytest
import numpy as np

from bot.core.models import Target, CalibrationProfile, RegionModel, Workflow, WorkflowStep
from bot.core.coordinates import Rect
from bot.vision.geometry import GeometryVerifier
from bot.vision.onnx_verifier import ONNXVerifier
from bot.vision.proposal import CandidateProposalEngine
from bot.vision.engine import VisionEngine
from bot.workflow.ledger import SessionLedger
from bot.workflow.scheduler import TwoTierScheduler
from bot.action.manager import ActionManager
from bot.action.base import BaseActionBackend
from bot.telemetry.benchmark import BenchmarkRunner
from tests.test_geometry import make_glyph_image

MODEL_PATH = os.path.abspath("models/ui_vision_encoder.onnx")


class SimulatedInstantActionBackend(BaseActionBackend):
    def probe_capability(self, context):
        return True, "SIMULATED_BACKEND"

    def dispatch_click(self, screen_x, screen_y, context):
        # Emulate async network dispatch latency (2ms)
        time.sleep(0.002)
        return True

    def close(self):
        pass


def test_hard_sla_supported_workload_19_regions():
    """
    End-to-End Latency Benchmark over 19 active regions.
    Hard Acceptance Criteria: 100% of samples must satisfy Latency <= 700.0 ms.
    """
    # 1. Setup Engines
    geo_verifier = GeometryVerifier(canonical_size=(64, 64))
    onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
    proposal_engine = CandidateProposalEngine(k_base_per_region=4, max_batch_limit=32)
    vision_engine = VisionEngine(onnx_verifier, geo_verifier, proposal_engine)

    action_backend = SimulatedInstantActionBackend()
    action_manager = ActionManager(max_clicks_per_second=1000.0, circuit_breaker_threshold=1000)
    action_manager.probe_and_bind(action_backend, {})

    # 2. Setup Target & Calibration
    target_img = make_glyph_image("CHECK", bg_color=(200, 50, 50))
    calib = CalibrationProfile(
        model_sha256=onnx_verifier.model_sha256,
        precision="FP32",
        canonical_size=(64, 64),
        t_g=0.60,
        t_e=0.70,
        m_safe=0.05
    )
    target = Target(target_id="target_check", name="Check Target", calibration=calib)
    calib.target_content_hash = target.compute_content_hash()

    # 3. Setup 19 Regions in Grid
    wf = Workflow(workflow_id="wf1", name="WF", steps=[WorkflowStep(step_index=0, target_id="target_check")])
    ledger = SessionLedger()
    scheduler = TwoTierScheduler(tier2_group_count=3)

    # Screen synthetic frame: 1920x1080 with 19 tables
    screen_frame = np.zeros((1080, 1920, 3), dtype=np.uint8)

    regions_cfg = {}
    for i in range(19):
        rx = (i % 6) * 300 + 50
        ry = (i // 6) * 300 + 50
        r_id = f"table_{i+1}"
        cfg = RegionModel(region_id=r_id, name=r_id, x=rx, y=ry, w=250, h=250)
        regions_cfg[r_id] = cfg
        inst = ledger.register_region(cfg, wf)
        inst.start_workflow()

        # Place target in first 4 regions
        if i < 4:
            screen_frame[ry+50:ry+98, rx+50:rx+98] = target_img

    # 4. Benchmark Execution Loop (N = 25 iterations with rotating active target regions)
    latencies = []
    stage_breakdowns = []
    n_iterations = 25
    expected_dispatches = 0
    successful_dispatches = 0

    for iteration in range(n_iterations):
        # Reset all regions to fresh WAIT_STEP state
        for r_id, inst in ledger.get_all_instances().items():
            inst.start_workflow()

        # Place target in a rotating region for this cycle
        active_idx = iteration % 19
        active_rid = f"table_{active_idx + 1}"
        active_cfg = regions_cfg[active_rid]

        # Reset screen frame and place target
        screen_frame.fill(0)
        screen_frame[active_cfg.y+50:active_cfg.y+98, active_cfg.x+50:active_cfg.x+98] = target_img
        first_visible_time = time.perf_counter()
        expected_dispatches += 1

        dispatch_success = False
        t_sched_total = 0.0
        t_prop_total = 0.0
        t_veri_total = 0.0
        t_fv_total = 0.0
        t_disp_total = 0.0

        # Run frame cycles (up to 5 frames at 60fps) until Two-Tier Scheduler picks the active region
        for frame_cycle in range(5):
            # Stage A: Scheduling
            t0 = time.perf_counter()
            scheduled_regions = scheduler.select_regions_for_frame(ledger.get_all_instances())
            t_sched_total += (time.perf_counter() - t0) * 1000.0

            # Stage B: Candidate Proposal across scheduled regions
            t0 = time.perf_counter()
            proposals_by_region = {}
            for r_id, expected_target_id in scheduled_regions:
                cfg = regions_cfg[r_id]
                crop = screen_frame[cfg.y:cfg.y + cfg.h, cfg.x:cfg.x + cfg.w]
                props = proposal_engine.generate_proposals_for_region(
                    crop, Rect(cfg.x, cfg.y, cfg.w, cfg.h), target_img, r_id
                )
                if props:
                    proposals_by_region[r_id] = props

            candidates, diag = proposal_engine.apply_same_frame_escalation(proposals_by_region)
            t_prop_total += (time.perf_counter() - t0) * 1000.0

            if not candidates:
                continue

            # Stage C: Tri-Condition Verification (Geometry + ONNX Batch)
            t0 = time.perf_counter()
            decisions = vision_engine.evaluate_candidates(screen_frame, candidates, target, target_img)
            t_veri_total += (time.perf_counter() - t0) * 1000.0

            # Stage D: Fresh Verification & Action Dispatch
            for res in decisions:
                if res.decision.value == "MATCH":
                    owner_region = ledger.associate_candidate(
                        res.candidate_rect[0], res.candidate_rect[1], target.target_id, candidate_region_id=active_rid
                    )
                    if owner_region:
                        # Fresh verify simulation on sub-ROI
                        t0_fv = time.perf_counter()
                        rx, ry, rw, rh = res.candidate_rect
                        fresh_crop = screen_frame[ry:ry+rh, rx:rx+rw]
                        g_fresh = geo_verifier.compute_geometry_score(fresh_crop, target_img)
                        assert g_fresh >= calib.t_g, f"Fresh verify failed in benchmark: {g_fresh:.3f} < {calib.t_g}"
                        t_fv_total = (time.perf_counter() - t0_fv) * 1000.0

                        # Action dispatch
                        t0_disp = time.perf_counter()
                        dispatched = action_manager.dispatch_action(rx, ry, {"region_id": owner_region})
                        assert dispatched, "Action dispatch must succeed in benchmark"
                        ledger.advance_region(owner_region)
                        t_disp_total = (time.perf_counter() - t0_disp) * 1000.0
                        dispatch_success = True
                        successful_dispatches += 1
                        break

            if dispatch_success:
                break

        action_dispatched_time = time.perf_counter()
        total_latency_ms = (action_dispatched_time - first_visible_time) * 1000.0

        latencies.append(total_latency_ms)
        stage_breakdowns.append({
            "scheduling": t_sched_total,
            "proposal": t_prop_total,
            "verification": t_veri_total,
            "fresh_verify": t_fv_total,
            "dispatch": t_disp_total
        })

    assert successful_dispatches == expected_dispatches, (
        f"Mismatch: {successful_dispatches}/{expected_dispatches} targets dispatched"
    )

    # 5. Evaluate Report
    report = BenchmarkRunner.evaluate_latency_samples(
        latencies=latencies,
        stage_records=stage_breakdowns,
        n_regions=19,
        resolution=(1920, 1080),
        model_sha256=onnx_verifier.model_sha256
    )

    print("\n" + report.to_markdown())

    # HARD SLA INVARIANT CHECKS (Execution Guard #4 & #5):
    assert report.count_gt_700ms == 0, f"Hard SLA Violated! {report.count_gt_700ms} samples exceeded 700ms."
    assert report.max_latency_ms <= 700.0, f"Max latency ({report.max_latency_ms:.2f}ms) exceeded 700ms."
    assert report.passed_hard_sla
