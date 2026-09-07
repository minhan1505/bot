"""
bot.capture.base
~~~~~~~~~~~~~~~~
Abstract Base Capture interface.
All capture implementations must return frames in Physical BGR format
along with precise monotonic capture timestamps and desktop offsets.
"""

from abc import ABC, abstractmethod
from typing import Tuple, Optional
import numpy as np


class BaseCapture(ABC):
    """Abstract interface for screen capture backends."""

    @abstractmethod
    def grab(self) -> Tuple[Optional[np.ndarray], float]:
        """
        Grabs a physical-pixel screen frame.
        Returns:
            frame: np.ndarray (H, W, 3) in BGR format, or None if grab failed.
            capture_timestamp: float (monotonic time of frame acquisition).
        """
        pass

    @abstractmethod
    def get_desktop_offset(self) -> Tuple[int, int]:
        """
        Returns (offset_x, offset_y) of the captured monitor relative to
        the virtual desktop origin (0, 0). Supports negative offsets on multi-monitor.
        """
        pass

    @abstractmethod
    def get_dimensions(self) -> Tuple[int, int]:
        """Returns (width, height) in physical pixels."""
        pass

    @abstractmethod
    def close(self):
        """Releases capture resources."""
        pass
