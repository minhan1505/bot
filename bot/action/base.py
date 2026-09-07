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
from typing import Tuple, Dict, Any


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

    @abstractmethod
    def close(self):
        """Releases backend connections."""
        pass
