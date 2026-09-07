"""
bot.action.cdp_backend
~~~~~~~~~~~~~~~~~~~~~~
Chrome DevTools Protocol (CDP) Action Backend.
Dispatches background mouse events directly into the browser/canvas rendering engine
without touching, seizing, or moving the physical Windows cursor.
"""

import json
import urllib.request
import asyncio
import websockets
from typing import Tuple, Dict, Any, Optional
import ctypes
import logging
from bot.action.base import BaseActionBackend
from bot.core.coordinates import CoordinateMapper, ViewportContext

logger = logging.getLogger(__name__)


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_physical_cursor_pos() -> Tuple[int, int]:
    """Queries current physical system cursor position via Win32."""
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


class CDPActionBackend(BaseActionBackend):
    """
    Background Action Backend via Chrome DevTools Protocol.
    """

    def __init__(self, host: str = "localhost", port: int = 9222):
        self.host = host
        self.port = port
        self.ws_url: Optional[str] = None
        self._msg_id: int = 0
        self._ws_connection = None

    def _get_page_ws_url(self) -> Optional[str]:
        """Queries Chrome HTTP endpoint to locate active page websocket debugger URL."""
        try:
            url = f"http://{self.host}:{self.port}/json"
            req = urllib.request.Request(url, headers={"User-Agent": "BotV2-CDP"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                tabs = json.loads(resp.read().decode())
                for tab in tabs:
                    if tab.get("type") == "page" and "webSocketDebuggerUrl" in tab:
                        return tab["webSocketDebuggerUrl"]
        except Exception as e:
            logger.debug(f"Failed to query CDP pages at {self.host}:{self.port}: {e}")
        return None

    def probe_capability(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """
        End-to-End Capability Probe on target surface:
        1. Checks CDP connection.
        2. Probes target surface/canvas responsiveness.
        3. Verifies physical system cursor position has NOT moved during probe.
        """
        cursor_before = get_physical_cursor_pos()

        # Step 1: Query page WebSocket
        ws_url = self._get_page_ws_url()
        if not ws_url:
            return False, f"CDP_UNAVAILABLE: Cannot connect to Chrome at http://{self.host}:{self.port}/json. Ensure Chrome is running with --remote-debugging-port={self.port}."

        # Step 2: Test non-destructive roundtrip via WebSocket
        try:
            async def _run_probe():
                async with websockets.connect(ws_url, close_timeout=2.0) as ws:
                    # Send Runtime.evaluate to probe document state
                    msg = {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {"expression": "document.readyState"}
                    }
                    await ws.send(json.dumps(msg))
                    raw_res = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    res = json.loads(raw_res)
                    ready_state = res.get("result", {}).get("result", {}).get("value")
                    return ready_state in ("interactive", "complete")

            supported = asyncio.run(_run_probe())
            if not supported:
                return False, "CDP_PROBE_FAILED: Target document did not report interactive/complete state."

        except Exception as exc:
            return False, f"CDP_COMMUNICATION_ERROR: {exc}"

        # Step 3: Verify system cursor was not disturbed
        cursor_after = get_physical_cursor_pos()
        if cursor_before != cursor_after:
            return False, f"MOUSE_INDEPENDENCE_VIOLATION: System cursor moved from {cursor_before} to {cursor_after} during probe."

        self.ws_url = ws_url
        return True, "CDP_SUPPORTED_BACKGROUND"

    def dispatch_click(
        self,
        screen_x: int,
        screen_y: int,
        context: Dict[str, Any]
    ) -> bool:
        """
        Dispatches background mousePressed + mouseReleased via CDP Input.dispatchMouseEvent.
        """
        if not self.ws_url:
            self.ws_url = self._get_page_ws_url()
            if not self.ws_url:
                logger.error("Cannot dispatch click: CDP WebSocket URL is unavailable.")
                return False

        # Convert Physical Screen Coordinates -> CSS Pixels if ViewportContext provided
        viewport_ctx: Optional[ViewportContext] = context.get("viewport_context")
        if viewport_ctx:
            css_x, css_y = CoordinateMapper.screen_to_css_pixels(screen_x, screen_y, viewport_ctx)
        else:
            # Assume 1:1 if no context
            css_x, css_y = float(screen_x), float(screen_y)

        async def _send_click():
            async with websockets.connect(self.ws_url, close_timeout=1.0) as ws:
                # 1. mousePressed
                self._msg_id += 1
                press_msg = {
                    "id": self._msg_id,
                    "method": "Input.dispatchMouseEvent",
                    "params": {
                        "type": "mousePressed",
                        "x": css_x,
                        "y": css_y,
                        "button": "left",
                        "clickCount": 1
                    }
                }
                await ws.send(json.dumps(press_msg))
                await ws.recv()

                # Short inter-event delay (30ms)
                await asyncio.sleep(0.030)

                # 2. mouseReleased
                self._msg_id += 1
                release_msg = {
                    "id": self._msg_id,
                    "method": "Input.dispatchMouseEvent",
                    "params": {
                        "type": "mouseReleased",
                        "x": css_x,
                        "y": css_y,
                        "button": "left",
                        "clickCount": 1
                    }
                }
                await ws.send(json.dumps(release_msg))
                await ws.recv()

        try:
            asyncio.run(_send_click())
            logger.info(f"CDP background click dispatched at CSS ({css_x:.1f}, {css_y:.1f})")
            return True
        except Exception as e:
            logger.error(f"Failed to dispatch CDP click: {e}")
            return False

    def close(self):
        self.ws_url = None
