from __future__ import annotations
import ctypes

# Per-monitor v2 DPI awareness context value (winuser.h)
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

def enable_dpi_awareness() -> None:
    """
    Best-effort: make the process DPI aware (Per-Monitor v2).
    Must be called early in process startup.
    """
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        return
    except Exception:
        pass

    # Fallbacks
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass

    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
