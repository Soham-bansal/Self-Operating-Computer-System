from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class Intent(str, Enum):
    NAVIGATE = "navigate"
    INTERACT = "interact"
    EXTRACT = "extract"
    VERIFY = "verify"
    UNKNOWN = "unknown"


class ActionType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE_TEXT = "type_text"
    PRESS_KEY = "press_key"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    WAIT = "wait"
    OPEN_URL = "open_url"
    DRAG_DROP = "drag_drop"
    SET_CLIPBOARD = "set_clipboard"


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)

    x: StrictInt = Field(ge=0)
    y: StrictInt = Field(ge=0)


class BBox(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)

    x: StrictInt = Field(ge=0)
    y: StrictInt = Field(ge=0)
    w: StrictInt = Field(gt=0)
    h: StrictInt = Field(gt=0)


class MatchQuery(BaseModel):
    """
    Explicit match query (no free-form dict).
    Keep it constrained so agents don't drift.
    """
    model_config = ConfigDict(extra="forbid", strict=False)

    element_id: Optional[StrictStr] = None
    role: Optional[StrictStr] = None
    text_equals: Optional[StrictStr] = None
    text_contains: Optional[StrictStr] = None
    bbox: Optional[BBox] = None


class Expectation(BaseModel):
    """
    What should be true after an action.
    """
    model_config = ConfigDict(extra="forbid", strict=False)

    should_change_screen: bool = False
    should_appear_text: Optional[StrictStr] = None
    should_disappear_text: Optional[StrictStr] = None
    should_focus_element_id: Optional[StrictStr] = None


class Action(BaseModel):
    """
    Validated, executable instruction. Strict + extra forbidden.
    """
    model_config = ConfigDict(extra="forbid", strict=False)

    action_type: ActionType

    # Targets
    point: Optional[Point] = None
    bbox: Optional[BBox] = None
    target: Optional[MatchQuery] = None

    # Payloads
    text: Optional[StrictStr] = None
    key: Optional[StrictStr] = None
    hotkey: Optional[List[StrictStr]] = None  # e.g. ["CTRL", "L"]
    url: Optional[StrictStr] = None

    # Parameters
    scroll_dx: Optional[int] = None
    scroll_dy: Optional[int] = None
    wait_ms: Optional[int] = Field(default=None, ge=0)

    # Drag/drop
    to_point: Optional[Point] = None

    @model_validator(mode="after")
    def _validate_required_fields(self) -> "Action":
        t = self.action_type

        def require(cond: bool, msg: str) -> None:
            if not cond:
                raise ValueError(msg)

        if t in {ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK}:
            require(self.point is not None or self.bbox is not None or self.target is not None,
                    f"{t} requires one of: point, bbox, target")
        elif t == ActionType.TYPE_TEXT:
            require(self.text is not None and self.text != "", "type_text requires non-empty text")
            require(self.target is not None or self.point is not None or self.bbox is not None,
                    "type_text requires one of: target, point, bbox")
        elif t == ActionType.PRESS_KEY:
            require(self.key is not None and self.key != "", "press_key requires non-empty key")
        elif t == ActionType.HOTKEY:
            require(self.hotkey is not None and len(self.hotkey) >= 2, "hotkey requires a list of 2+ keys")
        elif t == ActionType.SCROLL:
            require(self.scroll_dx is not None or self.scroll_dy is not None,
                    "scroll requires scroll_dx and/or scroll_dy")
        elif t == ActionType.WAIT:
            require(self.wait_ms is not None, "wait requires wait_ms")
        elif t == ActionType.OPEN_URL:
            require(self.url is not None and self.url != "", "open_url requires non-empty url")
        elif t == ActionType.DRAG_DROP:
            require(self.point is not None or self.target is not None or self.bbox is not None,
                    "drag_drop requires a source (point/bbox/target)")
            require(self.to_point is not None, "drag_drop requires to_point")
        elif t == ActionType.SET_CLIPBOARD:
            require(self.text is not None and self.text != "", "set_clipboard requires non-empty text")

        return self
