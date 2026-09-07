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
        """Starts workflow at Step 1."""
        self.generation += 1
        self.current_step_index = 0
        self.transition_to(RegionState.WAIT_STEP, reason="Workflow started")

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

    def on_target_detected(self, target_id: str, screen_pos: tuple):
        """Triggered when target of current step is detected."""
        if self.state != RegionState.WAIT_STEP:
            return
        if target_id != self.expected_target_id:
            return
        self.detected_target_id = target_id
        self.detected_screen_pos = screen_pos
        self.transition_to(RegionState.TARGET_DETECTED, reason=f"Target '{target_id}' detected at {screen_pos}")

    def on_fresh_verified(self):
        """Triggered when target is re-confirmed on fresh frame."""
        if self.state == RegionState.TARGET_DETECTED:
            self.transition_to(RegionState.VERIFIED, reason="Fresh frame confirmed presence of target")

    def on_action_dispatched(self):
        """Triggered when background action is dispatched."""
        if self.state == RegionState.VERIFIED:
            now = time.time()
            self.last_action_at = now
            self.transition_to(RegionState.ACTION_PENDING, reason="Action dispatched")

    def advance_step(self):
        """Advances to next step or completes workflow."""
        self.current_step_index += 1
        self.detected_target_id = None
        self.detected_screen_pos = None

        if self.current_step_index >= len(self.workflow.steps):
            self.transition_to(RegionState.DONE, reason="All workflow steps completed successfully")
        else:
            self.transition_to(RegionState.WAIT_STEP, reason=f"Advancing to Step {self.current_step_index + 1}")

    def check_timeout(self, now: float) -> bool:
        """Checks if current step has timed out."""
        step = self.current_step
        if not step or self.state not in (RegionState.WAIT_STEP, RegionState.TARGET_DETECTED):
            return False

        elapsed_ms = (now - self.state_entered_at) * 1000.0
        if elapsed_ms > step.timeout_ms:
            self.transition_to(RegionState.TIMEOUT, reason=f"Step {self.current_step_index + 1} timed out ({elapsed_ms:.1f}ms > {step.timeout_ms}ms)")
            return True
        return False
