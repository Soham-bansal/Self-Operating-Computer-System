import os
import sys
import time
import threading
from typing import Any, Dict, List, Optional, Callable

import httpx
from pynput.keyboard import GlobalHotKeys

from PySide6.QtCore import QTimer, QObject, Signal, QRunnable, QThreadPool, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QPlainTextEdit,
    QGroupBox,
    QMessageBox,
    QCheckBox,
    QLineEdit,
    QInputDialog,
    QComboBox,
    QDialog,
    QFormLayout,
)


# ----------------------------
# .env read/write (for the Settings tab)
# ----------------------------
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def read_env() -> Dict[str, str]:
    d: Dict[str, str] = {}
    if os.path.exists(ENV_PATH):
        try:
            with open(ENV_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    d[k.strip()] = v.strip()
        except Exception:
            pass
    return d


def write_env(updates: Dict[str, str]) -> None:
    """Update the given keys in .env, preserving all other lines/comments."""
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    seen = set()
    out = []
    for line in lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=", 1)[0].strip()
            if k in updates:
                out.append(f"{k}={updates[k]}")
                seen.add(k)
                continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# ----------------------------
# Backend client (matches your Phase A)
# ----------------------------
class BackendClient:
    def __init__(self, base_url: str, timeout_s: float = 6.0):
        self.base_url = base_url.rstrip("/")
        self._timeout = timeout_s

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self._timeout) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            return r.json() if r.content else {}

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(url, params=params)
            r.raise_for_status()
            return r.json() if r.content else {}

    def run(self, prompt: str, mode: str) -> Dict[str, Any]:
        return self._post("/run", {"mode": mode, "prompt": prompt})

    def pause(self, run_id: str) -> Dict[str, Any]:
        return self._post("/pause", {"run_id": run_id})

    def resume(self, run_id: str) -> Dict[str, Any]:
        return self._post("/resume", {"run_id": run_id})

    def stop(self, run_id: str) -> Dict[str, Any]:
        return self._post("/stop", {"run_id": run_id})

    def status(self, run_id: str) -> Dict[str, Any]:
        return self._get("/status", {"run_id": run_id})

    def respond(self, run_id: str, response: str) -> Dict[str, Any]:
        return self._post("/respond", {"run_id": run_id, "response": response})


# ----------------------------
# Helpers
# ----------------------------
def _now_ts() -> str:
    return time.strftime("%H:%M:%S")


def is_error_log_line(line: str) -> bool:
    s = (line or "")
    return " FAIL " in s or s.strip().endswith(" FAIL") or "last_error=" in s or "ERROR" in s


# ----------------------------
# Async worker (FIXED: signal lifetime + cleanup)
# ----------------------------
class WorkerSignals(QObject):
    ok = Signal(object)
    err = Signal(str)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)


class HttpTask(QRunnable):
    """
    QRunnable must be kept alive, otherwise:
    RuntimeError: Signal source has been deleted
    """
    def __init__(self, fn: Callable, *args, parent=None, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals(parent)

    def run(self):
        try:
            res = self.fn(*self.args, **self.kwargs)
            self.signals.ok.emit(res)
        except Exception as e:
            # if UI is closing, emitting may fail; guard it
            try:
                self.signals.err.emit(str(e))
            except Exception:
                pass
        finally:
            try:
                self.signals.finished.emit()
            except Exception:
                pass


# ----------------------------
# Global hotkeys manager
# ----------------------------
class HotkeyManager(QObject):
    stop_requested = Signal()
    pause_resume_requested = Signal()

    def __init__(self):
        super().__init__()
        self._thread: Optional[threading.Thread] = None
        self._listener: Optional[GlobalHotKeys] = None

    def start(self):
        hotkeys = {
            "<ctrl>+<shift>+q": lambda: self.stop_requested.emit(),
            "<ctrl>+<shift>+p": lambda: self.pause_resume_requested.emit(),
        }

        def _run():
            self._listener = GlobalHotKeys(hotkeys)
            self._listener.start()
            self._listener.join()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            if self._listener is not None:
                self._listener.stop()
        except Exception:
            pass


# ----------------------------
# Settings dialog (API keys + per-agent provider/model)
# ----------------------------
class SettingsDialog(QDialog):
    PROVIDERS = ["openai", "gemini", "ollama"]
    AGENTS = ["PLANNER", "NAVIGATOR", "VERIFIER"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(560)
        env = read_env()

        root = QVBoxLayout(self)

        # --- API keys ---
        keys_box = QGroupBox("API Keys")
        keys_form = QFormLayout(keys_box)
        self.openai_key = QLineEdit(env.get("OPENAI_API_KEY", ""))
        self.openai_key.setEchoMode(QLineEdit.Password)
        self.gemini_key = QLineEdit(env.get("GEMINI_API_KEY", ""))
        self.gemini_key.setEchoMode(QLineEdit.Password)
        self.ollama_url = QLineEdit(env.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"))
        keys_form.addRow("OpenAI API key:", self.openai_key)
        keys_form.addRow("Gemini API key:", self.gemini_key)
        keys_form.addRow("Ollama base URL:", self.ollama_url)
        root.addWidget(keys_box)

        # --- per-agent provider + model ---
        self.provider_combos = {}
        self.model_edits = {}
        for agent in self.AGENTS:
            box = QGroupBox(agent.capitalize())
            form = QFormLayout(box)
            combo = QComboBox()
            combo.addItems(self.PROVIDERS)
            cur = (env.get(f"{agent}_PROVIDER", "openai") or "openai").lower()
            if cur in self.PROVIDERS:
                combo.setCurrentText(cur)
            model = QLineEdit(env.get(f"MODEL_{agent}", ""))
            model.setPlaceholderText("e.g. gpt-4o / gemini-2.5-flash / llama3.2-vision")
            form.addRow("Provider:", combo)
            form.addRow("Model:", model)
            root.addWidget(box)
            self.provider_combos[agent] = combo
            self.model_edits[agent] = model

        hint = QLabel("Tip: Navigator needs a VISION model (gpt-4o, gemini-2.5-flash, "
                      "llama3.2-vision). Changes apply on your next Run.")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # --- buttons ---
        row = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        row.addStretch(1)
        row.addWidget(save_btn)
        row.addWidget(cancel_btn)
        root.addLayout(row)
        save_btn.clicked.connect(self._save)
        cancel_btn.clicked.connect(self.reject)

    def _save(self):
        updates = {
            "OPENAI_API_KEY": self.openai_key.text().strip(),
            "GEMINI_API_KEY": self.gemini_key.text().strip(),
            "OLLAMA_BASE_URL": self.ollama_url.text().strip() or "http://localhost:11434/v1",
        }
        for agent in self.AGENTS:
            updates[f"{agent}_PROVIDER"] = self.provider_combos[agent].currentText()
            updates[f"MODEL_{agent}"] = self.model_edits[agent].text().strip()
        try:
            write_env(updates)
            QMessageBox.information(self, "Settings saved",
                                   "Saved to .env. Changes apply on your next Run.")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", f"Could not write .env:\n{e}")


# ----------------------------
# Main Window
# ----------------------------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Phase B — Desktop UI + Controls (PySide6)")
        self.resize(1120, 740)

        self.threadpool = QThreadPool.globalInstance()

        # runtime state
        self.active_run_id: Optional[str] = None
        self.backend_status: str = "idle"
        self._asking_user: bool = False   # guard so we show the input dialog only once

        # IMPORTANT: keep workers alive + avoid overlapping poll
        self._inflight_workers = set()
        self._poll_in_flight: bool = False
        self._closing: bool = False

        # log state
        self._all_logs: List[str] = []
        self._last_seen_log_count: int = 0

        # backend
        default_url = os.getenv("BACKEND_URL", "http://127.0.0.1:8000")
        self.backend = BackendClient(default_url)

        # UI
        root = QWidget()
        self.setCentralWidget(root)
        main = QVBoxLayout(root)
        main.setContentsMargins(14, 14, 14, 14)
        main.setSpacing(10)

        # Header row with title + Settings button
        header = QHBoxLayout()
        title = QLabel("Self-Operating System")
        title.setStyleSheet("font-size:16px; font-weight:bold;")
        header.addWidget(title)
        header.addStretch(1)
        self.btn_settings = QPushButton("⚙  Settings")
        self.btn_settings.clicked.connect(self.on_open_settings)
        header.addWidget(self.btn_settings)
        main.addLayout(header)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        main.addLayout(top_row, 3)

        # Prompt
        prompt_box = QGroupBox("Prompt")
        prompt_layout = QVBoxLayout(prompt_box)
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("Enter your prompt here… (multi-line)")
        self.prompt_edit.setMinimumHeight(190)
        prompt_layout.addWidget(self.prompt_edit)
        top_row.addWidget(prompt_box, 3)

        # Controls + status
        right = QVBoxLayout()
        right.setSpacing(10)
        top_row.addLayout(right, 2)

        # Backend URL
        url_row = QHBoxLayout()
        url_row.addWidget(QLabel("Backend URL:"))
        self.backend_url_edit = QLineEdit(self.backend.base_url)
        self.backend_url_edit.setPlaceholderText("http://127.0.0.1:8000")
        url_row.addWidget(self.backend_url_edit, 1)
        self.btn_apply_url = QPushButton("Apply")
        self.btn_apply_url.clicked.connect(self.on_apply_url)
        url_row.addWidget(self.btn_apply_url)
        right.addLayout(url_row)

        # Buttons
        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Run")
        self.btn_pause = QPushButton("Pause")
        self.btn_stop = QPushButton("Stop")
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(self.btn_pause)
        btn_row.addWidget(self.btn_stop)
        right.addLayout(btn_row)

        # Emergency Stop
        self.btn_emergency = QPushButton("EMERGENCY STOP")
        self.btn_emergency.setMinimumHeight(54)
        self.btn_emergency.setStyleSheet(
            "QPushButton { background-color: #C62828; color: white; font-weight: 800; border-radius: 10px; }"
            "QPushButton:disabled { background-color: #8E8E8E; }"
        )
        right.addWidget(self.btn_emergency)

        # Status panel
        status_box = QGroupBox("Current Status")
        grid = QGridLayout(status_box)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)

        self.lbl_run_id = QLabel("-")
        self.lbl_status = QLabel("IDLE")
        self.lbl_phase = QLabel("IDLE")
        self.lbl_step = QLabel("-")
        self.lbl_attempt = QLabel("-")
        self.lbl_created = QLabel("-")
        self.lbl_updated = QLabel("-")
        self.lbl_last_error = QLabel("-")

        grid.addWidget(QLabel("Run ID:"), 0, 0)
        grid.addWidget(self.lbl_run_id, 0, 1)
        self.btn_copy_runid = QPushButton("Copy")
        self.btn_copy_runid.clicked.connect(self.on_copy_runid)
        grid.addWidget(self.btn_copy_runid, 0, 2)

        grid.addWidget(QLabel("Status:"), 1, 0)
        grid.addWidget(self.lbl_status, 1, 1)

        grid.addWidget(QLabel("Phase:"), 2, 0)
        grid.addWidget(self.lbl_phase, 2, 1)

        grid.addWidget(QLabel("Step ID:"), 3, 0)
        grid.addWidget(self.lbl_step, 3, 1)

        grid.addWidget(QLabel("Attempt:"), 4, 0)
        grid.addWidget(self.lbl_attempt, 4, 1)

        grid.addWidget(QLabel("Created:"), 5, 0)
        grid.addWidget(self.lbl_created, 5, 1)

        grid.addWidget(QLabel("Updated:"), 6, 0)
        grid.addWidget(self.lbl_updated, 6, 1)

        grid.addWidget(QLabel("Last error:"), 7, 0)
        grid.addWidget(self.lbl_last_error, 7, 1)

        right.addWidget(status_box)

        # Logs panel
        logs_box = QGroupBox("Live Logs")
        logs_layout = QVBoxLayout(logs_box)

        logs_controls = QHBoxLayout()
        self.chk_errors_only = QCheckBox("Show only errors")
        self.chk_errors_only.stateChanged.connect(self.refresh_log_view)
        self.btn_clear_logs = QPushButton("Clear logs")
        self.btn_clear_logs.clicked.connect(self.on_clear_logs)
        logs_controls.addWidget(self.chk_errors_only)
        logs_controls.addStretch(1)
        logs_controls.addWidget(self.btn_clear_logs)
        logs_layout.addLayout(logs_controls)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.log_view.setFont(mono)
        logs_layout.addWidget(self.log_view, 1)

        main.addWidget(logs_box, 5)

        # Connect buttons
        self.btn_run.clicked.connect(self.on_run)
        self.btn_pause.clicked.connect(self.on_pause_resume)
        self.btn_stop.clicked.connect(self.on_stop)
        self.btn_emergency.clicked.connect(self.on_emergency_stop)

        # Poll timer
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(700)
        self.poll_timer.timeout.connect(self.poll_status)

        # Hotkeys
        self.hotkeys = HotkeyManager()
        self.hotkeys.stop_requested.connect(self.on_emergency_stop)
        self.hotkeys.pause_resume_requested.connect(self.on_pause_resume)
        self.hotkeys.start()

        # Initial state
        self.set_active_ui(False)
        self.append_local_log("UI started. Hotkeys: Ctrl+Shift+P pause/resume, Ctrl+Shift+Q stop.")

    # ---------- worker helper ----------
    def _start_task(self, fn: Callable, *, ok=None, err=None, poll: bool = False):
        if self._closing:
            return
        if poll and self._poll_in_flight:
            return

        task = HttpTask(fn, parent=self)
        self._inflight_workers.add(task)

        if poll:
            self._poll_in_flight = True

        def _cleanup():
            self._inflight_workers.discard(task)
            if poll:
                self._poll_in_flight = False

        task.signals.finished.connect(_cleanup)

        if ok is not None:
            task.signals.ok.connect(ok)
        if err is not None:
            task.signals.err.connect(err)

        self.threadpool.start(task)

    # ---------- UX ----------
    def show_error(self, title: str, msg: str):
        QMessageBox.critical(self, title, msg)

    def set_active_ui(self, active: bool):
        self.btn_run.setEnabled(not active)
        self.btn_pause.setEnabled(active)
        self.btn_stop.setEnabled(active)
        self.btn_emergency.setEnabled(active)
        self.btn_copy_runid.setEnabled(bool(self.active_run_id))
        if not active:
            self.btn_pause.setText("Pause")

    def append_local_log(self, msg: str):
        self._all_logs.append(f"[{_now_ts()}] UI {msg}")
        self.refresh_log_view(scroll_to_bottom=True)

    def refresh_log_view(self, scroll_to_bottom: bool = False):
        errors_only = self.chk_errors_only.isChecked()
        lines = []
        for line in self._all_logs:
            if errors_only and not is_error_log_line(line):
                continue
            lines.append(line)
        self.log_view.setPlainText("\n".join(lines))
        if scroll_to_bottom:
            self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def on_apply_url(self):
        url = self.backend_url_edit.text().strip()
        if not url:
            return
        self.backend = BackendClient(url)
        self.append_local_log(f"Backend URL set to {self.backend.base_url}")

    # ---------- Actions ----------
    def on_open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    def on_run(self):
        prompt = self.prompt_edit.toPlainText().strip()
        mode = "Autonomous"  # only mode now

        if not prompt:
            self.show_error("Missing prompt", "Please enter a prompt before running.")
            return

        self._all_logs.clear()
        self._last_seen_log_count = 0
        self.refresh_log_view()

        self.append_local_log("Run requested.")
        self.set_active_ui(True)

        self._start_task(
            lambda: self.backend.run(prompt, mode),
            ok=self._on_run_ok,
            err=self._on_run_err,
        )

    def _on_run_ok(self, res: Dict[str, Any]):
        run_id = res.get("run_id")
        status = res.get("status")
        mode = res.get("mode")

        if not run_id:
            self.set_active_ui(False)
            self.show_error("Run failed", f"Backend returned no run_id.\n\nResponse: {res}")
            return

        self.active_run_id = str(run_id)
        self.backend_status = str(status or "running").lower()

        self.lbl_run_id.setText(self.active_run_id)
        self.lbl_status.setText(f"{status} ({mode})")
        self.lbl_phase.setText("PLAN")
        self.lbl_step.setText("-")
        self.lbl_attempt.setText("-")
        self.lbl_last_error.setText("-")

        self.append_local_log(f"Run started. run_id={self.active_run_id}")
        self.start_polling()

        # Minimize so the agent sees the real desktop, not our UI window
        QTimer.singleShot(1500, self.showMinimized)

    def _on_run_err(self, err: str):
        self.set_active_ui(False)
        self.append_local_log(f"ERROR Backend error on /run: {err}")
        self.show_error("Backend is down / Run failed", f"Could not start run.\n\nDetails:\n{err}")

    def on_pause_resume(self):
        if not self.active_run_id:
            return

        if self.backend_status == "paused":
            self.append_local_log("Resume requested.")
            self._start_task(
                lambda: self.backend.resume(self.active_run_id),
                ok=lambda _: self.append_local_log("Resumed."),
                err=lambda e: self._on_action_err("resume", e),
            )
        else:
            self.append_local_log("Pause requested.")
            self._start_task(
                lambda: self.backend.pause(self.active_run_id),
                ok=lambda _: self.append_local_log("Paused."),
                err=lambda e: self._on_action_err("pause", e),
            )

    def on_stop(self):
        if not self.active_run_id:
            return
        self.append_local_log("Stop requested.")
        self._start_task(
            lambda: self.backend.stop(self.active_run_id),
            ok=lambda _: self.append_local_log("Stopped."),
            err=lambda e: self._on_action_err("stop", e),
        )

    def on_emergency_stop(self):
        if not self.active_run_id:
            self.append_local_log("ERROR Emergency stop pressed but no active run.")
            return
        self.append_local_log("ERROR EMERGENCY STOP triggered!")
        self._start_task(
            lambda: self.backend.stop(self.active_run_id),
            ok=lambda _: self.append_local_log("Emergency stop sent."),
            err=lambda e: self._on_action_err("stop (emergency)", e),
        )

    def _on_action_err(self, action: str, err: str):
        self.append_local_log(f"ERROR Backend error on /{action}: {err}")
        self.show_error("Backend error", f"Action '{action}' failed.\n\nDetails:\n{err}")

    # ---------- Polling ----------
    def start_polling(self):
        if self.active_run_id and not self.poll_timer.isActive():
            self.poll_timer.start()
        self.set_active_ui(True)

    def stop_polling(self):
        if self.poll_timer.isActive():
            self.poll_timer.stop()
        self.set_active_ui(False)

    def poll_status(self):
        if not self.active_run_id:
            return

        self._start_task(
            lambda: self.backend.status(self.active_run_id),
            ok=self._on_status_ok,
            err=self._on_status_err,
            poll=True,  # prevents overlap + manages _poll_in_flight
        )

    def _prompt_user_for_input(self, question: str, secret: bool, kind: str = "input") -> None:
        """Show ONLY a small input dialog on top (the main UI stays minimized so it
        does not cover the agent's work). Used for human-in-the-loop input like OTP,
        an address, or a payment confirmation the agent asks for."""
        # Do NOT restore the main window — only the dialog should appear on top.
        dlg = QInputDialog(self)
        dlg.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        dlg.setWindowTitle("Agent needs your input")
        dlg.setLabelText(question)
        dlg.setTextEchoMode(QLineEdit.Password if secret else QLineEdit.Normal)
        # center on screen (main window is minimized, so anchor to the screen)
        dlg.adjustSize()
        scr = QApplication.primaryScreen().availableGeometry()
        fg = dlg.frameGeometry()
        fg.moveCenter(scr.center())
        dlg.move(fg.topLeft())
        dlg.activateWindow()
        dlg.raise_()
        ok = dlg.exec()
        text = dlg.textValue() if ok else ""
        log_text = "[hidden]" if secret else text

        rid = self.active_run_id
        self.append_local_log(f"Answering agent: {log_text}")

        def _send():
            return self.backend.respond(rid, text)

        def _done(_res):
            self._asking_user = False

        def _err(e):
            self._asking_user = False
            self._on_action_err("respond", e)

        self._start_task(_send, ok=_done, err=_err)

    def _on_status_ok(self, s: Dict[str, Any]):
        # update status panel
        self.backend_status = str(s.get("status") or "").lower()

        self.lbl_run_id.setText(str(s.get("run_id") or "-"))
        self.lbl_status.setText(f"{s.get('status')} ({s.get('mode')})")
        self.lbl_phase.setText(str(s.get("phase") or "-"))
        self.lbl_step.setText(str(s.get("step_id")) if s.get("step_id") is not None else "-")
        self.lbl_attempt.setText(str(s.get("attempt")) if s.get("attempt") is not None else "-")
        self.lbl_created.setText(str(s.get("created_ts") or "-"))
        self.lbl_updated.setText(str(s.get("updated_ts") or "-"))
        self.lbl_last_error.setText(str(s.get("last_error")) if s.get("last_error") else "-")

        self.btn_pause.setText("Resume" if self.backend_status == "paused" else "Pause")

        # logs list[str]
        logs = s.get("logs") or []
        if isinstance(logs, list):
            if len(logs) >= self._last_seen_log_count:
                new = logs[self._last_seen_log_count:]
                self._last_seen_log_count = len(logs)
            else:
                new = logs
                self._last_seen_log_count = len(logs)

            if new:
                self._all_logs.extend(new)
                self.refresh_log_view(scroll_to_bottom=True)

        if s.get("last_error"):
            self._all_logs.append(f"[{_now_ts()}] last_error={s['last_error']}")
            self.refresh_log_view(scroll_to_bottom=True)

        # --- Phase J: the agent is asking the human for input ---
        pending_q = s.get("pending_question")
        if pending_q and not self._asking_user and self.active_run_id:
            self._asking_user = True
            self._prompt_user_for_input(str(pending_q), bool(s.get("pending_secret")),
                                        str(s.get("pending_kind") or "input"))

        if self.backend_status in ("stopped", "done", "failed", "error"):
            self.stop_polling()
            # Restore window so user can see the result
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _on_status_err(self, err: str):
        self.append_local_log(f"ERROR Polling /status failed: {err}")

    # ---------- Extras ----------
    def on_copy_runid(self):
        if not self.active_run_id:
            return
        QApplication.clipboard().setText(self.active_run_id)
        self.append_local_log("run_id copied to clipboard.")

    def on_clear_logs(self):
        self._all_logs.clear()
        self._last_seen_log_count = 0
        self.refresh_log_view()
        self.append_local_log("Logs cleared.")

    def closeEvent(self, event):
        self._closing = True
        try:
            if self.poll_timer.isActive():
                self.poll_timer.stop()
        except Exception:
            pass
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
