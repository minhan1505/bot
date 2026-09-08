"""
bot.action.base
~~~~~~~~~~~~~~~
Abstract Action Backend Interface.
Hard Invariant:
  Production backends must operate completely in the background without moving,
  locking, or seizing the Windows system cursor.
  Physical mouse fallback is strictly forbidden.
"""

from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from typing import Tuple, Dict, Any, Optional


class ActionDispatchStatus(str, Enum):
    DISPATCHED = "DISPATCHED"        # Down and Up completed with verified ACK
    NOT_SENT = "NOT_SENT"            # Failed before sending (dead HWND, out of bounds, etc.)
    UNCERTAIN = "UNCERTAIN"          # Sent down but up failed / ACK lost
    FAIL_CLOSED = "FAIL_CLOSED"      # Blocked by safety (breaker, rate limit, probe status, emergency stop)


@dataclass
class ActionDispatchResult:
    status: ActionDispatchStatus
    reason: str = ""
    target_screen_pt: Tuple[int, int] = (0, 0)
    viewport_css_pt: Optional[Tuple[float, float]] = None

    @property
    def is_success(self) -> bool:
        return self.status == ActionDispatchStatus.DISPATCHED

    def __bool__(self) -> bool:
        return self.is_success


class BaseActionBackend(ABC):
    """Abstract interface for background non-physical action backends."""

    @abstractmethod
    def probe_capability(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Executes an end-to-end capability probe on the target surface.
        Verifies:
          1. Target application/surface receives and processes the action.
          2. Windows system cursor remains completely stationary (0px shift).
        Returns:
          (is_supported, reason_string)
        """
        pass

    @abstractmethod
    def dispatch_click(
        self,
        screen_x: int,
        screen_y: int,
        context: Dict[str, Any]
    ) -> bool:
        """
        Dispatches a background click at the designated target position.
        Returns True if successfully dispatched, False otherwise.
        """
        pass

    def dispatch_double_click(
        self,
        screen_x: int,
        screen_y: int,
        context: Dict[str, Any]
    ) -> ActionDispatchResult:
        """
        Dispatches a background double-click (FC-08, U01).
        Enforces T01 Outcome Truth:
          - If click 1 fails to send: returns NOT_SENT (0 clicks sent).
          - If click 1 succeeds, but click 2 fails/errors: status MUST be UNCERTAIN,
            NEVER NOT_SENT! Reporting NOT_SENT would cause naive retries to send duplicate
            clicks (3-4 clicks).
        """
        import time
        res1 = self.dispatch_click(screen_x, screen_y, context)
        if not isinstance(res1, ActionDispatchResult):
            if bool(res1):
                res1 = ActionDispatchResult(ActionDispatchStatus.DISPATCHED, "Click 1 sent", target_screen_pt=(screen_x, screen_y))
            else:
                res1 = ActionDispatchResult(ActionDispatchStatus.NOT_SENT, "Click 1 not sent", target_screen_pt=(screen_x, screen_y))

        if res1.status != ActionDispatchStatus.DISPATCHED:
            return res1

        time.sleep(0.050)
        res2 = self.dispatch_click(screen_x, screen_y, context)
        if not isinstance(res2, ActionDispatchResult):
            if bool(res2):
                res2 = ActionDispatchResult(ActionDispatchStatus.DISPATCHED, "Click 2 sent", target_screen_pt=(screen_x, screen_y))
            else:
                res2 = ActionDispatchResult(ActionDispatchStatus.NOT_SENT, "Click 2 not sent", target_screen_pt=(screen_x, screen_y))

        if res2.status == ActionDispatchStatus.DISPATCHED:
            return ActionDispatchResult(
                ActionDispatchStatus.DISPATCHED,
                "Double-click dispatched successfully",
                target_screen_pt=(screen_x, screen_y),
                viewport_css_pt=getattr(res2, "viewport_css_pt", getattr(res1, "viewport_css_pt", None))
            )
        else:
            # Click 1 was already dispatched to target!
            # Under T01 Invariant, partial dispatch CANNOT be reported as NOT_SENT.
            return ActionDispatchResult(
                ActionDispatchStatus.UNCERTAIN,
                f"Double-click partial dispatch: click 1 sent, click 2 failed ({res2.reason})",
                target_screen_pt=(screen_x, screen_y),
                viewport_css_pt=getattr(res2, "viewport_css_pt", getattr(res1, "viewport_css_pt", None))
            )

    @abstractmethod
    def close(self):
        """Releases backend connections."""
        pass

    def invalidate_surface_context(self, reason: str = "context_invalidated") -> None:
        """Invalidates surface context on geometry drift or resolution change."""
        pass

    def verify_viewport_freshness(self, ctx: Optional[Any] = None) -> Tuple[bool, str]:
        """Verifies that surface geometry has not drifted from context."""
        return True, "FRESH"
