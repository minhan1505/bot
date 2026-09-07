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
from bot.action.base import BaseActionBackend
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
        max_clicks_per_second: float = 4.0,
        circuit_breaker_threshold: int = 20
    ):
        self.backend = backend
        self.max_clicks_per_sec = max_clicks_per_second
        self.circuit_breaker_threshold = circuit_breaker_threshold

        self.is_supported = False
        self.support_status_message = "NOT_PROBED"
        self.emergency_stop_triggered = False

        # Anti-runaway tracking
        self._recent_click_timestamps = []
        self._total_clicks = 0
        self._circuit_breaker_tripped = False

    def probe_and_bind(self, backend: BaseActionBackend, context: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Runs capability probe on target surface.
        Binds backend only if supported.
        """
        self.backend = backend
        supported, reason = self.backend.probe_capability(context)
        self.is_supported = supported
        self.support_status_message = reason

        if not supported:
            logger.error(f"Action capability probe failed: {reason}. Status: BACKGROUND_ACTION_UNSUPPORTED.")
            self.backend = None
        else:
            logger.info(f"Action capability probe passed: {reason}.")

        return self.is_supported, self.support_status_message

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
    ) -> bool:
        """
        Safely dispatches action subject to safety guards.
        """
        if self.emergency_stop_triggered:
            logger.warning("Action dispatch rejected: Emergency stop active.")
            return False

        if self._circuit_breaker_tripped:
            logger.warning("Action dispatch rejected: Circuit breaker tripped (Anti-Runaway).")
            return False

        if not self.backend or not self.is_supported:
            logger.error("Action dispatch rejected: BACKGROUND_ACTION_UNSUPPORTED. Physical mouse fallback is forbidden.")
            return False

        now = time.time()

        # Anti-runaway rate limiting
        # Prune clicks older than 1 second
        self._recent_click_timestamps = [t for t in self._recent_click_timestamps if now - t < 1.0]

        if len(self._recent_click_timestamps) >= self.max_clicks_per_sec:
            logger.warning(f"Anti-Runaway triggered: Rate limit exceeded ({len(self._recent_click_timestamps)} >= {self.max_clicks_per_sec}/sec). Dropping action.")
            return False

        # Dispatch through verified non-physical backend
        success = self.backend.dispatch_click(screen_x, screen_y, context)
        if success:
            self._recent_click_timestamps.append(now)
            self._total_clicks += 1
            if len(self._recent_click_timestamps) >= self.circuit_breaker_threshold:
                self._circuit_breaker_tripped = True
                logger.critical(f"ANTI-RUNAWAY CIRCUIT BREAKER TRIPPED! ({len(self._recent_click_timestamps)} actions in 1 sec). Bot halted.")

        return success

    @property
    def total_clicks(self) -> int:
        return self._total_clicks
