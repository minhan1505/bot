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

        # If production mode requested, require surface verification
        if context.get("is_production", False) and not self.is_surface_verified:
            logger.error("Action dispatch rejected: SURFACE_UNVERIFIED_ACTION_BLOCKED. Protocol ACK is not surface verification.")
            return False

        # Total click quota check
        if self._total_clicks >= self.safety_config.max_total_clicks:
            logger.critical(f"Total click quota exceeded ({self._total_clicks} >= {self.safety_config.max_total_clicks}). Halting.")
            self._circuit_breaker_tripped = True
            return False

        # Per-region click quota check
        region_id = context.get("region_id")
        if region_id:
            reg_clicks = self._region_clicks.get(region_id, 0)
            if reg_clicks >= self.safety_config.max_clicks_per_region:
                logger.warning(f"Region '{region_id}' click quota exceeded ({reg_clicks} >= {self.safety_config.max_clicks_per_region}). Dropping action.")
                return False

        now = time.time()

        # Anti-runaway rate limiting: 1-second window
        clicks_1s = [t for t in self._recent_click_timestamps if now - t < 1.0]
        if len(clicks_1s) >= self.max_clicks_per_sec:
            logger.warning(f"Anti-Runaway triggered: Rate limit exceeded ({len(clicks_1s)} >= {self.max_clicks_per_sec}/sec). Dropping action.")
            return False

        # Prune circuit breaker window
        cb_window = self.safety_config.circuit_breaker_window_sec
        self._recent_click_timestamps = [t for t in self._recent_click_timestamps if now - t < cb_window]

        # Dispatch through verified non-physical backend
        success = self.backend.dispatch_click(screen_x, screen_y, context)
        if success:
            self._recent_click_timestamps.append(now)
            self._total_clicks += 1
            if region_id:
                self._region_clicks[region_id] = self._region_clicks.get(region_id, 0) + 1
            if len(self._recent_click_timestamps) >= self.circuit_breaker_threshold:
                self._circuit_breaker_tripped = True
                logger.critical(f"ANTI-RUNAWAY CIRCUIT BREAKER TRIPPED! ({len(self._recent_click_timestamps)} actions in {cb_window} sec). Bot halted.")

        return success

    @property
    def total_clicks(self) -> int:
        return self._total_clicks
