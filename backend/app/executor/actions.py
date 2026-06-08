from __future__ import annotations

import os
import random
import subprocess
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

# Pydantic compat: works on pydantic v1 and v2 (via pydantic.v1 in v2)
try:
    from pydantic.v1 import BaseModel, Field, ValidationError, conlist, root_validator
except Exception:  # pydantic v1
    from pydantic import BaseModel, Field, ValidationError, conlist, root_validator  # type: ignore


ActionType = Literal[
    "click",
    "double_click",
    "right_click",
    "move",
    "type",
    "hotkey",
    "scroll",
    "wait",
    "open_app",
]

ExecMode = Literal["confirm", "autonomous"]


class Point(BaseModel):
    x: int
    y: int

    class Config:
        extra = "forbid"


class Target(BaseModel):
    point: Optional[Point] = None
    bbox: Optional[conlist(int, min_items=4, max_items=4)] = None  # [x, y, w, h]

    class Config:
        extra = "forbid"

    @root_validator
    def _exactly_one(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        point = values.get("point")
        bbox = values.get("bbox")
        has_point = point is not None
        has_bbox = bbox is not None
        if has_point == has_bbox:
            raise ValueError("target must contain exactly one of: point OR bbox")
        if bbox is not None:
            x, y, w, h = bbox
            if w <= 0 or h <= 0:
                raise ValueError("bbox w/h must be > 0")
        return values

    def resolve_point(self) -> Point:
        if self.point is not None:
            return self.point
        x, y, w, h = self.bbox  # type: ignore[assignment]
        return Point(x=int(x + w // 2), y=int(y + h // 2))


class Action(BaseModel):
    type: ActionType
    target: Optional[Target] = None

    text: Optional[str] = None                 # only for "type"
    keys: Optional[List[str]] = None           # only for "hotkey"
    scroll_amount: Optional[int] = None        # only for "scroll"
    delay_ms: Optional[int] = None             # optional; required for "wait"
    app: Optional[str] = None                  # only for "open_app"

    class Config:
        extra = "forbid"

    @root_validator
    def _validate_combos(cls, values: Dict[str, Any]) -> Dict[str, Any]:
        t = values.get("type")
        target = values.get("target")
        text = values.get("text")
        keys = values.get("keys")
        scroll_amount = values.get("scroll_amount")
        delay_ms = values.get("delay_ms")
        app = values.get("app")

        # target rules
        if t in ("click", "double_click", "right_click", "move"):
            if target is None:
                raise ValueError(f"{t} requires target")
        elif t == "type":
            pass  # type MAY include an optional target (field to click before typing)
        else:
            if target is not None:
                raise ValueError(f"{t} must not include target")

        # per-type required fields + forbids
        if t == "type":
            if not text:
                raise ValueError("type requires non-empty text")
            if keys is not None or scroll_amount is not None or app is not None:
                raise ValueError("type must not include keys/scroll_amount/app")

        if t == "hotkey":
            if not keys or len(keys) == 0:
                raise ValueError("hotkey requires non-empty keys")
            if text is not None or scroll_amount is not None or app is not None:
                raise ValueError("hotkey must not include text/scroll_amount/app")

        if t == "scroll":
            if scroll_amount is None:
                raise ValueError("scroll requires scroll_amount")
            if text is not None or keys is not None or app is not None:
                raise ValueError("scroll must not include text/keys/app")

        if t == "wait":
            if delay_ms is None or delay_ms < 0:
                raise ValueError("wait requires delay_ms >= 0")
            if text is not None or keys is not None or scroll_amount is not None or app is not None:
                raise ValueError("wait must not include text/keys/scroll_amount/app")

        if t == "open_app":
            if not app:
                raise ValueError("open_app requires app")
            if text is not None or keys is not None or scroll_amount is not None:
                raise ValueError("open_app must not include text/keys/scroll_amount")

        # delay_ms (optional) can exist on any action, but must be >= 0
        if delay_ms is not None and delay_ms < 0:
            raise ValueError("delay_ms must be >= 0")

        return values


class StopRequested(RuntimeError):
    pass


@dataclass
class ExecResult:
    ok: bool
    action_type: str
    detail: str = ""
    error: str = ""
    traceback_str: str = ""
    validated_action: Optional[Dict[str, Any]] = None


class Executor:
    """
    Dumb Windows action runner (pyautogui primary).
    - strict schema validation
    - human-like delays
    - pyautogui.FAILSAFE=True
    - per-action error handling
    - stop flag checked frequently (incl. during waits and typing)
    """

    def __init__(
        self,
        *,
        mode: ExecMode = "confirm",
        stop_checker: Optional[Callable[[], bool]] = None,
        min_delay_s: float = 0.06,
        max_delay_s: float = 0.18,
    ) -> None:
        self.mode = mode
        self.stop_checker = stop_checker or (lambda: False)
        self.min_delay_s = min_delay_s
        self.max_delay_s = max_delay_s

        import pyautogui  # local import
        pyautogui.FAILSAFE = True
        pyautogui.PAUSE = 0.03  # small default pause after each pyautogui call
        self.pg = pyautogui

        # DPI scale: OmniParser captures at physical pixels, PyAutoGUI uses logical.
        # On a 200% HiDPI screen (2880 physical → 1440 logical), scale = 2.0.
        self._dpi_scale = self._detect_dpi_scale()

    # ---------- DPI scaling ----------
    @staticmethod
    def _detect_dpi_scale() -> float:
        """Return physical_px / logical_px ratio (e.g. 2.0 on a 200% HiDPI screen)."""
        try:
            import ctypes
            # GetScaleFactorForDevice returns integer percent: 100, 125, 150, 200 …
            pct = ctypes.windll.shcore.GetScaleFactorForDevice(0)
            if pct and pct >= 100:
                return pct / 100.0
        except Exception:
            pass
        return 1.0

    def _to_logical(self, x: int, y: int) -> Tuple[int, int]:
        """The backend process is DPI-aware (enable_dpi_awareness), so PyAutoGUI
        operates in PHYSICAL pixels 1:1 with OmniParser's coordinates.
        Verified by scripts/verify_dpi_click_mapping.py (err_px = 0.0).
        Therefore NO scaling is applied — coordinates pass through unchanged."""
        return int(x), int(y)

    # ---------- safety ----------
    def _check_stop(self) -> None:
        if self.stop_checker():
            raise StopRequested("STOP requested")

    def _human_pause(self) -> None:
        self._check_stop()
        time.sleep(random.uniform(self.min_delay_s, self.max_delay_s))

    def _screen_guard(self, x: int, y: int) -> None:
        w, h = self.pg.size()
        if x < 0 or y < 0 or x >= w or y >= h:
            raise ValueError(f"point out of screen bounds: ({x},{y}) not in [0..{w-1}]x[0..{h-1}]")

    def _normalize_key(self, k: str) -> str:
        k = k.strip().lower()
        if k in ("control", "ctrl"):
            return "ctrl"
        if k in ("escape", "esc"):
            return "esc"
        if k == "win":
            return "winleft"
        return k

    def _is_blocked_hotkey_confirm(self, keys: Sequence[str]) -> bool:
        # minimal "risky" blocks before Perception exists
        norm = tuple(self._normalize_key(k) for k in keys)
        risky = {
            ("winleft", "r"),  # Run dialog
        }
        return norm in risky

    def _is_blocked_open_app_confirm(self, app: str) -> bool:
        app_norm = app.strip().lower()
        allow = {"notepad", "notepad.exe", "calc", "calc.exe", "mspaint", "mspaint.exe", "explorer", "explorer.exe"}
        return app_norm not in allow

    # Hard denylist — these are ALWAYS blocked, in any mode (Phase J safety)
    _HARD_DENY_APPS = {
        "cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe",
        "regedit", "regedit.exe", "diskpart", "diskpart.exe", "format", "format.com",
        "wsreset", "bcdedit", "sc", "net", "taskkill", "shutdown", "reg",
    }

    def _is_hard_denied_app(self, app: str) -> bool:
        first = app.strip().lower().split()[0] if app.strip() else ""
        return first in self._HARD_DENY_APPS

    # ---------- public ----------
    def execute(self, action_obj: Action | Dict[str, Any]) -> ExecResult:
        # strict validation first: invalid actions never execute
        try:
            act = action_obj if isinstance(action_obj, Action) else Action.parse_obj(action_obj)
        except ValidationError as e:
            return ExecResult(ok=False, action_type="invalid", error=f"Action validation error: {e}")
        except Exception as e:
            return ExecResult(ok=False, action_type="invalid", error=f"Action parse error: {e}", traceback_str=traceback.format_exc())

        # now run
        try:
            self._check_stop()

            # optional pre-delay on non-wait actions
            if act.delay_ms is not None and act.type != "wait":
                self._wait_ms(act.delay_ms)

            if act.type == "click":
                p = act.target.resolve_point()
                self._click(p.x, p.y, clicks=1, button="left")

            elif act.type == "double_click":
                p = act.target.resolve_point()
                self._click(p.x, p.y, clicks=2, button="left")

            elif act.type == "right_click":
                p = act.target.resolve_point()
                self._click(p.x, p.y, clicks=1, button="right")

            elif act.type == "move":
                p = act.target.resolve_point()
                self._move(p.x, p.y)

            elif act.type == "type":
                # If a target field is given, click it first to ensure focus
                if act.target is not None:
                    p = act.target.resolve_point()
                    self._click(p.x, p.y, clicks=1, button="left")
                    time.sleep(0.3)
                self._type_text(act.text or "")

            elif act.type == "hotkey":
                keys = act.keys or []
                if self.mode == "confirm" and self._is_blocked_hotkey_confirm(keys):
                    raise PermissionError("blocked risky hotkey in confirm mode")
                self._hotkey(keys)

            elif act.type == "scroll":
                self._scroll(act.scroll_amount or 0)

            elif act.type == "wait":
                self._wait_ms(act.delay_ms or 0)

            elif act.type == "open_app":
                if self._is_hard_denied_app(act.app or ""):
                    raise PermissionError(f"open_app permanently blocked for safety: {act.app}")
                if self.mode == "confirm" and self._is_blocked_open_app_confirm(act.app or ""):
                    raise PermissionError(f"open_app blocked in confirm mode: {act.app}")
                self._open_app(act.app or "")

            else:
                return ExecResult(ok=False, action_type=str(act.type), error=f"Unsupported action type: {act.type}")

            return ExecResult(
                ok=True,
                action_type=act.type,
                detail="executed",
                validated_action=act.dict(),
            )

        except StopRequested:
            raise
        except Exception as e:
            return ExecResult(
                ok=False,
                action_type=act.type,
                error=str(e),
                traceback_str=traceback.format_exc(),
                validated_action=act.dict(),
            )

    # ---------- helpers ----------
    def _move(self, x: int, y: int) -> None:
        lx, ly = self._to_logical(x, y)
        self._check_stop()
        self._screen_guard(lx, ly)
        self.pg.moveTo(lx, ly, duration=random.uniform(0.08, 0.18))
        self._human_pause()

    def _click(self, x: int, y: int, *, clicks: int, button: str) -> None:
        lx, ly = self._to_logical(x, y)
        self._check_stop()
        self._screen_guard(lx, ly)
        self.pg.moveTo(lx, ly, duration=random.uniform(0.08, 0.18))
        self._human_pause()
        self.pg.click(clicks=clicks, button=button)
        self._human_pause()

    def _type_text(self, text: str) -> None:
        self._check_stop()

        # For long text (e.g. an article), char-by-char is far too slow and fragile.
        # Paste via clipboard instead — instant and reliable.
        if len(text) > 80:
            try:
                import pyperclip
                pyperclip.copy(text)
                time.sleep(0.1)
                self.pg.hotkey("ctrl", "v")
                self._human_pause()
                return
            except Exception:
                pass  # fall back to char-by-char if clipboard fails

        # Short text: char-by-char (reliable + stop-responsive)
        for ch in text:
            self._check_stop()
            self.pg.write(ch)
            time.sleep(random.uniform(0.02, 0.06))
        self._human_pause()

    def _hotkey(self, keys: Sequence[str]) -> None:
        self._check_stop()
        norm = [self._normalize_key(k) for k in keys if k and k.strip()]
        if not norm:
            raise ValueError("hotkey keys empty after normalization")
        if len(norm) == 1:
            self.pg.press(norm[0])
        else:
            self.pg.hotkey(*norm)
        self._human_pause()

    def _scroll(self, amount: int) -> None:
        self._check_stop()
        self.pg.scroll(int(amount))
        self._human_pause()

    def _wait_ms(self, delay_ms: int) -> None:
        end = time.time() + (delay_ms / 1000.0)
        while time.time() < end:
            self._check_stop()
            time.sleep(0.05)

    def _open_app(self, app: str) -> None:
        self._check_stop()
        app_norm = app.strip().lower()

        # Map common app names to launch targets
        launchers = {
            "notepad": ["notepad.exe"],
            "notepad.exe": ["notepad.exe"],
            "calc": ["calc.exe"],
            "calculator": ["calc.exe"],
            "mspaint": ["mspaint.exe"],
            "paint": ["mspaint.exe"],
            "explorer": ["explorer.exe"],
            "chrome": ["cmd", "/c", "start", "", "chrome", "--profile-directory=Default"],
            "google chrome": ["cmd", "/c", "start", "", "chrome", "--profile-directory=Default"],
            "edge": ["cmd", "/c", "start", "", "msedge"],
            "msedge": ["cmd", "/c", "start", "", "msedge"],
        }

        cmd = launchers.get(app_norm)
        if cmd is not None:
            subprocess.Popen(cmd, shell=False)
        else:
            # Use the shell 'start' verb so Windows resolves the app and brings it forward
            subprocess.Popen(["cmd", "/c", "start", "", app], shell=False)

        # Apps take time to launch + render; wait longer than a normal action.
        time.sleep(2.5)
        self._human_pause()
