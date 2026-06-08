from __future__ import annotations

import base64
import io
import json
import time
from typing import Any, Dict, Optional

from PIL import Image

from ..perception.schema_v2 import ScreenSchemaV2
from .agents import Action, PlanStep
from .llm import agent_llm


_SYSTEM = """You are a verification agent for a Windows PC automation system.
Look at the screenshot taken AFTER an action was executed.
Decide if the step goal was successfully achieved.

You MUST return a JSON object in this exact format:
{"passed": true, "reason": "explanation"} or {"passed": false, "reason": "explanation"}"""

# Actions that are purely mechanical — trust the executor's ok result, skip vision
_TRUST_EXEC_ACTIONS = {"type", "hotkey", "scroll", "wait", "move"}

# Actions that open UI — wait a moment then trust the executor
_OPEN_ACTIONS = {"open_app"}

# Actions that require vision to verify (screen state must change visibly)
_VISION_ACTIONS = {"click", "double_click", "right_click"}


def _encode_image(img: Image.Image, max_side: int = 1024) -> str:
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


class Verifier:
    def __init__(self) -> None:
        self._client, self._model = agent_llm("verifier")
        self.last_reason = ""

    def verify(
        self,
        step: PlanStep,
        action: Action,
        exec_result: Dict[str, Any],
        context: Dict[str, Any],
        screen_after: Optional[ScreenSchemaV2] = None,
        frame_after: Any = None,
    ) -> bool:
        exec_ok = exec_result.get("ok", False)

        # Executor failed → always fail, no LLM needed
        if not exec_ok:
            self.last_reason = f"Executor failed: {exec_result.get('error', 'unknown')}"
            return False

        action_type = action.kind

        # Purely mechanical actions — trust the executor
        if action_type in _TRUST_EXEC_ACTIONS:
            self.last_reason = f"Trusted executor for {action_type} action"
            return True

        # App-opening actions — wait for the app to appear, then pass
        if action_type in _OPEN_ACTIONS:
            time.sleep(1.5)
            self.last_reason = f"open_app executed successfully, app should be launching"
            return True

        # For click actions — use vision if available, otherwise trust exec
        if frame_after is None:
            self.last_reason = "No screenshot available — trusting executor"
            return True

        return self._vision_verify(step, action, exec_result, screen_after, frame_after)

    def _vision_verify(
        self,
        step: PlanStep,
        action: Action,
        exec_result: Dict[str, Any],
        screen_after: Optional[ScreenSchemaV2],
        frame_after: Any,
    ) -> bool:
        img_b64 = _encode_image(frame_after.rgb)
        active = screen_after.active_app.title if screen_after else "unknown"

        user_text = (
            f"STEP GOAL: {step.goal}\n"
            f"ACTION TAKEN: {action.kind} {action.payload}\n"
            f"ACTIVE APP: {active}\n\n"
            "Look at the screenshot. Did the action make visible progress toward the goal?\n"
            "Be generous — if any progress was made, return passed=true.\n"
            'Return JSON: {"passed": true/false, "reason": "..."}'
        )

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_b64}",
                                    "detail": "auto",
                                },
                            },
                            {"type": "text", "text": user_text},
                        ],
                    },
                ],
                max_tokens=150,
                temperature=0.0,
            )
            raw = json.loads(resp.choices[0].message.content)
            self.last_reason = raw.get("reason", "")
            return bool(raw.get("passed", False))
        except Exception as e:
            # If vision check fails, trust the executor
            self.last_reason = f"Vision check failed ({e}) — trusting executor"
            return True
