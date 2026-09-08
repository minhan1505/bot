"""
bot.telemetry.hardware_sla_harness
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
SLA Hardware Acceptance Harness for BotAutoClick V2.3.
Enforces Execution Guard #4 & #5:
  - 100% full live runner execution: runs real BotRuntimeRunner thread.
  - Supported Workload: 19 active concurrent regions under Two-Tier Scheduling.
  - Production Safety Bounds: max_clicks_per_second=10.0, circuit_breaker_threshold=30.
  - Measures full end-to-end latency from frame visibility to action dispatch.
  - Honest Reporting: Distinguishes software-in-the-loop harness from physical rig certification.
"""

import os
import sys
import time
import queue
import logging
import threading
import numpy as np
import cv2
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field

from bot.core.models import (
    Profile, Target, CalibrationProfile, RegionModel,
    Workflow, WorkflowStep, SafetyConfig, DecisionResult, ActionType
)
from bot.core.coordinates import Rect
from bot.vision.geometry import GeometryVerifier
from bot.vision.onnx_verifier import ONNXVerifier
from bot.vision.proposal import CandidateProposalEngine
from bot.vision.engine import VisionEngine
from bot.workflow.ledger import SessionLedger
from bot.workflow.runner import BotRuntimeRunner
from bot.action.manager import ActionManager
from bot.action.base import BaseActionBackend, ActionDispatchResult, ActionDispatchStatus
from bot.telemetry.benchmark import BenchmarkRunner, BenchmarkReport
from bot.telemetry.logger import AsyncTelemetryLogger
from tests.test_geometry import make_glyph_image

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.abspath("models/ui_vision_encoder.onnx")


class SimulatedLiveActionBackend(BaseActionBackend):
    """Simulates realistic background action backend with dispatch latency."""

    def __init__(self, dispatch_delay_sec: float = 0.003):
        self.dispatch_delay_sec = dispatch_delay_sec
        self.dispatched_events: List[Dict[str, Any]] = []

    def probe_capability(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        return True, "SIMULATED_LIVE_CDP_SUPPORTED"

    def dispatch_click(
        self,
        screen_x: int,
        screen_y: int,
        context: Dict[str, Any]
    ) -> ActionDispatchResult:
        if self.dispatch_delay_sec > 0:
            time.sleep(self.dispatch_delay_sec)
        record = {
            "time": time.perf_counter(),
            "x": screen_x,
            "y": screen_y,
            "region_id": context.get("region_id")
        }
        self.dispatched_events.append(record)
        return ActionDispatchResult(
            status=ActionDispatchStatus.DISPATCHED,
            reason="OK",
            target_screen_pt=(screen_x, screen_y)
        )

    def close(self):
        pass


class FrameInjectorCaptureManager:
    """Mockable DXGI capture manager allowing controlled target injection for SLA testing."""

    def __init__(self, base_frame: np.ndarray):
        self.base_frame = base_frame
        self.current_frame = base_frame.copy()
        self._lock = threading.Lock()
        self.grab_count = 0

    def update_frame(self, frame: np.ndarray):
        with self._lock:
            self.current_frame = frame.copy()

    def grab(self) -> Tuple[Optional[np.ndarray], float]:
        with self._lock:
            self.grab_count += 1
            return self.current_frame.copy(), time.time()

    def grab_sub_roi(self, rect: Rect) -> Tuple[Optional[np.ndarray], float]:
        frame, t_capture = self.grab()
        if frame is None:
            return None, t_capture
        h, w = frame.shape[:2]
        x1 = max(0, min(rect.x, w))
        y1 = max(0, min(rect.y, h))
        x2 = max(x1, min(rect.right, w))
        y2 = max(y1, min(rect.bottom, h))
        if x2 - x1 <= 0 or y2 - y1 <= 0:
            return None, t_capture
        return frame[y1:y2, x1:x2].copy(), t_capture

    def get_desktop_offset(self) -> Tuple[int, int]:
        return (0, 0)

    def close(self):
        pass


class HardwareSLAAcceptanceHarness:
    """
    Executes an end-to-end SLA workload acceptance benchmark against BotRuntimeRunner.
    Runs 19 active concurrent regions across multi-table layouts.
    """

    def __init__(
        self,
        n_regions: int = 19,
        screen_size: Tuple[int, int] = (1920, 1080),
        target_size: Tuple[int, int] = (48, 48),
        sample_iterations: int = 25
    ):
        self.n_regions = n_regions
        self.screen_width, self.screen_height = screen_size
        self.target_width, self.target_height = target_size
        self.sample_iterations = sample_iterations

        # Build synthetic base frame (desktop with 19 region boundaries)
        self.base_frame = np.zeros((self.screen_height, self.screen_width, 3), dtype=np.uint8)

        # Vision Subsystem
        self.geo_verifier = GeometryVerifier(canonical_size=(64, 64))
        self.onnx_verifier = ONNXVerifier(model_path=MODEL_PATH, canonical_size=(64, 64))
        self.proposal_engine = CandidateProposalEngine(k_base_per_region=4, max_batch_limit=128)
        self.vision_engine = VisionEngine(self.onnx_verifier, self.geo_verifier, self.proposal_engine)

        # Action Backend & Safety Manager
        self.backend = SimulatedLiveActionBackend(dispatch_delay_sec=0.003)
        self.safety_config = SafetyConfig(
            max_clicks_per_second=10.0,
            circuit_breaker_threshold=30,
            max_clicks_per_region=100,
            max_total_clicks=1000
        )
        self.action_manager = ActionManager(
            backend=self.backend,
            safety_config=self.safety_config
        )
        self.action_manager.probe_and_bind(self.backend, {"surface_verified": True})

        # Capture Manager
        self.capture_manager = FrameInjectorCaptureManager(self.base_frame)

        # Target & Confuser Setup
        self.target_img = make_glyph_image("CHECK", bg_color=(180, 40, 40))
        self.confuser_img = make_glyph_image("CROSS", bg_color=(40, 40, 180))

        os.makedirs("data/benchmark_targets", exist_ok=True)
        self.target_path = os.path.abspath("data/benchmark_targets/sla_target_check.png")
        self.confuser_path = os.path.abspath("data/benchmark_targets/sla_confuser_cross.png")
        cv2.imwrite(self.target_path, self.target_img)
        cv2.imwrite(self.confuser_path, self.confuser_img)

        # Calibrated Profile
        self.calib_profile = CalibrationProfile(
            model_sha256=self.onnx_verifier.model_sha256,
            precision="FP32",
            canonical_size=(64, 64),
            t_g=0.60,
            t_e=0.70,
            m_safe=0.05,
            min_pos_sim=0.92,
            max_neg_sim=0.65,
            separation_gap=0.27,
            sample_count_pos=10,
            sample_count_neg=10
        )

        self.target = Target(
            target_id="sla_check_target",
            name="SLA Check Target",
            reference_image_paths=[self.target_path],
            confuser_image_paths=[self.confuser_path],
            calibration=self.calib_profile
        )

        # Create Profile with 19 Regions
        self.profile = Profile(
            profile_id="sla_acceptance_profile",
            name="SLA 19-Region Acceptance Profile",
            safety_config=self.safety_config,
            scan_interval_ms=16 # 60 FPS
        )
        self.profile.targets[self.target.target_id] = self.target

        self.regions_dict: Dict[str, RegionModel] = {}
        self.workflows_dict: Dict[str, Workflow] = {}

        cols = 6
        cell_w, cell_h = 280, 280
        for i in range(self.n_regions):
            col = i % cols
            row = i // cols
            rx = 50 + col * (cell_w + 20)
            ry = 50 + row * (cell_h + 20)
            r_id = f"table_{i + 1}"
            reg = RegionModel(region_id=r_id, name=f"Table {i + 1}", x=rx, y=ry, w=cell_w, h=cell_h)
            self.profile.regions[r_id] = reg
            self.regions_dict[r_id] = reg

            wf = Workflow(
                workflow_id=f"wf_{r_id}",
                name=f"Workflow {r_id}",
                steps=[WorkflowStep(step_index=0, target_id=self.target.target_id, timeout_ms=10000, cooldown_ms=100)]
            )
            self.profile.workflows[wf.workflow_id] = wf
            reg.workflow_id = wf.workflow_id

        self.ledger = SessionLedger()
        for r_id, reg in self.regions_dict.items():
            wf = self.profile.workflows[reg.workflow_id]
            self.ledger.register_region(reg, wf)

        # Telemetry Logger
        os.makedirs("logs", exist_ok=True)
        self.telemetry_logger = AsyncTelemetryLogger("logs/hardware_sla_telemetry.jsonl")

        self.latencies_ms: List[float] = []
        self.samples_collected = 0

    def run_benchmark(self) -> BenchmarkReport:
        """Runs the live runner SLA acceptance benchmark across 19 concurrent regions."""
        logger.info(f"Starting Hardware SLA Acceptance Harness with {self.n_regions} regions...")

        # Setup Runner
        runner = BotRuntimeRunner(
            profile=self.profile,
            capture_manager=self.capture_manager,
            vision_engine=self.vision_engine,
            action_manager=self.action_manager,
            ledger=self.ledger,
            telemetry_logger=self.telemetry_logger,
            is_dry_run=False,
            is_production=True
        )

        runner.start()
        time.sleep(0.05) # Allow worker thread to initialize

        latencies = []
        expected_samples = self.sample_iterations

        try:
            for iteration in range(expected_samples):
                # Reset all 19 regions in ledger
                for r_id, inst in self.ledger.get_all_instances().items():
                    inst.start_workflow()

                # Pick active region for this target appearance
                active_idx = iteration % self.n_regions
                active_rid = f"table_{active_idx + 1}"
                active_reg = self.regions_dict[active_rid]

                # Draw fresh frame with target in active region
                frame = self.base_frame.copy()
                # Place target inside the active region
                tx = active_reg.x + 40
                ty = active_reg.y + 40
                frame[ty:ty + self.target_height, tx:tx + self.target_width] = self.target_img

                # Occasionally place confusers in neighboring regions to test selectivity
                if iteration % 3 == 0:
                    neigh_idx = (active_idx + 1) % self.n_regions
                    neigh_reg = self.regions_dict[f"table_{neigh_idx + 1}"]
                    frame[neigh_reg.y + 40:neigh_reg.y + 40 + self.target_height,
                          neigh_reg.x + 40:neigh_reg.x + 40 + self.target_width] = self.confuser_img

                # Record exact time target becomes visible
                t_first_visible = time.perf_counter()
                self.capture_manager.update_frame(frame)

                # Wait for dispatch to active region
                initial_dispatches = len(self.backend.dispatched_events)
                deadline = t_first_visible + 0.700 # 700ms hard SLA deadline
                dispatched = False

                while time.perf_counter() < deadline + 0.100: # Wait up to deadline + 100ms
                    if len(self.backend.dispatched_events) > initial_dispatches:
                        last_event = self.backend.dispatched_events[-1]
                        if last_event.get("region_id") == active_rid:
                            t_dispatched = last_event["time"]
                            latency_ms = (t_dispatched - t_first_visible) * 1000.0
                            latencies.append(latency_ms)
                            dispatched = True
                            break
                    time.sleep(0.005)

                if not dispatched:
                    elapsed_ms = (time.perf_counter() - t_first_visible) * 1000.0
                    logger.error(f"Sample {iteration + 1} timed out for region {active_rid} (Elapsed: {elapsed_ms:.2f}ms)")
                    latencies.append(elapsed_ms)

                # Clear frame before next iteration with realistic pacing under anti-runaway 10/sec bound
                self.capture_manager.update_frame(self.base_frame)
                time.sleep(0.120)

        finally:
            runner.stop()
            self.telemetry_logger.close()

        # Compute SLA Report
        report = BenchmarkRunner.evaluate_latency_samples(
            latencies=latencies,
            stage_records=[],
            n_regions=self.n_regions,
            resolution=(self.screen_width, self.screen_height),
            model_sha256=self.onnx_verifier.model_sha256
        )

        return report


def main():
    harness = HardwareSLAAcceptanceHarness(n_regions=19, sample_iterations=20)
    report = harness.run_benchmark()
    print("\n" + report.to_markdown())
    if report.passed_hard_sla:
        print("\n[PASS] HARD SLA ACCEPTANCE PASSED (Software Harness, 19 concurrent regions, max <= 700ms).")
    else:
        print("\n[FAIL] HARD SLA ACCEPTANCE FAILED!")


if __name__ == "__main__":
    main()
