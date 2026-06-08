from __future__ import annotations
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, StrictInt, StrictStr, StrictFloat


class _Cfg:
    extra = "forbid"


class Point(BaseModel):
    x: StrictInt
    y: StrictInt
    class Config(_Cfg): pass


class BBox(BaseModel):
    x: StrictInt
    y: StrictInt
    w: StrictInt
    h: StrictInt
    class Config(_Cfg): pass


class OCRToken(BaseModel):
    text: StrictStr
    bbox: BBox
    conf: StrictFloat
    class Config(_Cfg): pass


class ScreenElement(BaseModel):
    eid: StrictStr
    type: StrictStr
    bbox: BBox
    conf: StrictFloat = Field(0.0)
    label_text: Optional[StrictStr] = None
    description: Optional[StrictStr] = None
    source: List[StrictStr] = Field(default_factory=list)

    uia_id: Optional[StrictStr] = None
    name: Optional[StrictStr] = None
    value: Optional[StrictStr] = None
    enabled: Optional[bool] = None
    focusable: Optional[bool] = None
    depth: Optional[StrictInt] = None

    class Config(_Cfg): pass


class FocusInfo(BaseModel):
    bbox: BBox
    conf: StrictFloat = Field(0.0)
    method: StrictStr
    class Config(_Cfg): pass


class ActiveApp(BaseModel):
    title: StrictStr
    pid: StrictInt
    process: StrictStr
    class Config(_Cfg): pass


class ScreenSchemaV2(BaseModel):
    frame_id: StrictStr
    ts: StrictFloat

    virtual_bbox: BBox
    screen_w: StrictInt
    screen_h: StrictInt
    cursor: Point
    active_app: ActiveApp

    active_window_bbox: Optional[BBox] = None

    # Keep old fields (optional, but we won’t use them now)
    ocr_tokens: List[OCRToken] = Field(default_factory=list)
    elements: List[ScreenElement] = Field(default_factory=list)
    focus: Optional[FocusInfo] = None

    # ✅ NEW: store OmniParser raw output (but with fixed abs bbox added)
    omni_items: List[Dict[str, Any]] = Field(default_factory=list)
    omni_meta: Dict[str, Any] = Field(default_factory=dict)

    class Config(_Cfg): pass
