from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Optional

_UNSET = object()


def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    run_id: str
    status: str  # running/paused/stopped/done/failed/error
    mode: str
    created_ts: str
    updated_ts: str

    # Phase C stable status fields
    phase: str = "IDLE"
    step_id: Optional[int] = None
    attempt: Optional[int] = None

    last_error: Optional[str] = None

    # Control flags (set by API; read by worker)
    pause_requested: bool = False
    stop_requested: bool = False

    # Phase J: human-in-the-loop input
    pending_question: Optional[str] = None   # set by worker when it needs the user
    pending_secret: bool = False             # if True, the answer is sensitive (mask it)
    pending_kind: str = "input"              # "input" (text) | "confirm" (yes/no + optional text)
    user_response: Optional[str] = None      # set by API /respond; consumed by worker


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: Dict[str, RunRecord] = {}
        self._lock = Lock()

    def create(self, run_id: str, mode: str) -> RunRecord:
        with self._lock:
            rec = RunRecord(
                run_id=run_id,
                status="running",
                mode=mode,
                created_ts=utc_ts(),
                updated_ts=utc_ts(),
                phase="PLANNING",
                step_id=None,
                attempt=None,
                last_error=None,
                pause_requested=False,
                stop_requested=False,
            )
            self._runs[run_id] = rec
            return rec

    def get(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            return self._runs.get(run_id)

    def set_status(self, run_id: str, status: str, error: Optional[str] = None) -> Optional[RunRecord]:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            rec.status = status
            rec.updated_ts = utc_ts()
            rec.last_error = error
            return rec

    def update_progress(
        self,
        run_id: str,
        *,
        phase: Optional[str] = None,
        step_id: object = _UNSET,   # ✅ sentinel: only update if explicitly passed
        attempt: object = _UNSET,   # ✅ sentinel: only update if explicitly passed
        last_error: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[RunRecord]:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None

            if phase is not None:
                rec.phase = phase

            # ✅ preserve existing unless caller explicitly sets
            if step_id is not _UNSET:
                rec.step_id = step_id  # may be None or int

            if attempt is not _UNSET:
                rec.attempt = attempt  # may be None or int

            if last_error is not None:
                rec.last_error = last_error

            if status is not None:
                rec.status = status

            rec.updated_ts = utc_ts()
            return rec

    def request_pause(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            rec.pause_requested = True
            rec.updated_ts = utc_ts()
            return rec

    def request_resume(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            rec.pause_requested = False
            rec.updated_ts = utc_ts()
            return rec

    def request_stop(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            rec.stop_requested = True
            rec.updated_ts = utc_ts()
            return rec

    def set_phase(self, run_id: str, phase: str) -> Optional[RunRecord]:
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            rec.phase = phase
            rec.updated_ts = utc_ts()
            return rec

    # ── Phase J: human-in-the-loop ───────────────────────────────────────────
    def request_user_input(self, run_id: str, question: str, secret: bool = False,
                           kind: str = "input") -> Optional[RunRecord]:
        """Worker calls this when it needs input from the human."""
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            rec.pending_question = question
            rec.pending_secret = bool(secret)
            rec.pending_kind = kind or "input"
            rec.user_response = None
            rec.updated_ts = utc_ts()
            return rec

    def provide_user_response(self, run_id: str, response: str) -> Optional[RunRecord]:
        """API calls this when the human answers."""
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            rec.user_response = response
            rec.pending_question = None  # question answered
            rec.updated_ts = utc_ts()
            return rec

    def consume_user_response(self, run_id: str) -> Optional[str]:
        """Worker reads + clears the response once it has it."""
        with self._lock:
            rec = self._runs.get(run_id)
            if rec is None:
                return None
            resp = rec.user_response
            rec.user_response = None
            rec.pending_secret = False
            rec.updated_ts = utc_ts()
            return resp
