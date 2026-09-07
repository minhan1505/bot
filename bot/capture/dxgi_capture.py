"""
bot.capture.dxgi_capture
~~~~~~~~~~~~~~~~~~~~~~~~
DXGI Desktop Duplication capture using dxcam.
Achieves ultra-low latency (< 16ms) physical-pixel screen capture.
"""

import time
from typing import Tuple, Optional
import numpy as np
import logging
from bot.capture.base import BaseCapture

logger = logging.getLogger(__name__)


class DXGICapture(BaseCapture):
    """DXGI Screen Capture using dxcam."""

    def __init__(self, device_idx: int = 0, output_idx: int = 0):
        self.device_idx = device_idx
        self.output_idx = output_idx
        self._camera = None
        self._width = 0
        self._height = 0
        self._offset_x = 0
        self._offset_y = 0
        self._init_camera()

    def _init_camera(self):
        try:
            import dxcam
            self._camera = dxcam.create(
                device_idx=self.device_idx,
                output_idx=self.output_idx,
                output_color="BGR"
            )
            # Query output bounds
            if self._camera and hasattr(self._camera, "_output"):
                desc = self._camera._output.GetDesc()
                self._width = desc.DesktopCoordinates.right - desc.DesktopCoordinates.left
                self._height = desc.DesktopCoordinates.bottom - desc.DesktopCoordinates.top
                self._offset_x = desc.DesktopCoordinates.left
                self._offset_y = desc.DesktopCoordinates.top
            logger.info(f"DXGICapture initialized: {self._width}x{self._height} at ({self._offset_x}, {self._offset_y})")
        except Exception as e:
            logger.warning(f"Failed to initialize DXGICapture: {e}")
            self._camera = None
            raise

    def grab(self) -> Tuple[Optional[np.ndarray], float]:
        if not self._camera:
            return None, time.perf_counter()
        t_capture = time.perf_counter()
        frame = self._camera.grab()
        return frame, t_capture

    def get_desktop_offset(self) -> Tuple[int, int]:
        return (self._offset_x, self._offset_y)

    def get_dimensions(self) -> Tuple[int, int]:
        return (self._width, self._height)

    def close(self):
        if self._camera:
            try:
                del self._camera
            except Exception:
                pass
            self._camera = None
