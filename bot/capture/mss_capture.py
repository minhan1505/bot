"""
bot.capture.mss_capture
~~~~~~~~~~~~~~~~~~~~~~~
MSS Screen Capture fallback backend.
Cross-compatible multi-monitor physical pixel capture.
"""

import time
from typing import Tuple, Optional
import numpy as np
import mss
import logging
from bot.capture.base import BaseCapture

logger = logging.getLogger(__name__)


class MSSCapture(BaseCapture):
    """MSS Screen Capture fallback."""

    def __init__(self, monitor_index: int = 1):
        self.monitor_index = monitor_index
        self._sct = mss.MSS()
        self._monitor = None
        self._offset_x = 0
        self._offset_y = 0
        self._width = 0
        self._height = 0
        self._init_monitor()

    def _init_monitor(self):
        monitors = self._sct.monitors
        if self.monitor_index >= len(monitors):
            logger.warning(f"Monitor index {self.monitor_index} out of range (total {len(monitors)}). Fallback to 1.")
            self.monitor_index = 1 if len(monitors) > 1 else 0
        self._monitor = monitors[self.monitor_index]
        self._offset_x = self._monitor["left"]
        self._offset_y = self._monitor["top"]
        self._width = self._monitor["width"]
        self._height = self._monitor["height"]
        logger.info(f"MSSCapture initialized: monitor {self.monitor_index} ({self._width}x{self._height} at offset ({self._offset_x}, {self._offset_y}))")

    def grab(self) -> Tuple[Optional[np.ndarray], float]:
        t_capture = time.perf_counter()
        try:
            shot = self._sct.grab(self._monitor)
            # shot is BGRA -> convert to BGR uint8
            frame_bgra = np.frombuffer(shot.raw, dtype=np.uint8).reshape((shot.height, shot.width, 4))
            frame_bgr = frame_bgra[:, :, :3]
            return frame_bgr, t_capture
        except Exception as e:
            logger.debug(f"MSS grab failed: {e}")
            return None, t_capture

    def get_desktop_offset(self) -> Tuple[int, int]:
        return (self._offset_x, self._offset_y)

    def get_dimensions(self) -> Tuple[int, int]:
        return (self._width, self._height)

    def close(self):
        if self._sct:
            self._sct.close()
            self._sct = None
