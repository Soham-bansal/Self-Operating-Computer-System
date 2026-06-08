from __future__ import annotations

import inspect
import time
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import JSONResponse
from PIL import Image
import base64
import io

# OmniParser utilities (from Microsoft OmniParser repo)
from util.utils import (
    get_yolo_model,
    get_caption_model_processor,
    get_som_labeled_img,
    check_ocr_box,
)

app = FastAPI()

YOLO_MODEL: Any = None
CAPTION_PROC: Any = None


@app.get("/health")
def health() -> dict:
    # This route only starts responding AFTER the startup event finishes loading the
    # models, so a 200 here means the perception server is fully ready.
    return {"ok": True, "yolo": YOLO_MODEL is not None}


def _pil_from_upload(raw: bytes) -> Image.Image:
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("bad_image")
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _normalize_parsed(parsed: Any) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Normalize OmniParser output into list[dict].
    Returns (items, internal_error)
    """
    if isinstance(parsed, list):
        out: List[Dict[str, Any]] = []
        for it in parsed:
            if isinstance(it, dict):
                out.append(it)
        return out, None

    # some failures we return dict with error
    if isinstance(parsed, dict) and "_error" in parsed:
        return [], str(parsed.get("_error"))

    return [], f"Unexpected parsed type: {type(parsed).__name__}"


def _overlay_base64_to_jpeg_b64(overlay_b64: Any) -> Optional[str]:
    if not overlay_b64:
        return None
    if not isinstance(overlay_b64, str):
        return None
    try:
        raw = base64.b64decode(overlay_b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        return base64.b64encode(out.getvalue()).decode("utf-8")
    except Exception:
        return None


def _call_get_som_labeled_img_compat(
    pil_img: Image.Image,
    *,
    yolo_model: Any,
    caption_proc: Any,
    run_caption: bool,
    ocr_bbox: Optional[List[Any]],
    ocr_text: Optional[List[str]],
    box_threshold: float,
    iou_threshold: float,
    use_paddleocr: bool,
    icon_size: int,
) -> Tuple[Optional[Any], Any]:
    """
    Call get_som_labeled_img in a version-tolerant way WITHOUT breaking OCR.
    """
    sig = inspect.signature(get_som_labeled_img)
    params = sig.parameters

    kwargs: Dict[str, Any] = {}

    # thresholds (OmniParser sometimes uses BOX_TRESHOLD typo)
    if "box_threshold" in params:
        kwargs["box_threshold"] = float(box_threshold)
    if "BOX_TRESHOLD" in params:
        kwargs["BOX_TRESHOLD"] = float(box_threshold)

    if "iou_threshold" in params:
        kwargs["iou_threshold"] = float(iou_threshold)

    # coordinate mode (most useful for your client)
    if "output_coord_in_ratio" in params:
        kwargs["output_coord_in_ratio"] = True

    # paddle OCR enable flag (name differs across forks)
    for key in ("use_paddleocr", "use_paddle_ocr", "paddleocr", "use_ocr"):
        if key in params:
            kwargs[key] = bool(use_paddleocr)
            break

    # icon detect image size (name differs)
    for key in ("icon_detect_img_size", "icon_detect_image_size", "icon_det_img_size", "img_size"):
        if key in params:
            kwargs[key] = int(icon_size)
            break

    # caption toggle (name differs across forks — use_local_semantics is the actual param name)
    for key in ("use_local_semantics", "use_caption", "run_caption", "enable_caption", "caption"):
        if key in params:
            kwargs[key] = bool(run_caption)
            break

    # caption processor injection (IMPORTANT: keep available even if run_caption=False,
    # because some versions still touch it)
    if "caption_model_processor" in params:
        kwargs["caption_model_processor"] = caption_proc

    # Some forks accept caption_model directly
    if "caption_model" in params and isinstance(caption_proc, dict):
        kwargs["caption_model"] = caption_proc.get("model")

    # ✅ DO NOT FORCE ocr_bbox/ocr_text to [] (this kills OCR)
    # Instead, let OmniParser compute them. If OmniParser internally returns None and crashes,
    # that is an upstream bug; but you already fixed the crash and now you want OCR back.
    if "ocr_bbox" in params and ocr_bbox is not None:
        kwargs["ocr_bbox"] = ocr_bbox
    if "ocr_text" in params and ocr_text is not None:
        kwargs["ocr_text"] = ocr_text

    try:
        # Try 3-arg positional form first (many repos require caption_proc positionally)
        try:
            res = get_som_labeled_img(pil_img, yolo_model, caption_proc, **kwargs)
        except TypeError:
            # fallback to 2-arg form
            res = get_som_labeled_img(pil_img, yolo_model, **kwargs)

        if isinstance(res, tuple):
            if len(res) == 3:
                overlay, _label_coords, parsed = res
                return overlay, parsed
            if len(res) == 2:
                overlay, parsed = res
                return overlay, parsed
        return None, {"_error": f"Unexpected return from get_som_labeled_img: {type(res).__name__}"}

    except Exception as e:
        return None, {"_error": f"{type(e).__name__}: {e}"}


@app.on_event("startup")
def _startup() -> None:
    global YOLO_MODEL, CAPTION_PROC

    YOLO_MODEL = get_yolo_model("weights/icon_detect/model.pt")

    # Florence-2 caption model — only needed when run_caption=True.
    # We always call with run_caption=False so this is optional.
    # Wrapped in try/except so a transformers version mismatch doesn't kill the server.
    try:
        CAPTION_PROC = get_caption_model_processor(
            model_name="florence2",
            model_name_or_path="weights/icon_caption_florence",
        )
        print("[OmniParser] Florence-2 caption model loaded.")
    except Exception as e:
        print(f"[OmniParser] WARNING: Florence-2 failed to load ({type(e).__name__}: {e})")
        print("[OmniParser] run_caption=True will be unavailable. run_caption=False works fine.")
        CAPTION_PROC = None


@app.post("/parse")
async def parse(
    image: UploadFile = File(...),
    run_caption: bool = Query(default=False),
    box_threshold: float = Query(default=0.15),
    iou_threshold: float = Query(default=0.30),
    use_paddleocr: bool = Query(default=True),
    # Match Gradio param name while still allowing icon_size for API clients.
    imgsz: int = Query(default=640),
    icon_size: Optional[int] = Query(default=None),
):
    t0 = time.time()

    try:
        raw = await image.read()
        pil_img = _pil_from_upload(raw)
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"items": [], "parsed": [], "meta": {"error": f"{type(e).__name__}: {e}"}},
        )

    try:
        (ocr_text, ocr_bbox), _ = check_ocr_box(
            pil_img,
            display_img=False,
            output_bb_format="xyxy",
            goal_filtering=None,
            easyocr_args={"paragraph": False, "text_threshold": 0.9},
            use_paddleocr=use_paddleocr,
        )
    except Exception:
        ocr_text, ocr_bbox = [], []

    effective_icon_size = int(icon_size) if icon_size is not None else int(imgsz)

    # If Florence-2 failed to load, silently downgrade run_caption to False
    effective_run_caption = run_caption and (CAPTION_PROC is not None)

    overlay, parsed_raw = _call_get_som_labeled_img_compat(
        pil_img,
        yolo_model=YOLO_MODEL,
        caption_proc=CAPTION_PROC,
        run_caption=effective_run_caption,
        ocr_bbox=ocr_bbox,
        ocr_text=ocr_text,
        box_threshold=box_threshold,
        iou_threshold=iou_threshold,
        use_paddleocr=use_paddleocr,
        icon_size=effective_icon_size,
    )

    items, internal_error = _normalize_parsed(parsed_raw)
    parsed_text = "\n".join([f"icon {i}: {v}" for i, v in enumerate(items)])
    overlay_jpeg_b64 = _overlay_base64_to_jpeg_b64(overlay)

    return {
        # ✅ return BOTH keys to avoid any client mismatch ever again
        "items": items,
        "parsed": items,
        "parsed_text": parsed_text,
        "overlay_jpeg_b64": overlay_jpeg_b64,
        "meta": {
            "ms_total": int((time.time() - t0) * 1000),
            "run_caption": bool(run_caption),
            "box_threshold": float(box_threshold),
            "iou_threshold": float(iou_threshold),
            "use_paddleocr": bool(use_paddleocr),
            "imgsz": int(imgsz),
            "icon_size": int(effective_icon_size),
            "count": len(items),
            "internal_error": internal_error,
        },
    }
