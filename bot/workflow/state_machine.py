"""
bot.workflow.state_machine
~~~~~~~~~~~~~~~~~~~~~~~~~~
Generic Region State Machine for 1..N Step Workflows.
Enforces per-region ownership, generation tracking, and strict transition invariants.
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import time
import logging
from bot.core.models import Workflow, WorkflowStep

logger = logging.getLogger(__name__)


class RegionState(str, Enum):
    IDLE = "IDLE"
    WAIT_STEP = "WAIT_STEP"
    TARGET_DETECTED = "TARGET_DETECTED"
    VERIFIED = "VERIFIED"
    ACTION_PENDING = "ACTION_PENDING"
    UNCERTAIN_HOLD = "UNCERTAIN_HOLD"
    DONE = "DONE"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"


@dataclass
class RegionInstance:
    """
    State record for a single Region / Table.
    Maintains generation ID to prevent stale frame detections from corrupting new rounds.
    """
    region_id: str
    workflow: Workflow
    current_step_index: int = 0
    generation: int = 1
    state: RegionState = RegionState.IDLE
    state_entered_at: float = field(default_factory=time.time)
    step_started_at: float = field(default_factory=time.time)
    step_deadline: float = 0.0
    dispatch_attempts: int = 0
    fresh_verify_failures: int = 0
    last_action_at: float = 0.0
    detected_target_id: Optional[str] = None
    detected_screen_pos: Optional[tuple] = None  # (x, y)
    history: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def current_step(self) -> Optional[WorkflowStep]:
        if 0 <= self.current_step_index < len(self.workflow.steps):
            return self.workflow.steps[self.current_step_index]
        return None

    @property
    def expected_target_id(self) -> Optional[str]:
        step = self.current_step
        return step.target_id if step else None

    def start_workflow(self):
        """Starts workflow at Step 1 with clean retry budget and deadline."""
        self.generation += 1
        self.current_step_index = 0
        self.dispatch_attempts = 0
        self.fresh_verify_failures = 0
        now = time.time()
        self.step_started_at = now
        timeout_sec = (self.current_step.timeout_ms / 1000.0) if self.current_step else 5.0
        self.step_deadline = now + timeout_sec
        self.transition_to(RegionState.WAIT_STEP, reason="Workflow started")

    def can_attempt_dispatch(self) -> bool:
        """Determines if retry budget allows a new dispatch attempt (1 + retry_limit)."""
        step = self.current_step
        if not step:
            return False
        max_attempts = 1 + step.retry_limit
        return self.dispatch_attempts < max_attempts

    def on_action_attempt(self):
        """Increments attempt counter immediately upon initiation of dispatch."""
        self.dispatch_attempts += 1

    def transition_to(self, new_state: RegionState, reason: str = ""):
        """Executes a validated state transition with audit history."""
        old_state = self.state
        now = time.time()
        self.state = new_state
        self.state_entered_at = now
        entry = {
            "from": old_state.value,
            "to": new_state.value,
            "step": self.current_step_index,
            "generation": self.generation,
            "timestamp": now,
            "reason": reason
        }
        self.history.append(entry)
        logger.debug(f"[Region {self.region_id}|Gen {self.generation}] {old_state.value} -> {new_state.value} ({reason})")

    def is_in_cooldown(self, now: Optional[float] = None) -> bool:
        """Checks if region is currently in cooldown from previous action."""
        if self.last_action_at <= 0.0:
            return False
        if now is None:
            now = time.time()
        cooldown_ms = 0
        if self.current_step_index > 0 and self.current_step_index - 1 < len(self.workflow.steps):
            cooldown_ms = max(cooldown_ms, self.workflow.steps[self.current_step_index - 1].cooldown_ms)
        if self.current_step:
            cooldown_ms = max(cooldown_ms, self.current_step.cooldown_ms)
        elapsed_sec = now - self.last_action_at
        return elapsed_sec < (cooldown_ms / 1000.0)

    def on_target_detected(self, target_id: str, screen_pos: tuple, now: Optional[float] = None):
        """Triggered when target of current step is detected."""
        if self.state != RegionState.WAIT_STEP:
            return
        if target_id != self.expected_target_id:
            return
        if self.is_in_cooldown(now):
            return
        self.detected_target_id = target_id
        self.detected_screen_pos = screen_pos
        self.transition_to(RegionState.TARGET_DETECTED, reason=f"Target '{target_id}' detected at {screen_pos}")

    def on_fresh_verified(self):
        """Triggered when target is re-confirmed on fresh frame."""
        if self.state == RegionState.TARGET_DETECTED:
            self.transition_to(RegionState.VERIFIED, reason="Fresh frame confirmed presence of target")

    def on_fresh_verify_failed(self, reason: str = ""):
        """Triggered when fresh verification fails."""
        if self.state == RegionState.TARGET_DETECTED:
            self.fresh_verify_failures += 1
            step = self.current_step
            max_fresh_retries = (1 + step.retry_limit) if step else 1
            if self.fresh_verify_failures >= max_fresh_retries:
                self.transition_to(RegionState.REJECTED, reason=f"Fresh verify failed ({self.fresh_verify_failures}/{max_fresh_retries}): {reason}")
            else:
                self.transition_to(RegionState.WAIT_STEP, reason=f"Fresh verify failed, retry available ({self.fresh_verify_failures}/{max_fresh_retries}): {reason}")

    def on_action_dispatched(self):
        """Triggered when background action is dispatched."""
        if self.state in (RegionState.VERIFIED, RegionState.TARGET_DETECTED):
            now = time.time()
            self.last_action_at = now
            self.transition_to(RegionState.ACTION_PENDING, reason="Action dispatched")

    def advance_step(self):
        """Advances to next step or completes workflow with clean retry budget and deadline."""
        self.current_step_index += 1
        self.dispatch_attempts = 0
        self.fresh_verify_failures = 0
        self.detected_target_id = None
        self.detected_screen_pos = None

        if self.current_step_index >= len(self.workflow.steps):
            self.transition_to(RegionState.DONE, reason="All workflow steps completed successfully")
        else:
            now = time.time()
            self.step_started_at = now
            timeout_sec = (self.current_step.timeout_ms / 1000.0) if self.current_step else 5.0
            self.step_deadline = now + timeout_sec
            self.transition_to(RegionState.WAIT_STEP, reason=f"Advancing to Step {self.current_step_index + 1}")

    def check_timeout(self, now: float) -> bool:
        """Checks if current step has timed out against monotonic/time deadline."""
        step = self.current_step
        if not step or self.state in (RegionState.IDLE, RegionState.DONE, RegionState.TIMEOUT, RegionState.REJECTED):
            return False

        deadline = self.step_deadline if self.step_deadline > 0 else (self.step_started_at + (step.timeout_ms / 1000.0))
        if now >= deadline:
            self.transition_to(RegionState.TIMEOUT, reason=f"Step {self.current_step_index + 1} timed out ({deadline:.1f} <= {now:.1f})")
            return True
        return False
