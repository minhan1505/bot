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
    action_manager = ActionManager()
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

    # 4. Benchmark Execution Loop (N = 25 iterations)
    latencies = []
    stage_breakdowns = []
    n_iterations = 25

    for iteration in range(n_iterations):
        t_start = time.perf_counter()

        # Stage A: Scheduling
        t0 = time.perf_counter()
        scheduled_regions = scheduler.select_regions_for_frame(ledger.get_all_instances())
        t_sched = (time.perf_counter() - t0) * 1000.0

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
        t_proposal = (time.perf_counter() - t0) * 1000.0

        # Stage C: Tri-Condition Verification (Geometry + ONNX Batch)
        t0 = time.perf_counter()
        decisions = vision_engine.evaluate_candidates(screen_frame, candidates, target, target_img)
        t_verify = (time.perf_counter() - t0) * 1000.0

        # Stage D: Association & Action Dispatch
        t0 = time.perf_counter()
        for res in decisions:
            if res.decision.value == "MATCH":
                owner_region = ledger.associate_candidate(res.candidate_rect[0], res.candidate_rect[1], target.target_id)
                if owner_region:
                    action_manager.dispatch_action(res.candidate_rect[0], res.candidate_rect[1], {})
                    ledger.advance_region(owner_region)
        t_dispatch = (time.perf_counter() - t0) * 1000.0

        t_end = time.perf_counter()
        total_latency_ms = (t_end - t_start) * 1000.0

        latencies.append(total_latency_ms)
        stage_breakdowns.append({
            "scheduling": t_sched,
            "proposal": t_proposal,
            "verification": t_verify,
            "dispatch": t_dispatch
        })

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
