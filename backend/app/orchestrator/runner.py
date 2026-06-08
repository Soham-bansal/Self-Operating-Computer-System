from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..executor.actions import Action as ExecAction
from ..executor.actions import Executor, StopRequested
from ..logger import JSONLRunLogger
from ..perception import ScreenPerceptor, ScreenSchemaV2
from ..state.machine import Phase
from ..state.store import InMemoryRunStore
from .agents import Action as AgentAction
from .planner import Planner
from .navigator import Navigator
from .verifier import Verifier
from .skills import expand_skill

MAX_ACTIONS_PER_RUN = 60          # hard cap on total actions for one task
MAX_CONSECUTIVE_ERRORS = 5        # stop if the navigator keeps producing invalid output
SETTLE_DELAY_S = 0.8              # let the screen update after an action
MAX_MODE_SWITCHES = 8            # safety cap on web<->desktop switches in one run

# Fallback keyword hints (used only if the LLM router fails)
_WEB_HINTS = (
    "youtube", "google", "chrome", "browser", "website", "web ", "online",
    "http://", "https://", ".com", ".in", ".org", "amazon", "flipkart",
    "wikipedia", "gmail", "search the internet", "search online", "play a song",
    "play song", "watch", "browse", "whatsapp", "message to", "send a message",
)
_DESKTOP_HINTS = (
    "notepad", "wordpad", "microsoft word", " word ", "excel", "powerpoint",
    "calculator", "calc", "file explorer", "explorer", "paint", "mspaint",
    "settings", "control panel", "vscode", "visual studio",
)


def _keyword_mode(prompt: str) -> str:
    """Fallback heuristic if the LLM router fails. Returns 'web' or 'desktop'."""
    p = (prompt or "").lower()
    desktop = any(h in p for h in _DESKTOP_HINTS)
    web = any(h in p for h in _WEB_HINTS)
    if web and not desktop:
        return "web"
    if desktop and not web:
        return "desktop"
    return "web" if any(h in p for h in ("youtube", "google", "chrome", "browser",
                                         "http://", "https://", ".com")) else "desktop"


@dataclass
class RunContext:
    prompt: str
    mode: str
    meta: Dict[str, Any]


def _screen_signature(screen: ScreenSchemaV2) -> str:
    """Cheap fingerprint of the screen — used to detect whether an action changed anything."""
    parts = [str(screen.active_app.title)]
    for it in screen.omni_items:
        parts.append(str(it.get("content", "")))
    return "|".join(parts)


def _short_payload(kind: str, payload: Dict[str, Any]) -> str:
    """Compact human/LLM-readable description of an action for the history log."""
    if kind in ("click", "double_click", "right_click", "move"):
        tgt = payload.get("target", {})
        pt = tgt.get("point", {}) if isinstance(tgt, dict) else {}
        return f"@({pt.get('x','?')},{pt.get('y','?')})"
    if kind == "type":
        return f'"{str(payload.get("text",""))[:40]}"'
    if kind == "hotkey":
        return "+".join(payload.get("keys", []))
    if kind == "scroll":
        return f"amount={payload.get('scroll_amount')}"
    if kind == "open_app":
        return payload.get("app", "")
    if kind == "wait":
        return f"{payload.get('delay_ms')}ms"
    return ""


class RunOrchestrator:
    def __init__(self, store: InMemoryRunStore, omni_url: str = "http://127.0.0.1:8010"):
        self.store = store
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

        # Agents are created lazily at the start of each run (in _worker_loop), so the
        # backend can start WITHOUT an API key — you add keys in the UI Settings, then Run.
        self.planner = None
        self.navigator = None
        self.verifier = None

        self.perceptor = ScreenPerceptor(enable_omni=True, omni_url=omni_url)
        self._web_controller = None  # kept alive after a successful web run so the page stays open

    def start(self, run_id: str, prompt: str, mode: str) -> None:
        with self._lock:
            if run_id in self._threads and self._threads[run_id].is_alive():
                return
            t = threading.Thread(
                target=self._worker_loop,
                args=(run_id, prompt, mode),
                daemon=True,
            )
            self._threads[run_id] = t
            t.start()

    # ── intelligent mode routing ─────────────────────────────────────────────
    def _route_mode(self, prompt: str, log: JSONLRunLogger) -> str:
        """Let the LLM decide HOW to do the task: 'web', 'desktop', or 'both'.
        Returns the mode to START in ('web' or 'desktop'). For 'both' it returns
        the best starting mode; the agent can switch later via switch_mode."""
        system = (
            "You decide HOW a Windows automation task should be executed.\n"
            "Reply with JSON: {\"mode\":\"web|desktop|both\",\"start\":\"web|desktop\",\"why\":\"...\"}\n"
            "- web: done entirely in a web browser (websites, YouTube, Google, online shopping, web apps, WhatsApp Web).\n"
            "- desktop: done in native Windows apps (Notepad, Word, Excel, Calculator, File Explorer, Settings, installed apps).\n"
            "- both: needs the browser AND a native app (e.g. research online then write in Word).\n"
            "For 'both', set 'start' to whichever should happen first."
        )
        try:
            resp = self.planner._client.chat.completions.create(  # reuse planner's OpenAI client
                model=self.planner._model,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": f"Task: {prompt}"}],
                max_tokens=120, temperature=0.0,
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            mode = str(data.get("mode", "")).lower()
            start = str(data.get("start", "")).lower()
            why = str(data.get("why", ""))[:200]
            if mode not in ("web", "desktop", "both"):
                raise ValueError("bad mode")
            start_mode = start if start in ("web", "desktop") else ("web" if mode == "web" else "desktop")
            log.log_ok(phase=Phase.PLANNING.value, event_type="mode_routed",
                       validated_output={"mode": mode, "start": start_mode, "why": why})
            return start_mode
        except Exception as e:
            fallback = _keyword_mode(prompt)
            log.log_fail(phase=Phase.PLANNING.value, event_type="mode_route_failed",
                         reason=str(e), validated_output={"fallback": fallback})
            return fallback

    # ── main worker: route mode, then dispatch (with switching) ──────────────
    def _worker_loop(self, run_id: str, prompt: str, mode: str) -> None:
        log = JSONLRunLogger(run_id)

        # Create agents now, reading the latest Settings (providers / models / API keys
        # edited in the UI) — so changes apply without restarting the backend.
        try:
            self.planner = Planner()
            self.navigator = Navigator()
            self.verifier = Verifier()
        except Exception as e:
            msg = ("No valid API key for the selected provider. Open the app's "
                   "Settings, add your API key (and pick the provider/model), then Run again. "
                   f"[{e}]")
            self.store.update_progress(run_id, phase=Phase.FAILED.value,
                                       status="failed", last_error=msg)
            log.log_fail(phase=Phase.FAILED.value, event_type="run_failed",
                         reason=msg, validated_output={})
            return

        log.log_ok(phase=Phase.PLANNING.value, event_type="planning_started",
                   validated_output={"mode": mode})
        self.store.update_progress(run_id, phase=Phase.PLANNING.value,
                                   step_id=None, attempt=None, status="running")

        ctx = RunContext(prompt=prompt, mode=mode, meta={})
        # _mode_gate enforces the run mode (dry-run / per-action approval), so the
        # executor runs unrestricted here. The hard denylist (cmd, powershell, etc.)
        # still always applies inside the executor for safety.
        exec_mode = "autonomous"

        def stop_checker() -> bool:
            rec = self.store.get(run_id)
            return bool(rec and rec.stop_requested)

        executor = Executor(mode=exec_mode, stop_checker=stop_checker)

        try:
            current = self._route_mode(ctx.prompt, log)
            history: List[str] = []   # shared across modes so context carries over
            switches = 0

            while True:
                if current == "web":
                    result = self._web_loop(run_id, ctx, log, history)
                else:
                    result = self._desktop_loop(run_id, ctx, log, executor, history)

                if isinstance(result, str) and result.startswith("switch:") and switches < MAX_MODE_SWITCHES:
                    switches += 1
                    current = result.split(":", 1)[1]
                    log.log_ok(phase=Phase.PLANNING.value, event_type="mode_switched",
                               validated_output={"to": current, "switch_num": switches})
                    continue
                break
        except Exception as e:
            self.store.update_progress(run_id, phase=Phase.FAILED.value,
                                       status="failed", last_error=str(e))
            log.log_fail(phase=Phase.FAILED.value, event_type="run_failed",
                         reason=str(e), validated_output={})

    # ── DESKTOP (vision) loop ────────────────────────────────────────────────
    def _desktop_loop(self, run_id: str, ctx: "RunContext", log: JSONLRunLogger,
                      executor: Executor, history: List[str]) -> str:
        """Vision/OmniParser ReAct loop for native Windows apps.
        Returns 'done' | 'failed' | 'stopped' | 'switch:web'."""
        # Clean desktop for a fresh start
        try:
            executor.execute({"type": "hotkey", "keys": ["winleft", "d"]})
            time.sleep(1.0)
            self.perceptor.invalidate_cache()
            log.log_ok(phase=Phase.PLANNING.value, event_type="desktop_cleared",
                       validated_output={"note": "minimized all windows"})
        except Exception as e:
            log.log_fail(phase=Phase.PLANNING.value, event_type="desktop_clear_failed",
                         reason=str(e), validated_output={})

        # Light plan (guidance only) — computed once per run
        plan_text = ctx.meta.get("plan_text")
        if not plan_text:
            try:
                plan = self.planner.plan(prompt=ctx.prompt, mode=ctx.mode, context=ctx.meta)
                plan_text = "\n".join(f"  {s.step_id + 1}. {s.goal}" for s in plan)
            except Exception as e:
                plan_text = "  (no plan — work directly from the screen)"
                log.log_fail(phase=Phase.PLANNING.value, event_type="planning_failed",
                             reason=str(e), validated_output={})
            ctx.meta["plan_text"] = plan_text
            log.log_ok(phase=Phase.PLANNING.value, event_type="planning_done",
                       validated_output={"plan": plan_text})

        actions_taken = 0
        consecutive_errors = 0
        prev_sig: Optional[str] = None
        last_action_desc: Optional[str] = None
        repeat_count = 0

        try:
            while actions_taken < MAX_ACTIONS_PER_RUN:
                if self._check_stop(run_id, log):
                    return "stopped"
                self._pause_gate(run_id, log)
                if self._check_stop(run_id, log):
                    return "stopped"

                self.store.update_progress(run_id, phase=Phase.ACTING.value,
                                           step_id=actions_taken, attempt=0, status="running")

                # --- PERCEIVE ---
                self.perceptor.invalidate_cache()
                screen: Optional[ScreenSchemaV2] = None
                frame = None
                try:
                    screen, frame, perf = self.perceptor.perceive()
                    log.log_ok(phase=Phase.ACTING.value, event_type="perception_done",
                               validated_output={"items": len(screen.omni_items),
                                                 "total_ms": round(perf.get("total_ms", 0.0), 1)},
                               step_id=actions_taken)
                except Exception as e:
                    log.log_fail(phase=Phase.ACTING.value, event_type="perception_failed",
                                 reason=str(e), validated_output={}, step_id=actions_taken)
                    time.sleep(1.0)
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError("too_many_perception_failures")
                    continue

                # --- PROGRESS FEEDBACK ---
                cur_sig = _screen_signature(screen)
                if prev_sig is not None and history:
                    if cur_sig == prev_sig and "NO CHANGE" not in history[-1]:
                        history[-1] = history[-1] + "  <-- NO CHANGE: that action did nothing, try a DIFFERENT element/approach"
                prev_sig = cur_sig

                # --- DECIDE ---
                try:
                    action: AgentAction = self.navigator.decide(
                        goal=ctx.prompt, plan_text=plan_text, history=history,
                        screen=screen, frame=frame,
                    )
                    log.log_ok(phase=Phase.ACTING.value, event_type="action_selected",
                               validated_output={"kind": action.kind, "payload": action.payload},
                               step_id=actions_taken)
                except Exception as e:
                    log.log_fail(phase=Phase.ACTING.value, event_type="navigator_output_invalid",
                                 reason=str(e), validated_output={}, step_id=actions_taken)
                    history.append(f"(tried an action but it was invalid: {e})")
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError("too_many_navigator_errors")
                    continue

                # --- REPEATED-ACTION GUARD ---
                this_desc = f"{action.kind} {_short_payload(action.kind, action.payload)}"
                repeat_count = repeat_count + 1 if this_desc == last_action_desc else 0
                last_action_desc = this_desc
                if repeat_count >= 3:
                    history.append(
                        f"STUCK: repeated '{this_desc}' {repeat_count + 1} times with no progress. "
                        f"You MUST do something completely different now."
                    )
                    if repeat_count >= 6:
                        raise RuntimeError(f"stuck_repeating_action: {this_desc}")

                # --- TERMINAL / SWITCH / HUMAN INPUT ---
                if action.kind == "done":
                    self._finish_done(run_id, log, reason=f"agent_done: {action.payload.get('reason','')}")
                    return "done"
                if action.kind == "fail":
                    raise RuntimeError(f"agent_reported_fail: {action.payload.get('reason','')}")
                if action.kind == "switch_mode":
                    target = str(action.payload.get("to", "web")).lower()
                    if target not in ("web", "desktop"):
                        target = "web"
                    history.append(f"switched to {target} mode: {action.payload.get('reason','')}")
                    return f"switch:{target}"
                if action.kind == "ask_user":
                    question = str(action.payload.get("question", "Input needed"))
                    secret = bool(action.payload.get("secret", False))
                    answer = self._await_user_input(run_id, log, question, secret)
                    if answer is None:
                        return "stopped"
                    if secret:
                        history.append(f"asked user: '{question}' -> [received, hidden]")
                    else:
                        history.append(f"asked user: '{question}' -> '{answer}'")
                        ctx.meta["last_user_answer"] = answer
                    actions_taken += 1
                    continue

                # --- SKILL ---
                if action.kind == "skill":
                    sname = str(action.payload.get("name", ""))
                    sargs = action.payload.get("args", {}) or {}
                    for k, v in list(sargs.items()):
                        if v == "$user" and ctx.meta.get("last_user_answer"):
                            sargs[k] = ctx.meta["last_user_answer"]
                    try:
                        steps = expand_skill(sname, sargs)
                    except Exception as e:
                        log.log_fail(phase=Phase.ACTING.value, event_type="skill_invalid",
                                     reason=str(e), validated_output={"name": sname, "args": sargs},
                                     step_id=actions_taken)
                        history.append(f"skill {sname} -> INVALID ({e})")
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            raise RuntimeError("too_many_invalid_actions")
                        continue
                    consecutive_errors = 0
                    log.log_ok(phase=Phase.ACTING.value, event_type="skill_started",
                               validated_output={"name": sname, "args": sargs, "steps": len(steps)},
                               step_id=actions_taken)
                    skill_ok = True
                    for sub in steps:
                        if self._check_stop(run_id, log):
                            return "stopped"
                        try:
                            sub_res = executor.execute(sub)
                            actions_taken += 1
                        except StopRequested:
                            self.store.update_progress(run_id, phase=Phase.STOPPED.value,
                                                       status="stopped", last_error="executor_stop")
                            log.log_ok(phase=Phase.STOPPED.value, event_type="run_stopped",
                                       validated_output={"reason": "executor_stop"})
                            return "stopped"
                        if not sub_res.ok:
                            skill_ok = False
                            break
                        time.sleep(0.3)
                    history.append(f"skill {sname}({sargs}) -> {'ok' if skill_ok else 'partial/failed'}")
                    log.log_ok(phase=Phase.ACTING.value, event_type="skill_done",
                               validated_output={"name": sname, "ok": skill_ok}, step_id=actions_taken)
                    time.sleep(SETTLE_DELAY_S)
                    continue

                # --- BUILD + STRICT VALIDATE ---
                try:
                    if action.kind == "noop":
                        exec_action_dict: Dict[str, Any] = {"type": "wait", "delay_ms": 150}
                    else:
                        if not isinstance(action.payload, dict):
                            raise ValueError("agent action payload must be a dict")
                        exec_action_dict = {"type": action.kind, **action.payload}
                    exec_action: ExecAction = ExecAction.parse_obj(exec_action_dict)
                except Exception as e:
                    log.log_fail(phase=Phase.ACTING.value, event_type="navigator_output_invalid",
                                 reason=str(e),
                                 validated_output={"raw_kind": getattr(action, "kind", None),
                                                   "raw_payload": getattr(action, "payload", None)},
                                 step_id=actions_taken)
                    history.append(f"{action.kind} -> INVALID ({e})")
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError("too_many_invalid_actions")
                    continue

                consecutive_errors = 0

                # --- EXECUTE ---
                try:
                    res = executor.execute(exec_action_dict)
                    actions_taken += 1
                except StopRequested:
                    self.store.update_progress(run_id, phase=Phase.STOPPED.value,
                                               status="stopped", last_error="executor_stop")
                    log.log_ok(phase=Phase.STOPPED.value, event_type="run_stopped",
                               validated_output={"reason": "executor_stop"})
                    return "stopped"

                short = _short_payload(action.kind, action.payload)
                if res.ok:
                    history.append(f"{action.kind} {short} -> ok")
                    log.log_ok(phase=Phase.ACTING.value, event_type="action_executed",
                               validated_output={"action": exec_action.dict(), "detail": res.detail},
                               step_id=actions_taken)
                else:
                    history.append(f"{action.kind} {short} -> FAILED: {res.error}")
                    log.log_fail(phase=Phase.ACTING.value, event_type="action_failed",
                                 reason=res.error or "unknown",
                                 validated_output={"error": res.error, "action": exec_action.dict()},
                                 step_id=actions_taken)

                time.sleep(SETTLE_DELAY_S)

            raise RuntimeError("max_actions_exceeded")

        except Exception as e:
            self.store.update_progress(run_id, phase=Phase.FAILED.value,
                                       status="failed", last_error=str(e))
            log.log_fail(phase=Phase.FAILED.value, event_type="run_failed",
                         reason=str(e), validated_output={})
            return "failed"

    # ── WEB (DOM) loop ───────────────────────────────────────────────────────
    def _web_loop(self, run_id: str, ctx: "RunContext", log: JSONLRunLogger,
                  history: List[str]) -> str:
        """DOM-based browser automation loop (Playwright).
        Returns 'done' | 'failed' | 'stopped' | 'switch:desktop'."""
        from ..perception.web_controller import WebController
        from ..config import load_settings

        _s = load_settings()
        keep_open = False

        if _s.use_real_chrome_profile and _s.chrome_user_data_dir:
            self._kill_agent_chrome(_s.chrome_user_data_dir)
            time.sleep(1.0)

        controller = WebController(
            headless=False,
            use_real_profile=_s.use_real_chrome_profile,
            user_data_dir=_s.chrome_user_data_dir,
            profile_dir=_s.chrome_profile_dir,
        )
        try:
            controller.start()
        except Exception as e:
            log.log_fail(phase=Phase.FAILED.value, event_type="web_start_failed",
                         reason=f"{e} — is Playwright installed? run: playwright install chromium",
                         validated_output={})
            self.store.update_progress(run_id, phase=Phase.FAILED.value, status="failed",
                                       last_error=f"web_start_failed: {e}")
            return "failed"

        actions_taken = 0
        consecutive_errors = 0
        prev_sig: Optional[str] = None
        last_desc: Optional[str] = None
        repeat_count = 0
        last_click_index: Any = None
        click_repeat = 0
        result_signal = "failed"

        def sig_of(state: Dict[str, Any]) -> str:
            els = state.get("elements", [])
            return state.get("url", "") + "|" + "|".join(str(e.get("text", "")) for e in els[:40])

        try:
            while actions_taken < MAX_ACTIONS_PER_RUN:
                if self._check_stop(run_id, log):
                    result_signal = "stopped"; return result_signal
                self._pause_gate(run_id, log)
                if self._check_stop(run_id, log):
                    result_signal = "stopped"; return result_signal

                self.store.update_progress(run_id, phase=Phase.ACTING.value,
                                           step_id=actions_taken, attempt=0, status="running")

                # --- PERCEIVE (DOM) ---
                try:
                    state = controller.get_state()
                    log.log_ok(phase=Phase.ACTING.value, event_type="web_perception_done",
                               validated_output={"url": state.get("url", "")[:120],
                                                 "elements": len(state.get("elements", []))},
                               step_id=actions_taken)
                except Exception as e:
                    log.log_fail(phase=Phase.ACTING.value, event_type="web_perception_failed",
                                 reason=str(e), validated_output={}, step_id=actions_taken)
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError("too_many_web_perception_failures")
                    time.sleep(1.0)
                    continue

                cur_sig = sig_of(state)
                if prev_sig is not None and history and cur_sig == prev_sig and "NO CHANGE" not in history[-1]:
                    history[-1] += "  <-- NO CHANGE: try a different element/approach"
                prev_sig = cur_sig

                # --- DECIDE (DOM + screenshot) ---
                try:
                    shot = controller.screenshot_png()
                    action = self.navigator.decide_web(goal=ctx.prompt, history=history,
                                                       web_state=state, screenshot_png=shot)
                    log.log_ok(phase=Phase.ACTING.value, event_type="web_action_selected",
                               validated_output={"kind": action.kind, "payload": action.payload},
                               step_id=actions_taken)
                except Exception as e:
                    log.log_fail(phase=Phase.ACTING.value, event_type="web_navigator_invalid",
                                 reason=str(e), validated_output={}, step_id=actions_taken)
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        raise RuntimeError("too_many_web_navigator_errors")
                    continue
                consecutive_errors = 0

                # --- TERMINAL / SWITCH / HUMAN INPUT ---
                if action.kind == "done":
                    self._finish_done(run_id, log, reason=f"agent_done: {action.payload.get('reason','')}")
                    keep_open = True
                    result_signal = "done"; return result_signal
                if action.kind == "fail":
                    raise RuntimeError(f"agent_reported_fail: {action.payload.get('reason','')}")
                if action.kind == "switch_mode":
                    target = str(action.payload.get("to", "desktop")).lower()
                    if target not in ("web", "desktop"):
                        target = "desktop"
                    history.append(f"switched to {target} mode: {action.payload.get('reason','')}")
                    keep_open = True  # leave the page so the user/desktop step can use it
                    result_signal = f"switch:{target}"; return result_signal
                if action.kind == "ask_user":
                    controller.bring_to_front()
                    q = str(action.payload.get("question", "Input needed"))
                    ans = self._await_user_input(run_id, log, q,
                                                 bool(action.payload.get("secret", False)))
                    if ans is None:
                        result_signal = "stopped"; return result_signal
                    history.append(f"asked user: '{q}' -> '{ans}'")
                    actions_taken += 1
                    continue

                # repeat guard (general)
                this_desc = f"{action.kind} {action.payload}"
                repeat_count = repeat_count + 1 if this_desc == last_desc else 0
                last_desc = this_desc
                if repeat_count >= 3:
                    history.append("STUCK: repeated the same web action — do something different.")
                    if repeat_count >= 6:
                        raise RuntimeError("stuck_repeating_web_action")

                # click-specific guard (catches click[N] -> ask_user -> click[N] cycles)
                if action.kind == "web_click":
                    cur_idx = action.payload.get("index")
                    if cur_idx == last_click_index:
                        click_repeat += 1
                    else:
                        click_repeat = 0
                        last_click_index = cur_idx
                    if click_repeat >= 2:
                        history.append(
                            f"STUCK: element [{cur_idx}] clicked {click_repeat + 1} times and is NOT "
                            f"helping (likely a dead-end). Pick a DIFFERENT element, scroll, try another "
                            f"site, or ask the user."
                        )
                    if click_repeat >= 4:
                        raise RuntimeError(f"stuck_clicking_element_{cur_idx}")

                # --- EXECUTE WEB ACTION ---
                p = action.payload
                if action.kind == "web_goto":
                    target = str(p.get("url", "")).rstrip("/")
                    current = (state.get("url", "") or "").rstrip("/")
                    if target and current and (target == current or target in current):
                        res = {"ok": False, "error": "already on this URL — do not reload; use web_wait or act on an element"}
                    else:
                        try:
                            controller.goto(str(p.get("url", "")))
                            res = {"ok": True}
                        except Exception as e:
                            res = {"ok": False, "error": str(e)}
                elif action.kind == "web_wait":
                    time.sleep(2.5)
                    res = {"ok": True}
                elif action.kind == "web_click":
                    res = controller.click(int(p.get("index", -1)))
                elif action.kind == "web_type":
                    res = controller.type(int(p.get("index", -1)), str(p.get("text", "")),
                                          enter=bool(p.get("enter", False)))
                elif action.kind == "web_scroll":
                    res = controller.scroll(int(p.get("amount", 600)))
                elif action.kind == "web_enter":
                    res = controller.press_enter()
                else:
                    res = {"ok": False, "error": f"unknown web action: {action.kind}"}

                actions_taken += 1
                if res.get("ok"):
                    history.append(f"{action.kind} {p} -> ok")
                    log.log_ok(phase=Phase.ACTING.value, event_type="web_action_executed",
                               validated_output={"kind": action.kind}, step_id=actions_taken)
                else:
                    history.append(f"{action.kind} {p} -> FAILED: {res.get('error')}")
                    log.log_fail(phase=Phase.ACTING.value, event_type="web_action_failed",
                                 reason=str(res.get("error")), validated_output={}, step_id=actions_taken)

                time.sleep(0.5)

            raise RuntimeError("max_actions_exceeded")

        except StopRequested:
            self.store.update_progress(run_id, phase=Phase.STOPPED.value, status="stopped")
            result_signal = "stopped"
            return result_signal
        except Exception as e:
            self.store.update_progress(run_id, phase=Phase.FAILED.value, status="failed", last_error=str(e))
            log.log_fail(phase=Phase.FAILED.value, event_type="run_failed", reason=str(e), validated_output={})
            result_signal = "failed"
            return result_signal
        finally:
            if keep_open:
                self._web_controller = controller
            else:
                time.sleep(1.0)
                controller.close()

    @staticmethod
    def _kill_agent_chrome(user_data_dir: str) -> None:
        """Kill ONLY chrome processes using the agent's dedicated profile dir
        (matched by command line). Never touches the user's main Chrome."""
        import subprocess
        ps = (
            "Get-CimInstance Win32_Process -Filter \"name='chrome.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{user_data_dir}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           timeout=15, capture_output=True)
        except Exception:
            pass

    def _await_user_input(self, run_id: str, log: JSONLRunLogger,
                          question: str, secret: bool, kind: str = "input") -> Optional[str]:
        """Phase J: pause the run in AWAITING_USER_INPUT until the user responds
        (via the /respond API). Returns the answer, or None if the run is stopped."""
        self.store.request_user_input(run_id, question, secret=secret, kind=kind)
        self.store.update_progress(run_id, phase=Phase.AWAITING_USER_INPUT.value,
                                   status="awaiting_input")
        log.log_ok(phase=Phase.AWAITING_USER_INPUT.value, event_type="user_input_requested",
                   validated_output={"question": question, "secret": secret})

        while True:
            rec = self.store.get(run_id)
            if rec is None or rec.stop_requested:
                self._check_stop(run_id, log)
                return None
            if rec.user_response is not None:
                answer = self.store.consume_user_response(run_id)
                self.store.update_progress(run_id, phase=Phase.ACTING.value, status="running")
                log.log_ok(phase=Phase.ACTING.value, event_type="user_input_received",
                           validated_output={"secret": secret,
                                             "value": "[hidden]" if secret else answer})
                return answer or ""
            threading.Event().wait(0.25)

    def _pause_gate(self, run_id: str, log: JSONLRunLogger) -> None:
        while True:
            rec = self.store.get(run_id)
            if rec is None:
                return
            if rec.stop_requested:
                return
            if not rec.pause_requested:
                return
            if rec.status != "paused":
                self.store.update_progress(run_id, phase=Phase.PAUSED.value, status="paused")
                log.log_ok(phase=Phase.PAUSED.value, event_type="paused", validated_output={})
            threading.Event().wait(0.15)

    def _check_stop(self, run_id: str, log: JSONLRunLogger) -> bool:
        rec = self.store.get(run_id)
        if rec is None:
            return True
        if rec.stop_requested:
            if rec.status != "stopped":
                self.store.update_progress(run_id, phase=Phase.STOPPED.value, status="stopped")
                log.log_ok(phase=Phase.STOPPED.value, event_type="run_stopped", validated_output={})
            return True
        return False

    def _finish_done(self, run_id: str, log: JSONLRunLogger, reason: str) -> None:
        self.store.update_progress(run_id, phase=Phase.DONE.value, status="done")
        log.log_ok(phase=Phase.DONE.value, event_type="run_done", validated_output={"reason": reason})
