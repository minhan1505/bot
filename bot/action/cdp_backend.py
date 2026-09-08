"""
bot.action.cdp_backend
~~~~~~~~~~~~~~~~~~~~~~
Chrome DevTools Protocol (CDP) Action Backend.
Dispatches background mouse events directly into the browser/canvas rendering engine
without touching, seizing, or moving the physical Windows cursor.
"""

from __future__ import annotations
import json
import urllib.request
import asyncio
import websockets
from typing import Tuple, Dict, Any, Optional, List
import ctypes
import logging
from bot.action.base import BaseActionBackend, ActionDispatchResult, ActionDispatchStatus
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
        self.target_page_id: Optional[str] = None
        self.viewport_context: Optional[ViewportContext] = None
        self._msg_id: int = 0
        self._ws_connection = None

    def get_available_pages(self) -> List[Dict[str, Any]]:
        """Queries Chrome HTTP endpoint to locate all open page tabs."""
        try:
            url = f"http://{self.host}:{self.port}/json"
            req = urllib.request.Request(url, headers={"User-Agent": "BotV2-CDP"})
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                tabs = json.loads(resp.read().decode())
                pages = []
                for tab in tabs:
                    if tab.get("type") == "page" and "webSocketDebuggerUrl" in tab:
                        pages.append({
                            "id": tab.get("id"),
                            "title": tab.get("title", "(untitled)"),
                            "url": tab.get("url", ""),
                            "webSocketDebuggerUrl": tab["webSocketDebuggerUrl"]
                        })
                return pages
        except Exception as e:
            logger.debug(f"Failed to query CDP pages at {self.host}:{self.port}: {e}")
        return []

    def _get_page_ws_url(self, target_id: Optional[str] = None) -> Optional[str]:
        """Queries Chrome HTTP endpoint to locate active page websocket debugger URL."""
        target = target_id or self.target_page_id
        pages = self.get_available_pages()
        if not pages:
            return None
        if target:
            for p in pages:
                if p["id"] == target:
                    return p["webSocketDebuggerUrl"]
        # Default to first page if no explicit target ID specified
        return pages[0]["webSocketDebuggerUrl"]

    def probe_capability(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """
        End-to-End Capability Probe on target surface:
        1. Checks CDP connection.
        2. Probes target surface/canvas responsiveness.
        3. Verifies physical system cursor position has NOT moved during probe.
        """
        cursor_before = get_physical_cursor_pos()

        if "viewport_context" in context:
            self.viewport_context = context["viewport_context"]
        if "target_page_id" in context:
            self.target_page_id = context["target_page_id"]
        if "ws_url" in context:
            self.ws_url = context["ws_url"]

        # Step 1: Query page WebSocket
        ws_url = self.ws_url or self._get_page_ws_url()
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

    def invalidate_surface_context(self, reason: str = "context_invalidated") -> None:
        """
        Invalidates the bound ViewportContext due to window movement, resize, or DPR change.
        """
        logger.warning(f"CDPActionBackend ViewportContext invalidated: {reason}")
        self.viewport_context = None

    def verify_viewport_freshness(self, ctx: Optional[ViewportContext] = None) -> Tuple[bool, str]:
        """
        Verifies that target Chrome window/tab geometry and DPR have not drifted from ctx.
        If ctx is None, evaluates against self.viewport_context.
        Returns (True, 'FRESH') or (False, reason) and invalidates cached context on drift.
        """
        target_ctx = ctx or self.viewport_context
        if target_ctx is None:
            return False, "NO_VIEWPORT_CONTEXT_BOUND"

        ws_url = self.ws_url or self._get_page_ws_url()
        if not ws_url:
            self.invalidate_surface_context("CDP_DISCONNECTED")
            return False, "CDP_DISCONNECTED"

        try:
            async def _query_geom():
                async with websockets.connect(ws_url, close_timeout=1.5) as ws:
                    script = """
                    (() => {
                        const dpr = window.devicePixelRatio || 1.0;
                        const w = window.innerWidth;
                        const h = window.innerHeight;
                        const sx = window.screenX !== undefined ? window.screenX : window.screenLeft;
                        const sy = window.screenY !== undefined ? window.screenY : window.screenTop;
                        return { dpr: dpr, innerWidth: w, innerHeight: h, screenX: sx, screenY: sy };
                    })()
                    """
                    msg = {"id": 9001, "method": "Runtime.evaluate", "params": {"expression": script, "returnByValue": True}}
                    await ws.send(json.dumps(msg))
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.5)
                    res = json.loads(raw)
                    return res.get("result", {}).get("result", {}).get("value", {})

            current = asyncio.run(_query_geom())
            if not current:
                self.invalidate_surface_context("FAILED_TO_READ_GEOMETRY")
                return False, "FAILED_TO_READ_GEOMETRY"

            curr_dpr = float(current.get("dpr", 1.0))
            curr_w = int(current.get("innerWidth", 0))
            curr_h = int(current.get("innerHeight", 0))
            curr_sx = int(round(float(current.get("screenX", 0)) * curr_dpr))
            curr_sy = int(round(float(current.get("screenY", 0)) * curr_dpr))

            if abs(curr_dpr - target_ctx.device_pixel_ratio) > 1e-4:
                reason = f"DPR_DRIFT: recorded={target_ctx.device_pixel_ratio}, current={curr_dpr}"
                self.invalidate_surface_context(reason)
                return False, reason

            if int(round(target_ctx.inner_width)) != curr_w or int(round(target_ctx.inner_height)) != curr_h:
                reason = f"VIEWPORT_RESIZED: recorded={int(round(target_ctx.inner_width))}x{int(round(target_ctx.inner_height))}, current={curr_w}x{curr_h}"
                self.invalidate_surface_context(reason)
                return False, reason

            if target_ctx.window_rect.x != curr_sx or target_ctx.window_rect.y != curr_sy:
                reason = f"WINDOW_MOVED: recorded=({target_ctx.window_rect.x}, {target_ctx.window_rect.y}), current=({curr_sx}, {curr_sy})"
                self.invalidate_surface_context(reason)
                return False, reason

            return True, "FRESH"
        except Exception as e:
            reason = f"GEOMETRY_FRESHNESS_CHECK_ERROR: {e}"
            self.invalidate_surface_context(reason)
            return False, reason

    def dispatch_click(
        self,
        screen_x: int,
        screen_y: int,
        context: Dict[str, Any]
    ) -> ActionDispatchResult:
        """
        Dispatches background mousePressed + mouseReleased via CDP Input.dispatchMouseEvent.
        Returns ActionDispatchResult distinguishing DISPATCHED, NOT_SENT, UNCERTAIN.
        """
        # Convert Physical Screen Coordinates -> CSS Pixels if ViewportContext provided
        viewport_ctx: Optional[ViewportContext] = context.get("viewport_context") or self.viewport_context
        if context.get("is_production", False) and viewport_ctx is None:
            logger.error("Production CDP dispatch rejected: Missing ViewportContext. 1:1 fallback is strictly forbidden.")
            return ActionDispatchResult(
                ActionDispatchStatus.NOT_SENT,
                "Missing ViewportContext in production mode; 1:1 coordinate fallback is forbidden.",
                target_screen_pt=(screen_x, screen_y)
            )

        if not self.ws_url:
            self.ws_url = self._get_page_ws_url()
            if not self.ws_url:
                logger.error("Cannot dispatch click: CDP WebSocket URL is unavailable.")
                return ActionDispatchResult(ActionDispatchStatus.NOT_SENT, "CDP WebSocket URL is unavailable", target_screen_pt=(screen_x, screen_y))

        if context.get("verify_freshness", False) and viewport_ctx is not None:
            is_fresh, fresh_err = self.verify_viewport_freshness(viewport_ctx)
            if not is_fresh:
                logger.error(f"Production CDP dispatch rejected: Geometry freshness verification failed: {fresh_err}")
                return ActionDispatchResult(
                    ActionDispatchStatus.FAIL_CLOSED,
                    f"CDP_GEOMETRY_STALE: {fresh_err}",
                    target_screen_pt=(screen_x, screen_y)
                )

        if viewport_ctx:
            css_x, css_y = CoordinateMapper.screen_to_css_pixels(screen_x, screen_y, viewport_ctx)
            # Strict boundary check
            if not (0 <= css_x < viewport_ctx.inner_width and 0 <= css_y < viewport_ctx.inner_height):
                logger.error(f"Coordinates ({css_x}, {css_y}) out of viewport bounds ({viewport_ctx.inner_width}x{viewport_ctx.inner_height})")
                return ActionDispatchResult(
                    ActionDispatchStatus.NOT_SENT,
                    f"Coordinates ({css_x}, {css_y}) out of viewport bounds ({viewport_ctx.inner_width}x{viewport_ctx.inner_height})",
                    target_screen_pt=(screen_x, screen_y),
                    viewport_css_pt=(css_x, css_y)
                )
        else:
            css_x, css_y = float(screen_x), float(screen_y)

        pressed_down = False

        async def _recv_ack(ws, expected_id: int, timeout_sec: float = 0.100) -> Tuple[bool, str]:
            deadline = asyncio.get_event_loop().time() + timeout_sec
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    err = f"CDP ACK timeout for msg {expected_id}"
                    logger.error(err)
                    return False, err
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                    msg = json.loads(raw)
                    if msg.get("id") == expected_id:
                        if "error" in msg:
                            err = f"CDP command {expected_id} returned error: {msg['error']}"
                            logger.error(err)
                            return False, err
                        return True, ""
                    # Discard other asynchronous events from page
                except Exception as err:
                    err_msg = f"CDP recv error: {err}"
                    logger.error(err_msg)
                    return False, err_msg

        async def _send_click() -> ActionDispatchResult:
            nonlocal pressed_down
            async with websockets.connect(self.ws_url, close_timeout=1.0) as ws:
                # 1. mousePressed
                self._msg_id += 1
                press_id = self._msg_id
                press_msg = {
                    "id": press_id,
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
                pressed_down = True
                press_ok, press_err = await _recv_ack(ws, press_id)
                if not press_ok:
                    # mousePressed was already transmitted over WebSocket.
                    # Since physical click-down may have taken effect in Chrome,
                    # the outcome cannot be treated as NOT_SENT; it must be UNCERTAIN.
                    try:
                        self._msg_id += 1
                        emergency_release_msg = {
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
                        await ws.send(json.dumps(emergency_release_msg))
                    except Exception:
                        pass
                    return ActionDispatchResult(
                        ActionDispatchStatus.UNCERTAIN,
                        f"CDP mousePressed sent but ACK failed: {press_err}",
                        target_screen_pt=(screen_x, screen_y),
                        viewport_css_pt=(css_x, css_y)
                    )

                # Short inter-event delay (30ms)
                await asyncio.sleep(0.030)

                # 2. mouseReleased
                self._msg_id += 1
                release_id = self._msg_id
                release_msg = {
                    "id": release_id,
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
                release_ok, release_err = await _recv_ack(ws, release_id)
                if not release_ok:
                    # Attempt 1 emergency recovery release to clear stuck state
                    try:
                        await ws.send(json.dumps(release_msg))
                    except Exception:
                        pass
                    return ActionDispatchResult(
                        ActionDispatchStatus.UNCERTAIN,
                        f"CDP mousePressed succeeded but mouseReleased failed: {release_err}",
                        target_screen_pt=(screen_x, screen_y),
                        viewport_css_pt=(css_x, css_y)
                    )

                logger.info(f"CDP background click dispatched at CSS ({css_x:.1f}, {css_y:.1f})")
                return ActionDispatchResult(
                    ActionDispatchStatus.DISPATCHED,
                    "CDP click dispatched and acknowledged",
                    target_screen_pt=(screen_x, screen_y),
                    viewport_css_pt=(css_x, css_y)
                )

        try:
            return asyncio.run(_send_click())
        except Exception as e:
            logger.error(f"Failed to dispatch CDP click: {e}")
            if pressed_down:
                return ActionDispatchResult(
                    ActionDispatchStatus.UNCERTAIN,
                    f"CDP click exception after mousePressed: {e}",
                    target_screen_pt=(screen_x, screen_y),
                    viewport_css_pt=(css_x, css_y)
                )
            return ActionDispatchResult(
                ActionDispatchStatus.NOT_SENT,
                f"CDP click failed before mousePressed: {e}",
                target_screen_pt=(screen_x, screen_y),
                viewport_css_pt=(css_x, css_y)
            )

    def close(self):
        self.ws_url = None
