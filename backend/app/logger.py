from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LogEvent:
    ts: str
    run_id: str
    phase: str  # PLAN/ACT/VERIFY
    event_type: str

    step_id: Optional[str] = None
    attempt: Optional[int] = None

    llm_raw: Optional[str] = None
    validated_output: Dict[str, Any] = None  # always present (can be {})

    screen_frame_id: Optional[str] = None

    result_ok: bool = True
    result_reason: Optional[str] = None


class JSONLRunLogger:
    """
    One file per run: logs/runs/<run_id>.jsonl
    Each line is a JSON object.
    """
    def __init__(self, run_id: str, base_dir: str = "logs/runs") -> None:
        self.run_id = run_id
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)
        self.path = os.path.join(self.base_dir, f"{run_id}.jsonl")

    def log(self, event: LogEvent) -> None:
        payload = {
            "ts": event.ts,
            "run_id": event.run_id,
            "phase": event.phase,
            "event_type": event.event_type,
            "step_id": event.step_id,
            "attempt": event.attempt,
            "llm_raw": event.llm_raw,
            "validated_output": event.validated_output if event.validated_output is not None else {},
            "screen_frame_id": event.screen_frame_id,
            "result": {
                "ok": event.result_ok,
                "reason": event.result_reason,
            },
        }
        line = json.dumps(payload, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    
    def read_events(self, limit: int = 200) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines=f.readlines()[-limit:]
        except Exception:
            return []
        
        events: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue

        return events
        


    def log_ok(
        self,
        phase: str,
        event_type: str,
        validated_output: Dict[str, Any],
        step_id: Optional[str] = None,
        attempt: Optional[int] = None,
        llm_raw: Optional[str] = None,
        screen_frame_id: Optional[str] = None,
    ) -> None:
        self.log(
            LogEvent(
                ts=utc_ts(),
                run_id=self.run_id,
                phase=phase,
                event_type=event_type,
                step_id=step_id,
                attempt=attempt,
                llm_raw=llm_raw,
                validated_output=validated_output,
                screen_frame_id=screen_frame_id,
                result_ok=True,
                result_reason=None,
            )
        )

    def log_fail(
        self,
        phase: str,
        event_type: str,
        reason: str,
        validated_output: Dict[str, Any],
        step_id: Optional[str] = None,
        attempt: Optional[int] = None,
        llm_raw: Optional[str] = None,
        screen_frame_id: Optional[str] = None,
    ) -> None:
        self.log(
            LogEvent(
                ts=utc_ts(),
                run_id=self.run_id,
                phase=phase,
                event_type=event_type,
                step_id=step_id,
                attempt=attempt,
                llm_raw=llm_raw,
                validated_output=validated_output,
                screen_frame_id=screen_frame_id,
                result_ok=False,
                result_reason=reason,
            )
        )
