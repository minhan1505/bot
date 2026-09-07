"""
bot.action.window_backend
~~~~~~~~~~~~~~~~~~~~~~~~~
Win32 Window Message Action Backend (PostMessage).
Dispatches background WM_LBUTTONDOWN / WM_LBUTTONUP directly to the target HWND
without modifying the physical system cursor.
"""

import time
import ctypes
from ctypes import wintypes
from typing import Tuple, Dict, Any, Optional
import logging
from bot.action.base import BaseActionBackend
from bot.action.cdp_backend import get_physical_cursor_pos
from bot.core.coordinates import CoordinateMapper, Rect

logger = logging.getLogger(__name__)

# Win32 Constants
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
WM_NCHITTEST = 0x0084


class WindowActionBackend(BaseActionBackend):
    """
    Win32 Background Messaging Backend via PostMessage.
    """

    def __init__(self, hwnd: Optional[int] = None):
        self.hwnd = hwnd

    def probe_capability(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Probes target HWND validity and responsiveness.
        Ensures system cursor is not modified.
        """
        cursor_before = get_physical_cursor_pos()
        target_hwnd = context.get("hwnd", self.hwnd)

        if not target_hwnd:
            return False, "WIN32_UNSUPPORTED: Target HWND not provided."

        user32 = ctypes.windll.user32
        if not user32.IsWindow(wintypes.HWND(target_hwnd)):
            return False, f"WIN32_UNSUPPORTED: HWND {target_hwnd} is not a valid window."

        # Probe message responsiveness using SendMessageTimeout
        l_result = wintypes.LPARAM()
        SMTO_ABORTIFHUNG = 0x0002
        # Send WM_NCHITTEST to probe if window thread is responsive
        res = user32.SendMessageTimeoutW(
            wintypes.HWND(target_hwnd),
            wintypes.UINT(WM_NCHITTEST),
            wintypes.WPARAM(0),
            wintypes.LPARAM(0),
            wintypes.UINT(SMTO_ABORTIFHUNG),
            wintypes.UINT(1000), # 1 sec timeout
            ctypes.byref(l_result)
        )

        if res == 0:
            return False, f"WIN32_PROBE_FAILED: Target HWND {target_hwnd} is hung or not responding to messages."

        cursor_after = get_physical_cursor_pos()
        if cursor_before != cursor_after:
            return False, f"MOUSE_INDEPENDENCE_VIOLATION: System cursor moved during probe ({cursor_before} -> {cursor_after})."

        self.hwnd = target_hwnd
        return True, "WIN32_POSTMESSAGE_SUPPORTED"

    def dispatch_click(
        self,
        screen_x: int,
        screen_y: int,
        context: Dict[str, Any]
    ) -> bool:
        """
        Sends WM_LBUTTONDOWN and WM_LBUTTONUP via PostMessage to HWND client area.
        """
        target_hwnd = context.get("hwnd", self.hwnd)
        if not target_hwnd:
            logger.error("Cannot dispatch click: HWND is not configured.")
            return False

        user32 = ctypes.windll.user32

        # Convert screen to client coordinates
        pt = wintypes.POINT(x=screen_x, y=screen_y)
        res_stc = user32.ScreenToClient(wintypes.HWND(target_hwnd), ctypes.byref(pt))
        if res_stc == 0:
            logger.error(f"ScreenToClient failed for HWND {target_hwnd}")
            return False
        client_x, client_y = pt.x, pt.y

        # Pack into lParam: low-order word = x, high-order word = y
        l_param = (client_y << 16) | (client_x & 0xFFFF)

        # 1. Post WM_LBUTTONDOWN
        res_down = user32.PostMessageW(
            wintypes.HWND(target_hwnd),
            wintypes.UINT(WM_LBUTTONDOWN),
            wintypes.WPARAM(MK_LBUTTON),
            wintypes.LPARAM(l_param)
        )
        if res_down == 0:
            logger.error(f"PostMessageW WM_LBUTTONDOWN failed for HWND {target_hwnd}")
            return False

        time.sleep(0.030) # 30ms click duration

        # 2. Post WM_LBUTTONUP
        res_up = user32.PostMessageW(
            wintypes.HWND(target_hwnd),
            wintypes.UINT(WM_LBUTTONUP),
            wintypes.WPARAM(0),
            wintypes.LPARAM(l_param)
        )
        if res_up == 0:
            logger.error(f"PostMessageW WM_LBUTTONUP failed for HWND {target_hwnd} after DOWN sent!")
            # 1 recovery attempt
            user32.PostMessageW(wintypes.HWND(target_hwnd), wintypes.UINT(WM_LBUTTONUP), wintypes.WPARAM(0), wintypes.LPARAM(l_param))
            return False

        logger.info(f"Win32 background click posted to HWND {target_hwnd} at client ({client_x}, {client_y})")
        return True

    def close(self):
        self.hwnd = None
