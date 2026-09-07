"""
bot.core.dpi
~~~~~~~~~~~~
Windows Per-Monitor DPI Awareness V2 initialization.
Ensures capture, overlay, and click systems operate in a unified physical-pixel coordinate space.
"""

import sys
import ctypes
import logging

logger = logging.getLogger(__name__)

# Windows DPI Constants
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE = -3


def enable_dpi_awareness_v2() -> bool:
    """
    Enables Windows Per-Monitor DPI Awareness V2.
    Must be called BEFORE creating any QApplication or window instance.
    Returns True if successfully set, False otherwise.
    """
    if sys.platform != "win32":
        logger.info("Non-Windows platform detected. DPI awareness call skipped.")
        return False

    try:
        user32 = ctypes.windll.user32

        # Try SetProcessDpiAwarenessContext (Windows 10 1703+)
        if hasattr(user32, "SetProcessDpiAwarenessContext"):
            res = user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2))
            if res:
                logger.info("SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2) succeeded.")
                return True
            else:
                logger.warning("V2 failed, attempting PER_MONITOR_AWARE (V1)...")
                res = user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE))
                if res:
                    logger.info("SetProcessDpiAwarenessContext(PER_MONITOR_AWARE) succeeded.")
                    return True

        # Fallback to SetProcessDpiAwareness in shcore (Windows 8.1+)
        try:
            shcore = ctypes.windll.shcore
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            hr = shcore.SetProcessDpiAwareness(2)
            if hr == 0:
                logger.info("shcore.SetProcessDpiAwareness(2) succeeded.")
                return True
        except Exception as e:
            logger.debug(f"shcore.SetProcessDpiAwareness failed: {e}")

        # Final fallback to user32.SetProcessDPIAware (Windows Vista+)
        if hasattr(user32, "SetProcessDPIAware"):
            user32.SetProcessDPIAware()
            logger.info("user32.SetProcessDPIAware() fallback succeeded.")
            return True

    except Exception as exc:
        logger.error(f"Failed to enable Windows DPI awareness: {exc}", exc_info=True)

    return False
