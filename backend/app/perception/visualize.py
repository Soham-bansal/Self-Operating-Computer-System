from __future__ import annotations
import json
import os
from PIL import Image, ImageDraw

from .schema_v2 import ScreenSchemaV2


def save_debug_artifacts(schema: ScreenSchemaV2, full_rgb: Image.Image, out_dir: str = "logs/perception") -> None:
    os.makedirs(out_dir, exist_ok=True)
    overlay_path = os.path.join(out_dir, "latest_overlay.png")
    schema_path = os.path.join(out_dir, "latest_schema.json")

    img = full_rgb.copy()
    d = ImageDraw.Draw(img)

    vx, vy = schema.virtual_bbox.x, schema.virtual_bbox.y

    # ✅ Draw Omni items
    for i, it in enumerate(schema.omni_items):
        if not isinstance(it, dict):
            continue

        bb = it.get("bbox_abs_xywh")
        if not (isinstance(bb, list) and len(bb) == 4):
            continue

        x, y, w, h = [int(v) for v in bb]

        # convert to image coords
        x -= vx
        y -= vy

        itype = str(it.get("type", ""))
        content = it.get("content", None)
        content_s = ""
        if content is not None:
            content_s = str(content).strip().replace("\n", " ")
            content_s = content_s[:40]

        if itype == "text":
            color = "green"
        else:
            color = "red"

        d.rectangle([x, y, x + w, y + h], outline=color, width=2)
        label = f"{itype or 'item'} #{i}"
        if content_s:
            label += f" | {content_s}"
        d.text((x + 3, max(0, y - 14)), label, fill=color)

    img.save(overlay_path)

    with open(schema_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(schema.model_dump(), indent=2, ensure_ascii=False))
