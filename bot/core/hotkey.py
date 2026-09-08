"""
bot.core.hotkey
~~~~~~~~~~~~~~~
Global Emergency Stop Hotkey Engine (FR-074).
Registers a system-wide hotkey (default F12) via Windows Win32 API.
Ensures emergency stop triggers instantaneously even when the bot window
is minimized, hidden, or completely unfocused.
"""

import sys
import time
import ctypes
from ctypes import wintypes
import threading
from typing import Callable, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Win32 Constants
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# Supported virtual key codes
VK_MAP = {
    "F8": 0x77,
    "F9": 0x78,
    "F10": 0x79,
    "F11": 0x7A,
    "F12": 0x7B,
    "PAUSE": 0x13,
    "ESCAPE": 0x1B,
}

MOD_MAP = {
    "CTRL": MOD_CONTROL,
    "ALT": MOD_ALT,
    "SHIFT": MOD_SHIFT,
}


def parse_hotkey_string(hotkey_str: str) -> Tuple[int, int]:
    """
    Parses hotkey string (e.g. 'F12', 'Ctrl+F10', 'Ctrl+Alt+F9') into (vk_code, modifiers).
    """
    parts = [p.strip().upper() for p in hotkey_str.split("+") if p.strip()]
    if not parts:
        return VK_MAP["F12"], MOD_NOREPEAT

    mods = MOD_NOREPEAT
    vk = VK_MAP["F12"]

    for p in parts:
        if p in MOD_MAP:
            mods |= MOD_MAP[p]
        elif p in VK_MAP:
            vk = VK_MAP[p]
        else:
            logger.warning(f"Unknown hotkey component: {p}")

    return vk, mods


class GlobalHotkeyManager:
    """
    Manages system-wide global hotkeys on Windows via background message pump.
    Guarantees strict STOP-ONLY semantics (FC-01).
    """

    def __init__(
        self,
        hotkey_str: str = "F12",
        on_triggered: Optional[Callable[[], None]] = None
    ):
        self.hotkey_str = hotkey_str
        self.vk_code, self.modifiers = parse_hotkey_string(hotkey_str)
        self.on_triggered = on_triggered
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._stop_event = threading.Event()
        self._hotkey_id = 101
        self.is_registered = False
        self.last_error = ""
        self._last_trigger_time = 0.0

    def start(self) -> Tuple[bool, str]:
        """Starts background hotkey message loop thread."""
        if sys.platform != "win32":
            logger.info("Non-Windows platform: Global hotkey disabled.")
            self.is_registered = False
            return False, "NON_WINDOWS"

        self.stop()
        self._stop_event.clear()
        reg_ready = threading.Event()
        reg_result = {"success": False, "error": ""}

        def _thread_target():
            self._message_loop(reg_ready, reg_result)

        self._thread = threading.Thread(target=_thread_target, daemon=True)
        self._thread.start()

        # Wait up to 1.5s for registration confirmation
        reg_ready.wait(timeout=1.5)
        return reg_result["success"], reg_result["error"]

    def update_hotkey(self, hotkey_str: str) -> Tuple[bool, str]:
        """
        Dynamically updates hotkey (FC-01, U02).
        Unregisters previous hotkey, registers new hotkey, and returns status.
        If registration fails (e.g. HOTKEY_CONFLICT), safely restores the previous hotkey
        binding so emergency stop capability is never lost.
        """
        new_vk, new_mods = parse_hotkey_string(hotkey_str)
        old_str = self.hotkey_str
        old_vk = self.vk_code
        old_mods = self.modifiers
        was_alive = self.is_alive()

        self.hotkey_str = hotkey_str
        self.vk_code = new_vk
        self.modifiers = new_mods

        if was_alive or self.is_registered:
            success, err = self.start()
            if not success:
                logger.error(f"HOTKEY_CONFLICT: Failed to register hotkey '{hotkey_str}'. Reverting to '{old_str}'.")
                # Revert fields to old binding
                self.hotkey_str = old_str
                self.vk_code = old_vk
                self.modifiers = old_mods
                # Re-activate old hotkey to guarantee fail-safe emergency stop remains online
                rollback_success, rollback_err = self.start()
                if not rollback_success:
                    self.is_registered = False
                    self.last_error = "HOTKEY_ROLLBACK_FAILED"
                    logger.critical(
                        f"HOTKEY_UNBOUND: Both new hotkey '{hotkey_str}' and original hotkey '{old_str}' failed registration. "
                        f"Rollback error: {rollback_err}"
                    )
                    return False, "HOTKEY_ROLLBACK_FAILED"
                self.last_error = err or "HOTKEY_CONFLICT"
                return False, self.last_error
            return True, "HOTKEY_REGISTERED"
        return True, "HOTKEY_UPDATED"

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _message_loop(self, reg_ready: threading.Event, reg_result: dict):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        # Unregister in case previously registered on this id
        try:
            user32.UnregisterHotKey(None, self._hotkey_id)
        except Exception:
            pass

        # Register system-wide hotkey
        res = user32.RegisterHotKey(
            None,
            self._hotkey_id,
            self.modifiers,
            self.vk_code
        )

        if not res:
            err_code = ctypes.GetLastError()
            err_msg = "HOTKEY_CONFLICT"
            logger.warning(f"Failed to register global hotkey '{self.hotkey_str}' (VK={hex(self.vk_code)}). Error: {err_code} ({err_msg})")
            self.is_registered = False
            self.last_error = err_msg
            reg_result["success"] = False
            reg_result["error"] = err_msg
            reg_ready.set()
            return

        self.is_registered = True
        self.last_error = ""
        reg_result["success"] = True
        reg_result["error"] = ""
        reg_ready.set()
        logger.info(f"Global Emergency Hotkey ({self.hotkey_str}) registered system-wide.")

        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            has_msg = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1) # PM_REMOVE = 1
            if has_msg:
                if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                    now = time.time()
                    # Debounce duplicate messages within 300ms (MOD_NOREPEAT handles key repeat, debounce handles jitter)
                    if now - self._last_trigger_time > 0.300:
                        self._last_trigger_time = now
                        logger.critical(f"GLOBAL EMERGENCY HOTKEY ({self.hotkey_str}) TRIGGERED! Initiating STOP ONLY.")
                        if self.on_triggered:
                            try:
                                self.on_triggered()
                            except Exception as e:
                                logger.error(f"Error in emergency stop callback: {e}")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.02)

        # Unregister hotkey before exiting
        try:
            user32.UnregisterHotKey(None, self._hotkey_id)
        except Exception:
            pass
        self.is_registered = False
        logger.info("Global hotkey unregistered.")

    def stop(self):
        """Stops background hotkey thread cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
