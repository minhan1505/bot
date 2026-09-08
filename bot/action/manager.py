"""
bot.action.manager
~~~~~~~~~~~~~~~~~~
Action Manager with Safety Guards and Capability Verification.
Enforces:
  - Strict Fail-Closed: If background backend is unsupported, bot halts with BACKGROUND_ACTION_UNSUPPORTED.
  - Anti-Runaway Guard (FR-072): Rate-limits click frequency and trips circuit breaker on runaway bursts.
  - Emergency Hotkey stop (F12).
  - Absolutely zero physical mouse fallback.
"""

import time
from typing import Optional, Dict, Any, Tuple
import logging
from bot.core.models import SafetyConfig
from bot.action.base import BaseActionBackend, ActionDispatchResult, ActionDispatchStatus
from bot.action.cdp_backend import CDPActionBackend
from bot.action.window_backend import WindowActionBackend

logger = logging.getLogger(__name__)


class ActionManager:
    """
    Manages background action dispatching with anti-runaway safety guards.
    """

    def __init__(
        self,
        backend: Optional[BaseActionBackend] = None,
        safety_config: Optional[SafetyConfig] = None,
        max_clicks_per_second: float = 10.0,
        circuit_breaker_threshold: int = 30
    ):
        self.backend = backend
        if safety_config is not None:
            self.safety_config = safety_config
        else:
            self.safety_config = SafetyConfig(
                max_clicks_per_second=max_clicks_per_second,
                circuit_breaker_threshold=circuit_breaker_threshold
            )

        self.max_clicks_per_sec = self.safety_config.max_clicks_per_second
        self.circuit_breaker_threshold = self.safety_config.circuit_breaker_threshold

        self.is_supported = False
        self.is_protocol_verified = False
        self.is_surface_verified = False
        self.support_status_message = "NOT_PROBED"
        self.emergency_stop_triggered = False
        self.viewport_context = None

        # Anti-runaway tracking
        self._recent_click_timestamps = []
        self._total_clicks = 0
        self._region_clicks: Dict[str, int] = {}
        self._circuit_breaker_tripped = False

    def probe_and_bind(self, backend: BaseActionBackend, context: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Runs capability probe on target surface.
        Binds backend only if supported.
        """
        self.backend = backend
        self.viewport_context = context.get("viewport_context")
        if self.backend and hasattr(self.backend, "viewport_context") and self.viewport_context is not None:
            self.backend.viewport_context = self.viewport_context

        supported, reason = self.backend.probe_capability(context)
        self.is_supported = supported
        self.is_protocol_verified = supported
        self.is_surface_verified = context.get("surface_verified", False)
        self.support_status_message = reason

        if not supported:
            logger.error(f"Action capability probe failed: {reason}. Status: BACKGROUND_ACTION_UNSUPPORTED.")
            self.backend = None
        else:
            logger.info(f"Action capability probe passed: {reason}.")

        return self.is_supported, self.support_status_message

    def invalidate_binding(self, reason: str = "geometry_invalidation") -> None:
        """
        Invalidates current backend surface binding on geometry drift, window move/resize, or DPR change.
        """
        logger.warning(f"ActionManager surface binding invalidated: {reason}")
        self.is_surface_verified = False
        self.viewport_context = None
        self.support_status_message = f"BINDING_INVALIDATED: {reason}"
        if self.backend and hasattr(self.backend, "invalidate_surface_context"):
            try:
                self.backend.invalidate_surface_context(reason)
            except Exception:
                pass

    def trigger_emergency_stop(self):
        """Emergency stop handler (e.g. on F12 keypress)."""
        self.emergency_stop_triggered = True
        logger.critical("EMERGENCY STOP TRIGGERED. All action dispatching blocked immediately.")

    def reset_emergency_stop(self):
        self.emergency_stop_triggered = False
        self._circuit_breaker_tripped = False

    def dispatch_action(
        self,
        screen_x: int,
        screen_y: int,
        context: Dict[str, Any]
    ) -> ActionDispatchResult:
        """
        Safely dispatches action subject to safety guards.
        Returns ActionDispatchResult distinguishing DISPATCHED, UNCERTAIN, FAIL_CLOSED, NOT_SENT.
        """
        if self.viewport_context is not None and "viewport_context" not in context:
            context["viewport_context"] = self.viewport_context
        if self.emergency_stop_triggered:
            logger.warning("Action dispatch rejected: Emergency stop active.")
            return ActionDispatchResult(ActionDispatchStatus.FAIL_CLOSED, "Emergency stop active", target_screen_pt=(screen_x, screen_y))

        if self._circuit_breaker_tripped:
            logger.warning("Action dispatch rejected: Circuit breaker tripped (Anti-Runaway).")
            return ActionDispatchResult(ActionDispatchStatus.FAIL_CLOSED, "Circuit breaker tripped (Anti-Runaway)", target_screen_pt=(screen_x, screen_y))

        if not self.backend or not self.is_supported:
            logger.error("Action dispatch rejected: BACKGROUND_ACTION_UNSUPPORTED. Physical mouse fallback is forbidden.")
            return ActionDispatchResult(ActionDispatchStatus.FAIL_CLOSED, "BACKGROUND_ACTION_UNSUPPORTED", target_screen_pt=(screen_x, screen_y))

        # If production mode requested, require surface verification
        if context.get("is_production", False) and not self.is_surface_verified:
            logger.error("Action dispatch rejected: SURFACE_UNVERIFIED_ACTION_BLOCKED. Protocol ACK is not surface verification.")
            return ActionDispatchResult(ActionDispatchStatus.FAIL_CLOSED, "SURFACE_UNVERIFIED_ACTION_BLOCKED", target_screen_pt=(screen_x, screen_y))

        # Check geometry freshness if requested or if backend supports it
        if context.get("verify_freshness", False) and hasattr(self.backend, "verify_viewport_freshness"):
            fresh, fresh_err = self.backend.verify_viewport_freshness(self.viewport_context)
            if not fresh:
                self.invalidate_binding(fresh_err)
                return ActionDispatchResult(
                    ActionDispatchStatus.FAIL_CLOSED,
                    f"GEOMETRY_FRESHNESS_FAILED: {fresh_err}",
                    target_screen_pt=(screen_x, screen_y)
                )

        # Total click quota check
        if self._total_clicks >= self.safety_config.max_total_clicks:
            logger.critical(f"Total click quota exceeded ({self._total_clicks} >= {self.safety_config.max_total_clicks}). Halting.")
            self._circuit_breaker_tripped = True
            return ActionDispatchResult(ActionDispatchStatus.FAIL_CLOSED, "Total click quota exceeded", target_screen_pt=(screen_x, screen_y))

        # Per-region click quota check
        region_id = context.get("region_id")
        if region_id:
            reg_clicks = self._region_clicks.get(region_id, 0)
            if reg_clicks >= self.safety_config.max_clicks_per_region:
                logger.warning(f"Region '{region_id}' click quota exceeded ({reg_clicks} >= {self.safety_config.max_clicks_per_region}). Dropping action.")
                return ActionDispatchResult(ActionDispatchStatus.FAIL_CLOSED, f"Region click quota exceeded for '{region_id}'", target_screen_pt=(screen_x, screen_y))

        now = time.time()

        # Anti-runaway rate limiting: 1-second window
        clicks_1s = [t for t in self._recent_click_timestamps if now - t < 1.0]
        if len(clicks_1s) >= self.max_clicks_per_sec:
            logger.warning(f"Anti-Runaway triggered: Rate limit exceeded ({len(clicks_1s)} >= {self.max_clicks_per_sec}/sec). Dropping action.")
            return ActionDispatchResult(ActionDispatchStatus.FAIL_CLOSED, "Anti-Runaway rate limit exceeded", target_screen_pt=(screen_x, screen_y))

        # Prune circuit breaker window
        cb_window = self.safety_config.circuit_breaker_window_sec
        self._recent_click_timestamps = [t for t in self._recent_click_timestamps if now - t < cb_window]

        # Dispatch through verified non-physical backend
        raw_result = self.backend.dispatch_click(screen_x, screen_y, context)
        if isinstance(raw_result, ActionDispatchResult):
            result = raw_result
        elif bool(raw_result):
            result = ActionDispatchResult(ActionDispatchStatus.DISPATCHED, "Dispatched successfully", target_screen_pt=(screen_x, screen_y))
        else:
            result = ActionDispatchResult(ActionDispatchStatus.NOT_SENT, "Backend dispatch returned False", target_screen_pt=(screen_x, screen_y))

        if result.status == ActionDispatchStatus.DISPATCHED:
            self._recent_click_timestamps.append(now)
            self._total_clicks += 1
            if region_id:
                self._region_clicks[region_id] = self._region_clicks.get(region_id, 0) + 1
            if len(self._recent_click_timestamps) >= self.circuit_breaker_threshold:
                self._circuit_breaker_tripped = True
                logger.critical(f"ANTI-RUNAWAY CIRCUIT BREAKER TRIPPED! ({len(self._recent_click_timestamps)} actions in {cb_window} sec). Bot halted.")

        return result

    @property
    def total_clicks(self) -> int:
        return self._total_clicks
