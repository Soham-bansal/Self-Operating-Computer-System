from __future__ import annotations
import time
import uuid
from dataclasses import dataclass
from typing import Optional, Dict, Any

import mss
import numpy as np
from PIL import Image

from .win_meta import get_cursor_pos, get_active_window_info

@dataclass
class Frame:
    frame_id: str
    ts: float
    rgb: Image.Image                 # full virtual desktop RGB
    virtual_bbox: Dict[str, int]     # {left, top, width, height}
    screen_w: int
    screen_h: int
    cursor: Dict[str, int]
    active_app: Dict[str, Any]
    active_window_bbox: Optional[list[int]]  # [x,y,w,h] in screen coords
    active_window_rgb: Optional[Image.Image] # cropped image


def _rect_to_bbox(l: int, t: int, r: int, b: int) -> list[int]:
    return [l, t, max(0, r - l), max(0, b - t)]


def _intersect(a: list[int], b: list[int]) -> Optional[list[int]]:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2 - x1, y2 - y1]


def capture_fullscreen(monitor_index: int = 0) -> Frame:
    """
    monitor_index=0 means: full virtual desktop (all monitors).
    """
    frame_id = str(uuid.uuid4())
    ts = time.time()

    with mss.mss() as sct:
        monitors = sct.monitors  # [0]=virtual desktop, [1..]=individual monitors
        mon = monitors[monitor_index]

        # mss returns BGRA; use .rgb for RGB bytes
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.rgb)

        cursor_x, cursor_y = get_cursor_pos()
        aw = get_active_window_info()

        active_bbox = None
        active_crop = None

        if not aw.minimized:
            # window rect is in screen coords (same coordinate space as virtual desktop)
            l, t, r, b = aw.rect
            win_bbox = _rect_to_bbox(l, t, r, b)

            # intersect with captured area (virtual desktop bbox)
            cap_bbox = [mon["left"], mon["top"], mon["width"], mon["height"]]
            inter = _intersect(win_bbox, cap_bbox)
            if inter is not None:
                # crop requires coordinates relative to captured image origin
                rel_x = inter[0] - mon["left"]
                rel_y = inter[1] - mon["top"]
                rel_r = rel_x + inter[2]
                rel_b = rel_y + inter[3]
                active_crop = img.crop((rel_x, rel_y, rel_r, rel_b))
                active_bbox = inter

    return Frame(
        frame_id=frame_id,
        ts=ts,
        rgb=img,
        virtual_bbox={"left": mon["left"], "top": mon["top"], "width": mon["width"], "height": mon["height"]},
        screen_w=int(mon["width"]),
        screen_h=int(mon["height"]),
        cursor={"x": int(cursor_x), "y": int(cursor_y)},
        active_app={"title": aw.title, "pid": aw.pid, "process": aw.process_name, "hwnd": int(aw.hwnd)},
        active_window_bbox=active_bbox,
        active_window_rgb=active_crop,
    )
