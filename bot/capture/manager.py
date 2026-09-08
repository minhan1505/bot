"""
bot.capture.manager
~~~~~~~~~~~~~~~~~~~
CaptureManager with automatic failover (DXGI -> MSS -> Mock).
Exposes unified physical-pixel frame grabbing and sub-ROI cropping.
"""

import time
from typing import Tuple, Optional, List, Dict, Any
import numpy as np
import logging
import mss
from bot.capture.base import BaseCapture
from bot.capture.dxgi_capture import DXGICapture
from bot.capture.mss_capture import MSSCapture
from bot.core.coordinates import Rect

logger = logging.getLogger(__name__)


class CaptureManager:
    """Manages screen capture backends with graceful failover, monitor selection, and ROI."""

    def __init__(self, monitor_index: int = 1, prefer_dxgi: bool = True):
        self.monitor_index = monitor_index
        self.prefer_dxgi = prefer_dxgi
        self.backend_name = "NONE"
        self._capture: Optional[BaseCapture] = None
        self._last_frame: Optional[np.ndarray] = None
        self._last_timestamp: float = 0.0
        self.roi: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h) in monitor-local pixels
        self.init_backend()

    @staticmethod
    def enumerate_monitors() -> List[Dict[str, Any]]:
        """
        Enumerates all physical monitors and the virtual desktop composite (FC-02).
        Returns list of dicts with index, name, left, top, width, height.
        """
        monitors_list = []
        try:
            with mss.mss() as sct:
                for idx, m in enumerate(sct.monitors):
                    name = "All Monitors (Virtual Desktop)" if idx == 0 else f"Monitor {idx}"
                    monitors_list.append({
                        "index": idx,
                        "name": name,
                        "left": m["left"],
                        "top": m["top"],
                        "width": m["width"],
                        "height": m["height"]
                    })
        except Exception as e:
            logger.error(f"Failed to enumerate monitors via MSS: {e}")
            monitors_list.append({
                "index": 1,
                "name": "Primary Display (Fallback)",
                "left": 0,
                "top": 0,
                "width": 1920,
                "height": 1080
            })
        return monitors_list

    def set_monitor(self, monitor_index: int):
        """Switches active monitor and reinitializes backend safely (FC-02)."""
        self.monitor_index = monitor_index
        self.init_backend()

    def validate_roi(self, roi: Tuple[int, int, int, int]) -> Tuple[bool, str]:
        """
        Strictly validates ROI bounds against capture dimensions (FC-03).
        Rejects out-of-bounds rather than silently clipping.
        """
        if not roi or len(roi) != 4:
            return False, "ROI_INVALID: Must be a 4-tuple (x, y, w, h)"
        x, y, w, h = roi
        if w <= 0 or h <= 0:
            return False, f"ROI_OUT_OF_BOUNDS: Width ({w}) and height ({h}) must be > 0"
        if x < 0 or y < 0:
            return False, f"ROI_OUT_OF_BOUNDS: Origin ({x}, {y}) cannot be negative"

        dim_w, dim_h = (0, 0)
        if self._capture:
            dim_w, dim_h = self._capture.get_dimensions()

        if dim_w > 0 and dim_h > 0:
            if x + w > dim_w or y + h > dim_h:
                return False, f"ROI_OUT_OF_BOUNDS: ROI ({x}, {y}, {w}, {h}) exceeds monitor bounds ({dim_w}x{dim_h})"

        return True, "ROI_VALID"

    def set_roi(self, roi: Optional[Tuple[int, int, int, int]]) -> Tuple[bool, str]:
        """Sets or clears scan scope ROI (FC-03)."""
        if roi is None:
            self.roi = None
            return True, "ROI_CLEARED"

        valid, err = self.validate_roi(roi)
        if not valid:
            logger.error(f"Failed to set ROI: {err}")
            return False, err

        self.roi = roi
        logger.info(f"Scan scope ROI set to: {roi}")
        return True, "ROI_SET"

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
        Grabs physical BGR frame, cropped to ROI if configured (FC-03).
        Returns (frame, capture_timestamp).
        """
        if not self._capture:
            return None, time.perf_counter()

        frame, t_capture = self._capture.grab()
        if frame is None:
            return None, t_capture

        if self.roi is not None:
            rx, ry, rw, rh = self.roi
            h, w = frame.shape[:2]
            if rx + rw > w or ry + rh > h or rx < 0 or ry < 0:
                logger.error(f"ROI ({self.roi}) out of bounds for frame ({w}x{h}). Failing capture.")
                return None, t_capture
            frame = frame[ry:ry + rh, rx:rx + rw].copy()

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
        base_x, base_y = (0, 0)
        if self._capture:
            base_x, base_y = self._capture.get_desktop_offset()
        if self.roi is not None:
            return (base_x + self.roi[0], base_y + self.roi[1])
        return (base_x, base_y)

    def get_dimensions(self) -> Tuple[int, int]:
        if self.roi is not None:
            return (self.roi[2], self.roi[3])
        if self._capture:
            return self._capture.get_dimensions()
        return (0, 0)

    def close(self):
        if self._capture:
            self._capture.close()
            self._capture = None
            self.backend_name = "NONE"
