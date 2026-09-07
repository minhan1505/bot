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
from typing import Callable, Optional
import logging

logger = logging.getLogger(__name__)

# Win32 Constants
WM_HOTKEY = 0x0312
VK_F12 = 0x77
MOD_NOREPEAT = 0x4000


class GlobalHotkeyManager:
    """
    Manages system-wide global hotkeys on Windows via background message pump.
    """

    def __init__(self, vk_code: int = VK_F12, on_triggered: Optional[Callable[[], None]] = None):
        self.vk_code = vk_code
        self.on_triggered = on_triggered
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._stop_event = threading.Event()
        self._hotkey_id = 101
        self.is_registered = False

    def start(self):
        """Starts background hotkey message loop thread."""
        if sys.platform != "win32":
            logger.info("Non-Windows platform: Global hotkey disabled.")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()

    def _message_loop(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()

        # Register system-wide hotkey
        res = user32.RegisterHotKey(
            None,
            self._hotkey_id,
            MOD_NOREPEAT,
            self.vk_code
        )

        if not res:
            logger.warning(f"Failed to register global hotkey VK={hex(self.vk_code)}. Error: {ctypes.GetLastError()}")
            self.is_registered = False
            return

        self.is_registered = True
        logger.info(f"Global Emergency Hotkey (F12) successfully registered system-wide.")

        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            # Peek/Get message with 50ms timeout
            has_msg = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1) # PM_REMOVE = 1
            if has_msg:
                if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                    logger.critical("GLOBAL HOTKEY F12 TRIGGERED! Halting all bot operations.")
                    if self.on_triggered:
                        try:
                            self.on_triggered()
                        except Exception as e:
                            logger.error(f"Error in hotkey trigger callback: {e}")
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.02)

        # Unregister hotkey before exiting
        user32.UnregisterHotKey(None, self._hotkey_id)
        self.is_registered = False
        logger.info("Global hotkey unregistered.")

    def stop(self):
        """Stops background hotkey thread."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None
