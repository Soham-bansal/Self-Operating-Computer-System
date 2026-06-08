from __future__ import annotations

import json
from typing import Any, Dict, List

from .agents import PlanStep
from .llm import agent_llm


_SYSTEM = """You are a planning agent for a Windows PC automation system.
Break the user's task into clear sequential steps a computer can execute.

Available executor actions (use these when planning):
- open_app: directly launch any Windows app by name (e.g. "notepad", "chrome", "calculator")
- click: click on a UI element by coordinates
- type: type text into a focused field
- hotkey: press keyboard shortcuts
- scroll: scroll the screen

Rules:
- ALWAYS use open_app to launch applications — never plan to click the Start menu for this
- Each step must be ONE concrete, visible goal
- Keep steps small — one action per step where possible
- Maximum 15 steps
- Steps must be in correct order
- Be specific about app names, URLs, button labels

Return ONLY a JSON object:
{
  "steps": [
    {"step_id": 0, "goal": "Open Notepad using open_app"},
    {"step_id": 1, "goal": "Click inside the Notepad text area"},
    {"step_id": 2, "goal": "Type the required text"}
  ],
  "reasoning": "brief explanation of the plan"
}"""


class Planner:
    def __init__(self) -> None:
        self._client, self._model = agent_llm("planner")

    def plan(self, prompt: str, mode: str, context: Dict[str, Any]) -> List[PlanStep]:
        resp = self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": f"Task: {prompt}\nMode: {mode}"},
            ],
            max_tokens=1000,
            temperature=0.2,
        )

        raw = json.loads(resp.choices[0].message.content)
        steps_raw = raw.get("steps", [])

        if not steps_raw:
            raise ValueError(f"Planner returned empty plan for: {prompt!r}")

        return [
            PlanStep(step_id=int(s["step_id"]), goal=str(s["goal"]))
            for s in steps_raw
        ]
