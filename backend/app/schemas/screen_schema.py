from __future__ import annotations

# The canonical ScreenSchemaV2 lives in perception/schema_v2.py.
# This module re-exports it so any legacy import still resolves correctly.
from ..perception.schema_v2 import (  # noqa: F401
    ScreenSchemaV2,
    ScreenElement,
    OCRToken,
    FocusInfo,
    ActiveApp,
    BBox,
    Point,
)
