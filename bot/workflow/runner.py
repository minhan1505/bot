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
from bot.action.base import ActionDispatchResult, ActionDispatchStatus
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
        is_production: bool = False,
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
        self.is_production = is_production
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

                    # Collect competitor targets for Gate 3 Margin evaluation
                    alt_targets = {}
                    for other_id, other_cfg in self.profile.targets.items():
                        if other_id != expected_target_id and other_cfg.reference_image_paths:
                            alt_img = cv2.imread(other_cfg.reference_image_paths[0])
                            if alt_img is not None:
                                alt_targets[other_id] = alt_img

                    try:
                        if alt_targets:
                            decisions = self.vision_engine.evaluate_candidates(
                                frame, region_candidates, target_cfg, ref_img, alt_targets
                            )
                        else:
                            decisions = self.vision_engine.evaluate_candidates(
                                frame, region_candidates, target_cfg, ref_img
                            )
                    except TypeError:
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
                                    if inst.state != RegionState.WAIT_STEP:
                                        continue

                                    snapshot_gen = inst.generation
                                    snapshot_step = inst.current_step_index

                                    inst.on_target_detected(expected_target_id, (cand_cx, cand_cy))
                                    if inst.state != RegionState.TARGET_DETECTED:
                                        continue

                                    # Fresh Verify (FR-053): Re-confirm on a fresh sub-ROI with Tri-Gate Authority
                                    sub_rect = Rect(res.candidate_rect[0], res.candidate_rect[1], res.candidate_rect[2], res.candidate_rect[3])
                                    fresh_crop = None
                                    if hasattr(self.capture_manager, "grab_sub_roi"):
                                        fresh_crop, _ = self.capture_manager.grab_sub_roi(sub_rect)

                                    # Invariant Check: Verify generation and step did not change during grab
                                    if inst.generation != snapshot_gen or inst.current_step_index != snapshot_step:
                                        logger.warning(f"Region {owner_region}: Stale generation or step changed during fresh verify grab.")
                                        continue

                                    # Invariant Check: Verify step deadline did not expire during grab
                                    if inst.is_deadline_expired():
                                        logger.warning(f"Region {owner_region}: Step deadline expired during fresh verify grab.")
                                        inst.transition_to(RegionState.TIMEOUT, reason="Step deadline expired during fresh verify grab")
                                        continue

                                    if fresh_crop is None:
                                        inst.on_fresh_verify_failed("Sub-ROI crop failed on fresh frame")
                                        continue

                                    # Fast tri-condition confirmation on fresh frame
                                    g_fresh = 1.0
                                    if hasattr(self.vision_engine, "geo_verifier") and self.vision_engine.geo_verifier is not None:
                                        g_fresh = self.vision_engine.geo_verifier.compute_geometry_score(fresh_crop, ref_img)

                                    calib = target_cfg.calibration
                                    t_g = calib.t_g if calib else 0.5
                                    t_e = calib.t_e if calib else 0.65
                                    m_safe = calib.m_safe if calib else 0.05

                                    geo_fresh_pass = (g_fresh >= t_g)

                                    emb_fresh_pass = True
                                    margin_fresh_pass = True

                                    if hasattr(self.vision_engine, "onnx_verifier") and self.vision_engine.onnx_verifier is not None:
                                        fresh_emb = self.vision_engine.onnx_verifier.compute_embeddings([fresh_crop])[0]
                                        target_emb = self.vision_engine.onnx_verifier.get_cached_target_embedding(expected_target_id, [ref_img])
                                        e_fresh = self.vision_engine.onnx_verifier.cosine_similarity(fresh_emb, target_emb) if target_emb is not None else 0.0
                                        emb_fresh_pass = (e_fresh >= t_e)

                                        if alt_targets:
                                            competitor_scores = [
                                                self.vision_engine.onnx_verifier.cosine_similarity(
                                                    fresh_emb,
                                                    self.vision_engine.onnx_verifier.get_cached_target_embedding(aid, [aimg])
                                                )
                                                for aid, aimg in alt_targets.items()
                                                if self.vision_engine.onnx_verifier.get_cached_target_embedding(aid, [aimg]) is not None
                                            ]
                                            if competitor_scores:
                                                best_comp = max(competitor_scores)
                                                margin_fresh_pass = ((e_fresh - best_comp) >= m_safe)

                                    if not (geo_fresh_pass and emb_fresh_pass and margin_fresh_pass):
                                        reason = f"Fresh verify tri-gate failed: G={g_fresh:.3f}/{t_g}"
                                        logger.warning(f"Region {owner_region}: {reason}")
                                        inst.on_fresh_verify_failed(reason)
                                        continue

                                    # Invariant Check: Verify generation, step, and deadline before moving to VERIFIED
                                    if inst.generation != snapshot_gen or inst.current_step_index != snapshot_step:
                                        logger.warning(f"Region {owner_region}: Stale generation or step changed before fresh verification commit.")
                                        continue

                                    if inst.is_deadline_expired():
                                        logger.warning(f"Region {owner_region}: Step deadline expired after fresh verify tri-gate.")
                                        inst.transition_to(RegionState.TIMEOUT, reason="Step deadline expired after fresh verify tri-gate")
                                        continue

                                    inst.on_fresh_verified()
                                    if inst.state != RegionState.VERIFIED:
                                        continue

                                    # Map coordinates with desktop offset
                                    desktop_offset = (0, 0)
                                    if hasattr(self.capture_manager, "get_desktop_offset"):
                                        desktop_offset = self.capture_manager.get_desktop_offset()

                                    screen_x = cand_cx + desktop_offset[0]
                                    screen_y = cand_cy + desktop_offset[1]

                                    backend = getattr(self.action_manager, "backend", None)
                                    action_context = {
                                        "hwnd": getattr(backend, "hwnd", None),
                                        "region_id": owner_region,
                                        "workflow_id": inst.workflow.workflow_id,
                                        "step_index": inst.current_step_index,
                                        "generation": inst.generation,
                                        "timestamp": time.time(),
                                        "desktop_offset": desktop_offset,
                                        "viewport_context": getattr(backend, "viewport_context", None),
                                        "is_production": self.is_production,
                                    }

                                    step = inst.current_step
                                    if step and step.action_type == ActionType.DETECT_ONLY:
                                        # DETECT_ONLY: Advance without physical dispatch
                                        logger.info(f"[DETECT_ONLY] Target '{expected_target_id}' detected for Region {owner_region}")
                                        inst.on_action_dispatched()
                                        inst.advance_step()
                                        if self.on_state_change:
                                            self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                    elif not self.is_dry_run:
                                        # Strict Invariant: Check deadline immediately before dispatch
                                        if inst.is_deadline_expired():
                                            logger.warning(f"Region {owner_region}: Step deadline expired before action dispatch")
                                            inst.transition_to(RegionState.TIMEOUT, reason="Step deadline expired before action dispatch")
                                            continue

                                        # Invariant Check: Verify generation & state immediately before dispatch
                                        if inst.generation != snapshot_gen or inst.current_step_index != snapshot_step or inst.state != RegionState.VERIFIED:
                                            logger.warning(f"Region {owner_region}: Generation, step or state mismatch before dispatch.")
                                            continue

                                        if not inst.can_attempt_dispatch():
                                            logger.warning(f"Region {owner_region} exhausted dispatch attempt budget")
                                            inst.transition_to(RegionState.REJECTED, reason="Dispatch retry budget exhausted")
                                            continue

                                        # Telemetry critical reservation
                                        token = None
                                        if self.telemetry:
                                            token = self.telemetry.reserve_critical_slots(count=2)
                                            if token is None:
                                                logger.error(f"Telemetry buffer full. SAFE_PAUSE triggered for Region {owner_region}")
                                                inst.transition_to(RegionState.SAFE_PAUSE, reason="Telemetry buffer full, cannot guarantee audit evidence")
                                                continue

                                            intent_data = {
                                                "region_id": owner_region,
                                                "step_index": inst.current_step_index,
                                                "generation": inst.generation,
                                                "target_id": expected_target_id,
                                                "screen_pos": (screen_x, screen_y),
                                                "context": action_context
                                            }
                                            intent_ok = False
                                            if hasattr(self.telemetry, "consume"):
                                                intent_ok = bool(self.telemetry.consume(token, "ACTION_INTENT", intent_data))
                                            elif hasattr(token, "consume"):
                                                intent_ok = bool(token.consume("ACTION_INTENT", intent_data))
                                            else:
                                                self.telemetry.log_event("ACTION_INTENT", intent_data)
                                                intent_ok = True

                                            if not intent_ok:
                                                logger.error(f"Critical ACTION_INTENT rejected for Region {owner_region}. Halting dispatch with SAFE_PAUSE.")
                                                if hasattr(self.telemetry, "release"):
                                                    self.telemetry.release(token)
                                                elif hasattr(token, "release"):
                                                    token.release()
                                                inst.transition_to(RegionState.SAFE_PAUSE, reason="Critical ACTION_INTENT audit recording rejected")
                                                if self.on_state_change:
                                                    self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                                continue

                                        # Record attempt counter before dispatch
                                        inst.on_action_attempt()

                                        dispatched = self.action_manager.dispatch_action(screen_x, screen_y, action_context)

                                        outcome_ok = True
                                        if token and self.telemetry:
                                            outcome_data = {
                                                "region_id": owner_region,
                                                "step_index": inst.current_step_index,
                                                "generation": inst.generation,
                                                "dispatched": bool(dispatched),
                                                "timestamp": time.time()
                                            }
                                            if hasattr(self.telemetry, "consume"):
                                                outcome_ok = bool(self.telemetry.consume(token, "ACTION_OUTCOME", outcome_data))
                                            elif hasattr(token, "consume"):
                                                outcome_ok = bool(token.consume("ACTION_OUTCOME", outcome_data))
                                            else:
                                                self.telemetry.log_event("ACTION_OUTCOME", outcome_data)
                                                outcome_ok = True

                                            if not outcome_ok:
                                                logger.error(f"Critical ACTION_OUTCOME rejected for Region {owner_region}. Marking evidence incomplete and pausing execution.")
                                                if hasattr(self.telemetry, "release"):
                                                    self.telemetry.release(token)
                                                elif hasattr(token, "release"):
                                                    token.release()

                                        # Handle Action Outcome with explicit ActionDispatchResult handling
                                        if isinstance(dispatched, ActionDispatchResult) or hasattr(dispatched, "status"):
                                            status = getattr(dispatched, "status", None)
                                            reason = getattr(dispatched, "reason", "")
                                            if status == ActionDispatchStatus.UNCERTAIN:
                                                logger.warning(f"Uncertain action dispatch for Region {owner_region}: {reason}")
                                                inst.transition_to(RegionState.UNCERTAIN_HOLD, reason=reason or "Action uncertain")
                                                if not outcome_ok:
                                                    inst.transition_to(RegionState.SAFE_PAUSE, reason="Critical ACTION_OUTCOME evidence recording failed")
                                                if self.on_state_change:
                                                    self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                                continue
                                            elif status == ActionDispatchStatus.FAIL_CLOSED:
                                                logger.error(f"Fail-closed action dispatch for Region {owner_region}: {reason}")
                                                inst.transition_to(RegionState.REJECTED, reason=reason or "Action fail-closed")
                                                if not outcome_ok:
                                                    inst.transition_to(RegionState.SAFE_PAUSE, reason="Critical ACTION_OUTCOME evidence recording failed")
                                                if self.on_state_change:
                                                    self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                                continue
                                            elif status == ActionDispatchStatus.NOT_SENT:
                                                logger.warning(f"Action not sent for Region {owner_region}: {reason}")
                                                if inst.can_attempt_dispatch() and outcome_ok:
                                                    inst.transition_to(RegionState.WAIT_STEP, reason="Action not sent; retry available within deadline")
                                                else:
                                                    inst.transition_to(RegionState.REJECTED, reason=f"Action not sent: {reason}" if reason else "Action not sent and retry budget exhausted")
                                                if not outcome_ok:
                                                    inst.transition_to(RegionState.SAFE_PAUSE, reason="Critical ACTION_OUTCOME evidence recording failed")
                                                if self.on_state_change:
                                                    self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                                continue
                                            elif status == ActionDispatchStatus.DISPATCHED:
                                                inst.on_action_dispatched()
                                                if outcome_ok:
                                                    inst.advance_step()
                                                else:
                                                    inst.transition_to(RegionState.SAFE_PAUSE, reason="Critical ACTION_OUTCOME evidence recording failed")
                                                if self.on_state_change:
                                                    self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                                continue

                                        # Generic boolean handling (legacy / mocks)
                                        if bool(dispatched):
                                            inst.on_action_dispatched()
                                            if outcome_ok:
                                                inst.advance_step()
                                            else:
                                                inst.transition_to(RegionState.SAFE_PAUSE, reason="Critical ACTION_OUTCOME evidence recording failed")
                                            if self.on_state_change:
                                                self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                            continue
                                        else:
                                            logger.warning(f"Failed to dispatch action for Region {owner_region}")
                                            if inst.can_attempt_dispatch() and outcome_ok:
                                                inst.transition_to(RegionState.WAIT_STEP, reason="Dispatch failed; retry available within deadline")
                                            else:
                                                inst.transition_to(RegionState.REJECTED, reason="Dispatch failed and retry limit reached")
                                            if not outcome_ok:
                                                inst.transition_to(RegionState.SAFE_PAUSE, reason="Critical ACTION_OUTCOME evidence recording failed")
                                            if self.on_state_change:
                                                self.on_state_change(owner_region, inst.state.value, inst.current_step_index)
                                            continue
                                    else:
                                        # Dry-Run mode: Advance without dispatching
                                        logger.info(f"[DRY-RUN] WOULD_CLICK at ({screen_x}, {screen_y}) for Region {owner_region}")
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
