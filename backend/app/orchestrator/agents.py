from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class PlanStep:
    step_id: int
    goal: str


@dataclass
class Action:
    # IMPORTANT: kind must match Phase D Action.type values
    kind: str
    payload: Dict[str, Any]


class PlannerStub:
    def plan(self, prompt: str, mode: str, context: Dict[str, Any]) -> List[PlanStep]:
        p = (prompt or "").lower()

        # If user wants Phase D demo, plan a sequence of atomic actions (one action per step)
        if "phase d" in p or "executor demo" in p or "notepad" in p:
            return [
                PlanStep(step_id=0, goal="Open Notepad"),
                PlanStep(step_id=1, goal="Wait for Notepad"),
                PlanStep(step_id=2, goal="Type validation line"),
                PlanStep(step_id=3, goal="Select all"),
                PlanStep(step_id=4, goal="Delete"),
                PlanStep(step_id=5, goal="Type again"),
                PlanStep(step_id=6, goal="Close Notepad"),
                PlanStep(step_id=7, goal="Don't Save (Alt+N)"),
                PlanStep(step_id=8, goal="Confirm close (Enter)"),
            ]

        # Default safe plan
        return [
            PlanStep(step_id=0, goal=f"Understand user intent: {prompt[:60]}"),
            PlanStep(step_id=1, goal="Perform a safe dummy action"),
            PlanStep(step_id=2, goal="Verify the dummy outcome"),
        ]


class NavigatorStub:
    def next_action(self, step: PlanStep, attempt: int, context: Dict[str, Any], screen: Optional[Any] = None) -> Action:
        # Phase D demo steps (strict schema-compatible)
        if step.step_id == 0:
            return Action(kind="open_app", payload={"app": "notepad"})
        if step.step_id == 1:
            return Action(kind="wait", payload={"delay_ms": 700})
        if step.step_id == 2:
            return Action(kind="type", payload={"text": "Phase D executor working ✅"})
        if step.step_id == 3:
            return Action(kind="hotkey", payload={"keys": ["ctrl", "a"]})
        if step.step_id == 4:
            return Action(kind="hotkey", payload={"keys": ["delete"]})
        if step.step_id == 5:
            return Action(kind="type", payload={"text": "Phase D executor working ✅"})
        if step.step_id == 6:
            return Action(kind="hotkey", payload={"keys": ["alt", "f4"]})
        if step.step_id == 7:
            # Handles Notepad "Save?" dialog on many Windows setups
            return Action(kind="hotkey", payload={"keys": ["alt", "n"]})  # Don't Save
        if step.step_id == 8:
            return Action(kind="hotkey", payload={"keys": ["enter"]})

        # Fallback safe no-op
        return Action(kind="noop", payload={"note": f"doing step={step.step_id}, attempt={attempt}"})


class VerifierStub:
    def verify(self, step: PlanStep, action: Action, exec_result: Dict[str, Any], context: Dict[str, Any]) -> bool:
        # always pass (as required)
        return True
