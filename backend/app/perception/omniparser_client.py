from __future__ import annotations

from typing import Any, Dict, Optional

import base64
import cv2
import numpy as np
import requests


class OmniParserClient:
    def __init__(self, base_url: str, timeout_s: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)

    def parse_bgr(
        self,
        bgr: np.ndarray,
        *,
        run_caption: bool = True,
        box_threshold: float = 0.15,
        iou_threshold: float = 0.30,
        use_paddleocr: bool = True,
        imgsz: int = 640,
        # keep for backward compat; if provided it overrides imgsz
        icon_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        if bgr is None or not hasattr(bgr, "shape"):
            raise ValueError("bgr must be a numpy image")

        ok, buf = cv2.imencode(".png", bgr)
        if not ok:
            raise RuntimeError("Failed to encode image")

        if icon_size is not None:
            imgsz = int(icon_size)

        params = {
            "run_caption": "true" if run_caption else "false",
            "box_threshold": str(float(box_threshold)),
            "iou_threshold": str(float(iou_threshold)),
            "use_paddleocr": "true" if use_paddleocr else "false",
            "imgsz": str(int(imgsz)),
        }

        files = {"image": ("frame.png", buf.tobytes(), "image/png")}
        url = f"{self.base_url}/parse"
        r = requests.post(url, params=params, files=files, timeout=self.timeout_s)

        if r.status_code != 200:
            raise requests.HTTPError(f"{r.status_code} {r.reason}: {r.text}", response=r)

        data = r.json()
        if not isinstance(data, dict):
            raise ValueError("Invalid response: not a JSON object")

        # normalize output fields
        data.setdefault("items", data.get("parsed", []))
        data.setdefault("parsed_text", "")
        data.setdefault("overlay_jpeg_b64", None)
        data.setdefault("meta", {})

        return data

    @staticmethod
    def overlay_b64_to_bytes(data: Dict[str, Any]) -> Optional[bytes]:
        b64 = data.get("overlay_jpeg_b64")
        if not b64:
            return None
        return base64.b64decode(b64)

    @staticmethod
    def overlay_b64_to_bgr(data: Dict[str, Any]) -> Optional[np.ndarray]:
        raw = OmniParserClient.overlay_b64_to_bytes(data)
        if raw is None:
            return None
        arr = np.frombuffer(raw, np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img
