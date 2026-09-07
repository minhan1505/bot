"""
bot.workflow.runner
~~~~~~~~~~~~~~~~~~~
Real-Time Execution Runtime Worker.
Executes the unified live pipeline:
  Capture -> Scheduler -> Hybrid Proposal -> Same-Frame Escalation
  -> Tri-Condition Verification -> Fresh Verify -> Background Action Dispatch
Runs on a background thread to ensure GUI remains 100% responsive.
"""

import time
import threading
from typing import Dict, Optional, Callable, List
import numpy as np
import logging

from bot.core.models import Profile, Target, DecisionResult, DecisionClass, ActionType
from bot.core.coordinates import Rect
from bot.capture.manager import CaptureManager
from bot.vision.engine import VisionEngine
from bot.workflow.ledger import SessionLedger
from bot.workflow.scheduler import TwoTierScheduler
from bot.workflow.state_machine import RegionState
from bot.action.manager import ActionManager
from bot.telemetry.logger import AsyncTelemetryLogger

logger = logging.getLogger(__name__)


class BotRuntimeRunner:
    """
    Orchestrates live bot execution on a background thread.
    """

    def __init__(
        self,
        profile: Profile,
        capture_manager: CaptureManager,
        vision_engine: VisionEngine,
        action_manager: ActionManager,
        ledger: SessionLedger,
        telemetry_logger: Optional[AsyncTelemetryLogger] = None,
        is_dry_run: bool = False,
        on_decision_callback: Optional[Callable[[DecisionResult], None]] = None,
        on_state_callback: Optional[Callable[[str, str, int], None]] = None
    ):
        self.profile = profile
        self.capture_manager = capture_manager
        self.vision_engine = vision_engine
        self.action_manager = action_manager
        self.ledger = ledger
        self.telemetry = telemetry_logger
        self.is_dry_run = is_dry_run
        self.on_decision = on_decision_callback
        self.on_state_change = on_state_callback

        self.scheduler = TwoTierScheduler(tier2_group_count=3)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._session_id = f"session_{int(time.time())}"
        self.total_evaluations = 0
        self.total_matches = 0

    def start(self):
        """Starts real-time background worker loop."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info(f"BotRuntimeRunner started (Session: {self._session_id}, DryRun={self.is_dry_run})")

    def stop(self):
        """Stops background worker cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("BotRuntimeRunner stopped cleanly.")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def _run_loop(self):
        scan_interval_sec = max(0.005, self.profile.scan_interval_ms / 1000.0)

        while not self._stop_event.is_set():
            t_cycle_start = time.perf_counter()

            # 1. Capture screen frame
            frame, t_capture = self.capture_manager.grab()
            if frame is None:
                time.sleep(0.010)
                continue

            now = time.time()

            # 2. Timeout stuck regions
            timed_out = self.ledger.timeout_stuck_regions(now)
            for r_id in timed_out:
                if self.on_state_change:
                    self.on_state_change(r_id, "TIMEOUT", 0)

            # 3. Two-Tier Scheduler: Pick regions to inspect this frame
            scheduled_regions = self.scheduler.select_regions_for_frame(self.ledger.get_all_instances())
            if not scheduled_regions:
                # No active regions waiting
                time.sleep(0.010)
                continue

            # 4. Generate candidate proposals per region
            proposals_by_region = {}
            for r_id, target_id in scheduled_regions:
                target_cfg = self.profile.targets.get(target_id)
                region_cfg = self.profile.regions.get(r_id)

                if not target_cfg or not region_cfg or not target_cfg.reference_image_paths:
                    continue

                # Read reference image
                import cv2
                ref_img = cv2.imread(target_cfg.reference_image_paths[0])
                if ref_img is None:
                    continue

                r_rect = Rect(region_cfg.x, region_cfg.y, region_cfg.w, region_cfg.h)
                h_f, w_f = frame.shape[:2]
                rx1 = max(0, min(r_rect.x, w_f))
                ry1 = max(0, min(r_rect.y, h_f))
                rx2 = max(rx1, min(r_rect.right, w_f))
                ry2 = max(ry1, min(r_rect.bottom, h_f))

                if rx2 - rx1 < 8 or ry2 - ry1 < 8:
                    continue

                region_crop = frame[ry1:ry2, rx1:rx2]
                props = self.vision_engine.proposal_engine.generate_proposals_for_region(
                    region_crop, r_rect, ref_img, r_id
                )
                if props:
                    proposals_by_region[r_id] = props

            # 5. Same-Frame Escalation & Quota
            candidates, diag = self.vision_engine.proposal_engine.apply_same_frame_escalation(proposals_by_region)

            # 6. Tri-Condition Verification
            if candidates:
                # Group candidates by target_id of their region
                for r_id, expected_target_id in scheduled_regions:
                    region_candidates = [c for c in candidates if c.region_id == r_id]
                    if not region_candidates:
                        continue

                    target_cfg = self.profile.targets.get(expected_target_id)
                    if not target_cfg or not target_cfg.reference_image_paths:
                        continue

                    import cv2
                    ref_img = cv2.imread(target_cfg.reference_image_paths[0])
                    if ref_img is None:
                        continue

                    decisions = self.vision_engine.evaluate_candidates(
                        frame, region_candidates, target_cfg, ref_img
                    )

                    for res in decisions:
                        self.total_evaluations += 1
                        if self.on_decision:
                            self.on_decision(res)

                        if self.telemetry:
                            self.telemetry.log_event("DECISION", res.model_dump())

                        if res.decision == DecisionClass.MATCH:
                            self.total_matches += 1
                            cand_cx = res.candidate_rect[0] + res.candidate_rect[2] // 2
                            cand_cy = res.candidate_rect[1] + res.candidate_rect[3] // 2

                            # Associate candidate with Region
                            candidate_region_id = r_id
                            owner_region = self.ledger.associate_candidate(
                                cand_cx, cand_cy, expected_target_id, candidate_region_id=r_id
                            )
                            if owner_region:
                                inst = self.ledger.get_instance(owner_region)
                                if inst:
                                    inst.on_target_detected(expected_target_id, (cand_cx, cand_cy))

                                    # Fresh Verify (FR-053): Re-confirm on a fresh sub-ROI
                                    sub_rect = Rect(res.candidate_rect[0], res.candidate_rect[1], res.candidate_rect[2], res.candidate_rect[3])
                                    fresh_crop, _ = self.capture_manager.grab_sub_roi(sub_rect)

                                    if fresh_crop is not None:
                                        # Fast geometry confirmation on fresh frame
                                        g_fresh = self.vision_engine.geo_verifier.compute_geometry_score(fresh_crop, ref_img)
                                        if g_fresh >= (target_cfg.calibration.t_g if target_cfg.calibration else 0.5):
                                            inst.on_fresh_verified()

                                            step = inst.current_step
                                            if step and step.action_type == ActionType.DETECT_ONLY:
                                                # DETECT_ONLY: Advance without physical dispatch
                                                logger.info(f"[DETECT_ONLY] Target '{expected_target_id}' detected for Region {owner_region}")
                                                inst.on_action_dispatched()
                                                inst.advance_step()
                                                if self.on_state_change:
                                                    self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                            elif not self.is_dry_run:
                                                dispatched = self.action_manager.dispatch_action(cand_cx, cand_cy, {})
                                                if dispatched:
                                                    inst.on_action_dispatched()
                                                    inst.advance_step()
                                                    if self.on_state_change:
                                                        self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                                else:
                                                    logger.warning(f"Failed to dispatch action for Region {owner_region} - retrying")
                                                    inst.transition_to(RegionState.WAIT_STEP, reason="Dispatch failed; retrying")
                                            else:
                                                # Dry-Run mode: Advance without dispatching
                                                logger.info(f"[DRY-RUN] WOULD_CLICK at ({cand_cx}, {cand_cy}) for Region {owner_region}")
                                                inst.advance_step()
                                                if self.on_state_change:
                                                    self.on_state_change(owner_region, inst.state.value, inst.current_step_index)

            # Precise pacing
            t_elapsed = time.perf_counter() - t_cycle_start
            t_sleep = max(0.001, scan_interval_sec - t_elapsed)
            # Sleep in short slices so stop() responds within 5ms
            slice_steps = int(t_sleep / 0.005)
            for _ in range(slice_steps):
                if self._stop_event.is_set():
                    break
                time.sleep(0.005)
