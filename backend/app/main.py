from __future__ import annotations

import uuid
from typing import Optional

import os
import json
from datetime import datetime, timezone


from contextlib import asynccontextmanager

import requests as _requests
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .config import Mode, load_settings
from .logger import JSONLRunLogger
from .state.store import InMemoryRunStore
from .orchestrator.runner import RunOrchestrator


settings = load_settings()
store = InMemoryRunStore()
orchestrator = RunOrchestrator(store, omni_url=settings.omni_url)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        r = _requests.get(f"{settings.omni_url}/", timeout=3.0)
        print(f"[startup] OmniParser reachable at {settings.omni_url} (HTTP {r.status_code})")
    except Exception as e:
        print(f"[startup] WARNING: OmniParser not reachable at {settings.omni_url} — {e}")
        print("[startup] Start it with: scripts\\start_omni.bat")
    yield


app = FastAPI(title="screenai-agent", version="0.1.0", lifespan=lifespan)

class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)
    mode: Mode = Field(default=Mode.Observe)
    prompt: StrictStr = Field(default="")
    run_id: Optional[StrictStr] = None



class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)
    run_id: StrictStr


class RespondRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=False)
    run_id: StrictStr
    response: StrictStr



class RunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: StrictStr
    status: StrictStr
    mode: Mode


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: StrictStr
    status: StrictStr
    mode: StrictStr
    created_ts: StrictStr
    updated_ts: StrictStr
    last_error: Optional[StrictStr] = None

    phase: StrictStr
    step_id: Optional[int] = None
    attempt: Optional[int] = None
    logs: list[StrictStr] = []

    # Phase J: human-in-the-loop
    pending_question: Optional[StrictStr] = None
    pending_secret: bool = False
    pending_kind: StrictStr = "input"




@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    run_id = req.run_id or str(uuid.uuid4())
    rec = store.create(run_id=run_id, mode=req.mode.value)

    logger = JSONLRunLogger(run_id=run_id)
    logger.log_ok(
        phase="IDLE",
        event_type="run_started",
        validated_output={"mode": req.mode.value, "prompt": req.prompt},
    )

    # Start background worker
    orchestrator.start(run_id=run_id, prompt=req.prompt, mode=req.mode.value)

    return RunResponse(run_id=rec.run_id, status=rec.status, mode=req.mode)


TERMINAL = {"done", "failed", "stopped"}


@app.post("/pause", response_model=RunResponse)
def pause(req: ControlRequest) -> RunResponse:
    rec = store.get(req.run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    if rec.status in TERMINAL:
        return RunResponse(run_id=rec.run_id, status=rec.status, mode=Mode(rec.mode))

    store.request_pause(req.run_id)
    store.set_status(req.run_id, "paused")
    store.set_phase(req.run_id, "PAUSED")   # ✅ ADD THIS
    JSONLRunLogger(req.run_id).log_ok("PAUSED", "pause_requested", validated_output={})

    return RunResponse(run_id=rec.run_id, status="paused", mode=Mode(rec.mode))


@app.post("/resume", response_model=RunResponse)
def resume(req: ControlRequest) -> RunResponse:
    rec = store.get(req.run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    if rec.status in TERMINAL:
        return RunResponse(run_id=rec.run_id, status=rec.status, mode=Mode(rec.mode))

    store.request_resume(req.run_id)
    store.set_status(req.run_id, "running")
    store.set_phase(req.run_id, "ACTING")   # ✅ ADD THIS (or restore previous phase if you track it)
    JSONLRunLogger(req.run_id).log_ok("ACTING", "resume_requested", validated_output={})

    return RunResponse(run_id=rec.run_id, status="running", mode=Mode(rec.mode))


@app.post("/stop", response_model=RunResponse)
def stop(req: ControlRequest) -> RunResponse:
    rec = store.get(req.run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run_id not found")

    if rec.status in TERMINAL:
        return RunResponse(run_id=rec.run_id, status=rec.status, mode=Mode(rec.mode))

    store.request_stop(req.run_id)

    # ✅ make phase consistent immediately
    store.set_phase(req.run_id, "STOPPED")
    store.set_status(req.run_id, "stopped")

    JSONLRunLogger(req.run_id).log_ok("STOPPED", "stop_requested", validated_output={})
    return RunResponse(run_id=rec.run_id, status="stopped", mode=Mode(rec.mode))


@app.post("/respond", response_model=RunResponse)
def respond(req: RespondRequest) -> RunResponse:
    """Phase J: the human answers a pending question from the agent."""
    rec = store.get(req.run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run_id not found")
    if rec.status in TERMINAL:
        return RunResponse(run_id=rec.run_id, status=rec.status, mode=Mode(rec.mode))
    if not rec.pending_question:
        raise HTTPException(status_code=409, detail="run is not awaiting user input")

    store.provide_user_response(req.run_id, req.response)
    # Do NOT log the response value (it may be a secret like an OTP/password)
    JSONLRunLogger(req.run_id).log_ok(
        "ACTING", "user_response_submitted",
        validated_output={"chars": len(req.response)},
    )
    return RunResponse(run_id=rec.run_id, status="running", mode=Mode(rec.mode))


def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()

def safe_read_events(run_id: str, limit: int = 200) -> list[dict]:
    path = os.path.join("logs", "runs", f"{run_id}.jsonl")
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.readlines()[-limit:]
    except Exception:
        return []

    out: list[dict] = []
    for line in raw:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            # ignore partial/bad line (prevents 500)
            continue
    return out



@app.get("/status", response_model=StatusResponse)
def status(run_id: StrictStr = Query(...)) -> StatusResponse:
    rec = store.get(run_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="run_id not found")

    # Always derive stable fields from the store (never from logs)
    phase = rec.phase or "IDLE"
    step_id = rec.step_id
    attempt = rec.attempt

    # Read logs safely (never crash)
    events = safe_read_events(run_id, limit=200)

    log_lines: list[str] = []
    for e in events:
        ts = e.get("ts", "")
        ph = e.get("phase", "")
        et = e.get("event_type", "")
        ok = e.get("result", {}).get("ok", True)
        reason = e.get("result", {}).get("reason")

        sid = e.get("step_id")
        att = e.get("attempt")

        extra = []
        if sid is not None:  # IMPORTANT: allow sid=0
            extra.append(f"step={sid}")
        if att is not None:
            extra.append(f"attempt={att}")
        extra.append("ok" if ok else "FAIL")
        if reason:
            extra.append(f"reason={reason}")

        log_lines.append(f"[{ts}] {ph} {et} " + " ".join(extra))

    return StatusResponse(
        run_id=rec.run_id,
        status=rec.status,
        mode=rec.mode,
        created_ts=rec.created_ts,
        updated_ts=rec.updated_ts,
        last_error=rec.last_error,
        phase=phase,
        step_id=step_id,
        attempt=attempt,
        logs=log_lines,
        pending_question=rec.pending_question,
        pending_secret=rec.pending_secret,
        pending_kind=rec.pending_kind,
    )
