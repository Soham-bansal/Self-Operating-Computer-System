from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

from .dpi import enable_dpi_awareness
from .capture import capture_fullscreen, Frame
from .schema_v2 import ScreenSchemaV2, BBox, Point, ActiveApp
from .visualize import save_debug_artifacts
from .omniparser_client import OmniParserClient


def _hash_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def _clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v


def _crop_active_window(
    full_img: Image.Image,
    *,
    virtual_left: int,
    virtual_top: int,
    aw_bbox: Tuple[int, int, int, int] | None,
) -> tuple[Image.Image, int, int]:
    if not aw_bbox:
        return full_img, virtual_left, virtual_top

    aw_x, aw_y, aw_w, aw_h = (int(aw_bbox[0]), int(aw_bbox[1]), int(aw_bbox[2]), int(aw_bbox[3]))
    if aw_w <= 16 or aw_h <= 16:
        return full_img, virtual_left, virtual_top

    img_w, img_h = full_img.size
    left = aw_x - virtual_left
    top = aw_y - virtual_top
    right = left + aw_w
    bottom = top + aw_h

    left_c = _clamp(int(left), 0, img_w)
    top_c = _clamp(int(top), 0, img_h)
    right_c = _clamp(int(right), 0, img_w)
    bottom_c = _clamp(int(bottom), 0, img_h)

    if right_c - left_c <= 16 or bottom_c - top_c <= 16:
        return full_img, virtual_left, virtual_top

    crop = full_img.crop((left_c, top_c, right_c, bottom_c))
    return crop, aw_x, aw_y


def _omni_bbox_to_screen_xywh(
    bbox_xyxy: Any,
    *,
    crop_w0: int,   # ORIGINAL crop width (before resize)
    crop_h0: int,   # ORIGINAL crop height (before resize)
    sent_w: int,    # SENT image width
    sent_h: int,    # SENT image height
    base_x: int,
    base_y: int,
) -> Optional[Tuple[int, int, int, int]]:
    """
    OmniParser bbox may be:
      - normalized ratio xyxy in [0..1] relative to the image it processed
      - OR pixel xyxy relative to the sent image (depends on repo/fork/settings)

    We convert to ABSOLUTE SCREEN xywh.
    IMPORTANT:
      If bbox is ratio, apply it to ORIGINAL crop size (crop_w0/h0),
      not the downscaled sent size.
    """
    if not (isinstance(bbox_xyxy, list) and len(bbox_xyxy) == 4):
        return None

    try:
        x1, y1, x2, y2 = [float(v) for v in bbox_xyxy]
    except Exception:
        return None

    # Heuristic: if bbox values look like pixels (e.g., > 1.5), treat as pixel coords
    is_pixel = max(x1, y1, x2, y2) > 1.5

    if not is_pixel:
        # ratio coords: clamp 0..1 and apply to ORIGINAL crop size
        x1 = max(0.0, min(1.0, x1))
        y1 = max(0.0, min(1.0, y1))
        x2 = max(0.0, min(1.0, x2))
        y2 = max(0.0, min(1.0, y2))

        px1 = int(round(x1 * crop_w0))
        py1 = int(round(y1 * crop_h0))
        px2 = int(round(x2 * crop_w0))
        py2 = int(round(y2 * crop_h0))
    else:
        # pixel coords relative to SENT image -> scale back to ORIGINAL crop size
        if sent_w <= 0 or sent_h <= 0:
            return None
        sx = crop_w0 / float(sent_w)
        sy = crop_h0 / float(sent_h)

        px1 = int(round(x1 * sx))
        py1 = int(round(y1 * sy))
        px2 = int(round(x2 * sx))
        py2 = int(round(y2 * sy))

    w = max(0, px2 - px1)
    h = max(0, py2 - py1)
    if w <= 0 or h <= 0:
        return None

    return (base_x + px1, base_y + py1, w, h)



class ScreenPerceptor:
    def __init__(
        self,
        *,
        enable_omni: bool = True,
        omni_url: str = "http://127.0.0.1:8010",
        monitor_index: int = 0,
        run_caption: bool = True,
        box_threshold: float = 0.05,
        iou_threshold: float = 0.10,
        max_side: int = 1920,
        use_paddleocr: bool = True,
        icon_size: int = 1920,
    ):
        enable_dpi_awareness()

        self.monitor_index = int(monitor_index)
        self.run_caption = bool(run_caption)
        self.box_threshold = float(box_threshold)
        self.iou_threshold = float(iou_threshold)
        self.max_side = int(max_side)
        self.use_paddleocr = bool(use_paddleocr)
        self.icon_size = int(icon_size)

        self._client: Optional[OmniParserClient] = None
        if enable_omni:
            self._client = OmniParserClient(str(omni_url).rstrip("/"))

        self._last_hash: Optional[str] = None
        self._last_schema: Optional[ScreenSchemaV2] = None

    def invalidate_cache(self) -> None:
        self._last_hash = None
        self._last_schema = None

    def perceive(self) -> tuple[ScreenSchemaV2, Frame, Dict[str, float]]:
        t0 = time.time()
        frame = capture_fullscreen(monitor_index=self.monitor_index)
        t_cap = time.time()

        vleft = int(frame.virtual_bbox["left"])
        vtop = int(frame.virtual_bbox["top"])

        # Use full screen — do NOT crop to active window.
        # Cropping excludes the taskbar and other system UI which the agent needs
        # to find the Start button, search bar, system tray, etc.
        crop_img = frame.rgb
        base_x, base_y = vleft, vtop

        # Downscale crop for speed (server sees exactly what we send)
        crop_w0, crop_h0 = crop_img.size
        if max(crop_w0, crop_h0) > self.max_side:
            scale = self.max_side / float(max(crop_w0, crop_h0))
            nw = max(1, int(crop_w0 * scale))
            nh = max(1, int(crop_h0 * scale))
            crop_img_send = crop_img.resize((nw, nh), Image.BILINEAR)
        else:
            crop_img_send = crop_img

        # cache by bytes we send
        h = _hash_bytes(crop_img_send.tobytes())
        if self._last_hash == h and self._last_schema is not None:
            return self._last_schema, frame, {
                "capture_ms": (t_cap - t0) * 1000.0,
                "ocr_ms": 0.0,
                "total_ms": (time.time() - t0) * 1000.0,
                "cached": 1.0,
            }

        if self._client is None:
            raise RuntimeError("Omni disabled but perceive() expects omni outputs")

        # PIL -> BGR numpy
        rgb = np.asarray(crop_img_send)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        t_om0 = time.time()
        resp = self._client.parse_bgr(
            bgr,
            run_caption=self.run_caption,
            box_threshold=self.box_threshold,
            iou_threshold=self.iou_threshold,
            use_paddleocr=self.use_paddleocr,
            icon_size=self.icon_size,
        )
        t_om1 = time.time()

        # items may be in resp["items"] or resp["parsed"]
        items = resp.get("items", None)
        if not isinstance(items, list):
            items = resp.get("parsed", [])
        if not isinstance(items, list):
            items = []

        sent_w, sent_h = crop_img_send.size

        # ✅ Keep Omni output simple, but add ABS screen bbox for drawing + downstream use
        fixed_items: List[Dict[str, Any]] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            bb = it.get("bbox")
            crop_w0, crop_h0 = crop_img.size
            sent_w, sent_h = crop_img_send.size
            xywh = _omni_bbox_to_screen_xywh(
                bb,
                crop_w0=crop_w0,
                crop_h0=crop_h0,
                sent_w=sent_w,
                sent_h=sent_h,
                base_x=base_x,
                base_y=base_y,
            )
            if xywh is None:
                continue

            it2 = dict(it)
            it2["bbox_abs_xywh"] = [int(xywh[0]), int(xywh[1]), int(xywh[2]), int(xywh[3])]
            fixed_items.append(it2)

        meta = resp.get("meta", {})
        if not isinstance(meta, dict):
            meta = {}

        schema = ScreenSchemaV2(
            frame_id=frame.frame_id,
            ts=float(frame.ts),
            virtual_bbox=BBox(
                x=int(frame.virtual_bbox["left"]),
                y=int(frame.virtual_bbox["top"]),
                w=int(frame.virtual_bbox["width"]),
                h=int(frame.virtual_bbox["height"]),
            ),
            screen_w=int(frame.screen_w),
            screen_h=int(frame.screen_h),
            cursor=Point(x=int(frame.cursor["x"]), y=int(frame.cursor["y"])),
            active_app=ActiveApp(
                title=str(frame.active_app["title"]),
                pid=int(frame.active_app["pid"]),
                process=str(frame.active_app["process"]),
            ),
            active_window_bbox=(
                BBox(
                    x=int(frame.active_window_bbox[0]),
                    y=int(frame.active_window_bbox[1]),
                    w=int(frame.active_window_bbox[2]),
                    h=int(frame.active_window_bbox[3]),
                )
                if frame.active_window_bbox
                else None
            ),

            # keep empty — we’re embedding omni directly
            ocr_tokens=[],
            elements=[],
            focus=None,

            # ✅ embedded omni output
            omni_items=fixed_items,
            omni_meta=meta,
        )

        save_debug_artifacts(schema, frame.rgb)

        self._last_hash = h
        self._last_schema = schema

        server_ms = float(schema.omni_meta.get("ms_total", 0.0) or 0.0)

        return schema, frame, {
            "capture_ms": (t_cap - t0) * 1000.0,
            "ocr_ms": server_ms,
            "total_ms": (time.time() - t0) * 1000.0,
            "cached": 0.0,
            "omni_call_ms": (t_om1 - t_om0) * 1000.0,
        }
