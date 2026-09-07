"""
bot.capture.manager
~~~~~~~~~~~~~~~~~~~
CaptureManager with automatic failover (DXGI -> MSS -> Mock).
Exposes unified physical-pixel frame grabbing and sub-ROI cropping.
"""

import time
from typing import Tuple, Optional
import numpy as np
import logging
from bot.capture.base import BaseCapture
from bot.capture.dxgi_capture import DXGICapture
from bot.capture.mss_capture import MSSCapture
from bot.core.coordinates import Rect

logger = logging.getLogger(__name__)


class CaptureManager:
    """Manages screen capture backends with graceful failover."""

    def __init__(self, monitor_index: int = 1, prefer_dxgi: bool = True):
        self.monitor_index = monitor_index
        self.prefer_dxgi = prefer_dxgi
        self.backend_name = "NONE"
        self._capture: Optional[BaseCapture] = None
        self._last_frame: Optional[np.ndarray] = None
        self._last_timestamp: float = 0.0
        self.init_backend()

    def init_backend(self):
        if self._capture:
            self._capture.close()
            self._capture = None

        if self.prefer_dxgi:
            try:
                self._capture = DXGICapture(device_idx=0, output_idx=max(0, self.monitor_index - 1))
                self.backend_name = "DXGI"
                logger.info("DXGI capture successfully initialized.")
                return
            except Exception as e:
                logger.info(f"DXGI capture unavailable ({e}). Falling back to MSS.")

        try:
            self._capture = MSSCapture(monitor_index=self.monitor_index)
            self.backend_name = "MSS"
            logger.info("MSS capture successfully initialized as fallback.")
        except Exception as e:
            logger.error(f"All capture backends failed: {e}")
            self.backend_name = "NONE"

    def set_mock_backend(self, mock_capture: BaseCapture):
        """Allows injecting a mock capture for headless tests / benchmarks."""
        if self._capture:
            self._capture.close()
        self._capture = mock_capture
        self.backend_name = "MOCK"

    def grab(self) -> Tuple[Optional[np.ndarray], float]:
        """
        Grabs physical BGR frame.
        Returns (frame, capture_timestamp).
        """
        if not self._capture:
            return None, time.perf_counter()

        frame, t_capture = self._capture.grab()
        if frame is not None:
            self._last_frame = frame
            self._last_timestamp = t_capture
        return frame, t_capture

    def grab_sub_roi(self, rect: Rect) -> Tuple[Optional[np.ndarray], float]:
        """
        Grabs and crops a sub-region (physical pixels) from the current frame.
        Useful for Fresh-Verify (FR-053).
        """
        frame, t_capture = self.grab()
        if frame is None:
            return None, t_capture

        h, w = frame.shape[:2]
        x1 = max(0, min(rect.x, w))
        y1 = max(0, min(rect.y, h))
        x2 = max(x1, min(rect.right, w))
        y2 = max(y1, min(rect.bottom, h))

        if x2 - x1 <= 0 or y2 - y1 <= 0:
            return None, t_capture

        crop = frame[y1:y2, x1:x2].copy()
        return crop, t_capture

    def get_desktop_offset(self) -> Tuple[int, int]:
        if self._capture:
            return self._capture.get_desktop_offset()
        return (0, 0)

    def get_dimensions(self) -> Tuple[int, int]:
        if self._capture:
            return self._capture.get_dimensions()
        return (0, 0)

    def close(self):
        if self._capture:
            self._capture.close()
            self._capture = None
            self.backend_name = "NONE"
