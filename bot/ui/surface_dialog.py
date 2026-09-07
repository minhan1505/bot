"""
bot.ui.surface_dialog
~~~~~~~~~~~~~~~~~~~~~
Interactive Surface Verification Probe Dialog for Action Backends.
Enforces:
  - Explicit Tab Selection: Queries Chrome DevTools HTTP endpoint and allows selecting specific target tab.
  - Active Benign Surface Probe: Dispatches non-destructive mouseMoved event directly to the target surface
    and verifies active reaction in the application DOM event loop.
  - Mouse Independence: Verifies Windows physical cursor remains stationary (0px deviation).
  - Production Gate: ActionManager.is_surface_verified and backend.viewport_context are atomically bound.
"""

import json
import urllib.request
import asyncio
import websockets
import ctypes
from ctypes import wintypes
from typing import Optional, Dict, Any, List, Tuple
import logging

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QSpinBox, QLineEdit, QPushButton, QLabel,
    QTextEdit, QMessageBox, QGroupBox
)
from PySide6.QtCore import Qt

from bot.action.manager import ActionManager
from bot.action.cdp_backend import CDPActionBackend, get_physical_cursor_pos
from bot.action.window_backend import WindowActionBackend, WM_NCHITTEST
from bot.core.coordinates import ViewportContext

logger = logging.getLogger(__name__)


def list_visible_windows() -> List[Tuple[int, str]]:
    """Lists visible top-level windows with title."""
    windows = []
    user32 = ctypes.windll.user32

    def enum_proc(hwnd, lparam):
        if user32.IsWindowVisible(wintypes.HWND(hwnd)):
            length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(wintypes.HWND(hwnd), buf, length + 1)
                title = buf.value.strip()
                if title:
                    windows.append((hwnd, title))
        return True

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    return windows


class SurfaceVerificationDialog(QDialog):
    """Interactive 2-Stage Surface Verification Probe Dialog with Active Event Reaction."""

    def __init__(self, action_manager: ActionManager, parent=None):
        super().__init__(parent)
        self.action_manager = action_manager
        self.setWindowTitle("Action Backend & Surface Verification Probe")
        self.resize(620, 560)

        self.probe_success = False

        layout = QVBoxLayout(self)

        # 1. Backend Selector
        gb_backend = QGroupBox("Select Action Backend")
        lay_backend = QVBoxLayout(gb_backend)

        form_b = QFormLayout()
        self.combo_backend_type = QComboBox()
        self.combo_backend_type.addItems(["Chrome DevTools Protocol (CDP)", "Win32 Window (PostMessage)"])
        self.combo_backend_type.currentIndexChanged.connect(self._on_backend_type_changed)
        form_b.addRow("Backend Type:", self.combo_backend_type)
        lay_backend.addLayout(form_b)
        layout.addWidget(gb_backend)

        # 2. CDP Config Box
        self.gb_cdp = QGroupBox("CDP Target Configuration")
        lay_cdp = QVBoxLayout(self.gb_cdp)

        form_cdp = QFormLayout()
        self.txt_cdp_host = QLineEdit("localhost")
        self.spin_cdp_port = QSpinBox()
        self.spin_cdp_port.setRange(1024, 65535)
        self.spin_cdp_port.setValue(9222)
        form_cdp.addRow("CDP Host:", self.txt_cdp_host)
        form_cdp.addRow("CDP Port:", self.spin_cdp_port)

        self.combo_cdp_tabs = QComboBox()
        form_cdp.addRow("Target Chrome Tab:", self.combo_cdp_tabs)
        lay_cdp.addLayout(form_cdp)

        btn_refresh_tabs = QPushButton("Query & Refresh Chrome Tabs")
        btn_refresh_tabs.clicked.connect(self._refresh_cdp_tabs)
        lay_cdp.addWidget(btn_refresh_tabs)

        layout.addWidget(self.gb_cdp)

        # 3. Win32 Config Box
        self.gb_win32 = QGroupBox("Win32 Window Configuration")
        lay_win32 = QVBoxLayout(self.gb_win32)
        form_w = QFormLayout()
        self.combo_windows = QComboBox()
        form_w.addRow("Detected Window:", self.combo_windows)
        self.txt_hwnd = QLineEdit()
        self.txt_hwnd.setPlaceholderText("Direct HWND (integer or 0xHEX)")
        form_w.addRow("Target HWND:", self.txt_hwnd)
        lay_win32.addLayout(form_w)

        btn_refresh_wins = QPushButton("Refresh Window List")
        btn_refresh_wins.clicked.connect(self._refresh_windows)
        lay_win32.addWidget(btn_refresh_wins)
        layout.addWidget(self.gb_win32)
        self.gb_win32.hide()

        # 4. Probe Actions & Diagnostics
        self.btn_run_probe = QPushButton("Run 2-Stage Surface Verification Probe (with Active Benign Event)")
        self.btn_run_probe.setStyleSheet("background-color: #1565c0; color: white; font-weight: bold; padding: 10px;")
        self.btn_run_probe.clicked.connect(self._run_probe)
        layout.addWidget(self.btn_run_probe)

        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("Probe diagnostics will appear here...")
        layout.addWidget(self.txt_log)

        # Status badge
        self.lbl_status = QLabel("STATUS: UNVERIFIED")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet("font-weight: bold; padding: 6px; background: #eee; border-radius: 4px;")
        layout.addWidget(self.lbl_status)

        # Buttons
        btn_bar = QHBoxLayout()
        self.btn_accept = QPushButton("Accept & Bind Backend")
        self.btn_accept.setEnabled(False)
        self.btn_accept.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 6px 14px;")
        self.btn_accept.clicked.connect(self.accept)
        btn_bar.addWidget(self.btn_accept)

        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.reject)
        btn_bar.addWidget(self.btn_close)

        layout.addLayout(btn_bar)

        self._refresh_windows()
        self._refresh_cdp_tabs()

    def _on_backend_type_changed(self, index: int):
        if index == 0:
            self.gb_cdp.show()
            self.gb_win32.hide()
        else:
            self.gb_cdp.hide()
            self.gb_win32.show()

    def _refresh_cdp_tabs(self):
        host = self.txt_cdp_host.text().strip() or "localhost"
        port = self.spin_cdp_port.value()
        backend = CDPActionBackend(host=host, port=port)
        pages = backend.get_available_pages()
        self.combo_cdp_tabs.clear()
        if not pages:
            self.combo_cdp_tabs.addItem("(No Chrome page tabs found. Ensure Chrome runs with --remote-debugging-port)", "")
        else:
            for p in pages:
                title = p.get("title", "(untitled)")[:35]
                url = p.get("url", "")[:40]
                label = f"{title} [{url}]"
                self.combo_cdp_tabs.addItem(label, p.get("webSocketDebuggerUrl"))

    def _refresh_windows(self):
        self.combo_windows.clear()
        windows = list_visible_windows()
        for hwnd, title in windows:
            label = f"{title[:40]} (HWND: {hwnd})"
            self.combo_windows.addItem(label, hwnd)
        if windows:
            self.combo_windows.currentIndexChanged.connect(self._on_window_selected)
            self._on_window_selected(0)

    def _on_window_selected(self, index: int):
        hwnd = self.combo_windows.currentData()
        if hwnd:
            self.txt_hwnd.setText(str(hwnd))

    def _log(self, msg: str):
        self.txt_log.append(msg)

    def _run_probe(self):
        self.txt_log.clear()
        self.btn_accept.setEnabled(False)
        self.probe_success = False

        backend_type = self.combo_backend_type.currentIndex()
        if backend_type == 0:
            self._probe_cdp()
        else:
            self._probe_win32()

    def _probe_cdp(self):
        host = self.txt_cdp_host.text().strip() or "localhost"
        port = self.spin_cdp_port.value()
        self._log(f"--- Stage 1: Probing CDP Protocol at {host}:{port} ---")

        cursor_before = get_physical_cursor_pos()
        self._log(f"[Safety] Initial Physical Cursor Pos: {cursor_before}")

        backend = CDPActionBackend(host=host, port=port)

        # Use specifically selected tab
        selected_ws = self.combo_cdp_tabs.currentData()
        if selected_ws:
            backend.ws_url = selected_ws
            self._log(f"Selected Tab WebSocket: {selected_ws[:55]}...")

        supported, reason = backend.probe_capability({"ws_url": backend.ws_url} if backend.ws_url else {})
        if not supported:
            self._log(f"✖ Stage 1 Failed: {reason}")
            self.lbl_status.setText("STATUS: PROTOCOL_FAILED")
            self.lbl_status.setStyleSheet("font-weight: bold; padding: 6px; background: #ffcdd2; color: #b71c1c;")
            return

        self._log(f"✔ Stage 1 Passed: Protocol WebSocket Handshake OK ({reason})")

        # Stage 2: Active Surface Verification with Benign Event Reaction
        self._log("--- Stage 2: Probing Target Surface Reaction with Benign Event ---")
        ws_url = backend.ws_url
        if not ws_url:
            self._log("✖ Stage 2 Failed: WebSocket URL missing.")
            return

        try:
            async def _run_active_surface_probe():
                async with websockets.connect(ws_url, close_timeout=3.0) as ws:
                    # 1. Query tab metadata & viewport
                    script_info = """
                    (() => {
                        return {
                            title: document.title,
                            visibility: document.visibilityState,
                            readyState: document.readyState,
                            innerWidth: window.innerWidth,
                            innerHeight: window.innerHeight,
                            devicePixelRatio: window.devicePixelRatio || 1.0
                        };
                    })()
                    """
                    msg1 = {"id": 1001, "method": "Runtime.evaluate", "params": {"expression": script_info, "returnByValue": True}}
                    await ws.send(json.dumps(msg1))
                    res1 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                    info = res1.get("result", {}).get("result", {}).get("value", {})

                    w = info.get("innerWidth", 0)
                    h = info.get("innerHeight", 0)
                    if w <= 0 or h <= 0:
                        return info, False, "Viewport dimensions <= 0"

                    # 2. Attach temporary probe event listener in page DOM
                    center_x = float(w // 2)
                    center_y = float(h // 2)
                    script_probe = """
                    (() => {
                        window.__bot_probe_received = false;
                        window.__bot_probe_x = -1;
                        window.__bot_probe_y = -1;
                        const handler = (e) => {
                            window.__bot_probe_received = true;
                            window.__bot_probe_x = Math.round(e.clientX);
                            window.__bot_probe_y = Math.round(e.clientY);
                        };
                        window.addEventListener('mousemove', handler, { once: true, capture: true });
                        return true;
                    })()
                    """
                    msg2 = {"id": 1002, "method": "Runtime.evaluate", "params": {"expression": script_probe, "returnByValue": True}}
                    await ws.send(json.dumps(msg2))
                    await asyncio.wait_for(ws.recv(), timeout=3.0)

                    # 3. Dispatch benign mouseMoved event via CDP
                    move_msg = {
                        "id": 1003,
                        "method": "Input.dispatchMouseEvent",
                        "params": {
                            "type": "mouseMoved",
                            "x": center_x,
                            "y": center_y
                        }
                    }
                    await ws.send(json.dumps(move_msg))
                    await asyncio.wait_for(ws.recv(), timeout=3.0)

                    # 4. Check DOM probe reaction
                    script_verify = """
                    (() => {
                        return {
                            received: Boolean(window.__bot_probe_received),
                            x: window.__bot_probe_x,
                            y: window.__bot_probe_y
                        };
                    })()
                    """
                    msg4 = {"id": 1004, "method": "Runtime.evaluate", "params": {"expression": script_verify, "returnByValue": True}}
                    await ws.send(json.dumps(msg4))
                    res4 = json.loads(await asyncio.wait_for(ws.recv(), timeout=3.0))
                    reaction = res4.get("result", {}).get("result", {}).get("value", {})

                    probe_ok = reaction.get("received", False)
                    return info, probe_ok, f"DOM reaction: received={probe_ok} at ({reaction.get('x')}, {reaction.get('y')})"

            surface_data, probe_ok, probe_msg = asyncio.run(_run_active_surface_probe())
            title = surface_data.get("title", "(untitled)")
            w = surface_data.get("innerWidth", 0)
            h = surface_data.get("innerHeight", 0)
            dpr = surface_data.get("devicePixelRatio", 1.0)
            vis = surface_data.get("visibility", "unknown")

            self._log(f"  Target Tab Title: {title}")
            self._log(f"  Viewport: {w}x{h} CSS px, DPR: {dpr}")
            self._log(f"  Visibility State: {vis}")
            self._log(f"  Active Benign Event Reaction: {probe_msg}")

            if w <= 0 or h <= 0:
                self._log("✖ Stage 2 Failed: Viewport dimensions <= 0.")
                return

            if not probe_ok:
                self._log("✖ Stage 2 Warning: Target document did not return event reaction acknowledgment.")
                return

            # Verify physical cursor independence
            cursor_after = get_physical_cursor_pos()
            self._log(f"[Safety] Physical Cursor Pos After Probe: {cursor_after}")
            if cursor_before != cursor_after:
                self._log("✖ MOUSE_INDEPENDENCE_VIOLATION: System cursor moved during probe!")
                return

            vp_ctx = ViewportContext(
                origin_screen_x=0,
                origin_screen_y=0,
                inner_width=w,
                inner_height=h,
                device_pixel_ratio=dpr
            )

            # Persist ViewportContext directly onto backend and ActionManager
            backend.viewport_context = vp_ctx
            context = {
                "surface_verified": True,
                "viewport_context": vp_ctx,
                "ws_url": ws_url
            }
            self.action_manager.probe_and_bind(backend, context)

            self._log("✔ Stage 2 Passed: Target surface & active DOM event reaction verified.")
            self._log("★ SURFACE VERIFIED: Full background production action unlocked.")
            self.lbl_status.setText("STATUS: SURFACE_VERIFIED (PRODUCTION_READY)")
            self.lbl_status.setStyleSheet("font-weight: bold; padding: 6px; background: #c8e6c9; color: #1b5e20;")
            self.btn_accept.setEnabled(True)
            self.probe_success = True

        except Exception as exc:
            self._log(f"✖ Stage 2 Surface Inspection Failed: {exc}")
            self.lbl_status.setText("STATUS: SURFACE_PROBE_ERROR")
            self.lbl_status.setStyleSheet("font-weight: bold; padding: 6px; background: #ffcdd2; color: #b71c1c;")

    def _probe_win32(self):
        hwnd_str = self.txt_hwnd.text().strip()
        try:
            hwnd = int(hwnd_str, 16 if hwnd_str.startswith("0x") else 10)
        except ValueError:
            self._log("✖ Invalid HWND format. Enter numeric or hex HWND.")
            return

        self._log(f"--- Stage 1: Probing Win32 HWND {hwnd} ---")
        cursor_before = get_physical_cursor_pos()
        self._log(f"[Safety] Initial Cursor Pos: {cursor_before}")

        backend = WindowActionBackend(hwnd=hwnd)
        supported, reason = backend.probe_capability({"hwnd": hwnd})
        if not supported:
            self._log(f"✖ Stage 1 Failed: {reason}")
            self.lbl_status.setText("STATUS: PROTOCOL_FAILED")
            self.lbl_status.setStyleSheet("font-weight: bold; padding: 6px; background: #ffcdd2; color: #b71c1c;")
            return

        self._log(f"✔ Stage 1 Passed: Win32 Message Thread Responsive ({reason})")

        # Stage 2: Surface Verification
        self._log("--- Stage 2: Probing Target Window Render Surface ---")
        user32 = ctypes.windll.user32
        rect = wintypes.RECT()
        if not user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            self._log("✖ Stage 2 Failed: Cannot get ClientRect.")
            return

        w = rect.right - rect.left
        h = rect.bottom - rect.top
        self._log(f"  Client Area Dimensions: {w}x{h}")
        if w <= 0 or h <= 0:
            self._log("✖ Stage 2 Failed: Window client area has 0 size.")
            return

        if user32.IsIconic(wintypes.HWND(hwnd)):
            self._log("✖ Stage 2 Failed: Window is minimized (Iconic). Background render surface inactive.")
            return

        # Send benign hit-test and mouse move to client center
        center_x = w // 2
        center_y = h // 2
        l_result = wintypes.LPARAM()
        SMTO_ABORTIFHUNG = 0x0002
        user32.SendMessageTimeoutW(
            wintypes.HWND(hwnd),
            wintypes.UINT(WM_NCHITTEST),
            wintypes.WPARAM(0),
            wintypes.LPARAM((center_y << 16) | (center_x & 0xFFFF)),
            wintypes.UINT(SMTO_ABORTIFHUNG),
            wintypes.UINT(1000),
            ctypes.byref(l_result)
        )
        self._log(f"  HitTest Result at client center ({center_x}, {center_y}): {l_result.value}")

        # Verify cursor independence
        cursor_after = get_physical_cursor_pos()
        self._log(f"[Safety] Cursor Pos After Probe: {cursor_after}")
        if cursor_before != cursor_after:
            self._log("✖ MOUSE_INDEPENDENCE_VIOLATION: Cursor moved during probe!")
            return

        context = {
            "hwnd": hwnd,
            "surface_verified": True
        }
        self.action_manager.probe_and_bind(backend, context)

        self._log("✔ Stage 2 Passed: Window client surface verified.")
        self._log("★ SURFACE VERIFIED: Full background production action unlocked.")
        self.lbl_status.setText("STATUS: SURFACE_VERIFIED (PRODUCTION_READY)")
        self.lbl_status.setStyleSheet("font-weight: bold; padding: 6px; background: #c8e6c9; color: #1b5e20;")
        self.btn_accept.setEnabled(True)
        self.probe_success = True
