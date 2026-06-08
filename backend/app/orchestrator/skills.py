"""
Phase I — Skills Library
========================
Reusable, RELIABLE capabilities the Navigator can invoke instead of improvising.

Each skill expands into a deterministic sequence of executor action dicts. The
sequences are KEYBOARD-FIRST (e.g. Ctrl+L to focus the browser address bar) because
keyboard shortcuts are far more reliable than hunting for and clicking UI elements.

The Navigator invokes a skill with:
    {"type": "skill", "name": "<skill>", "args": {...}}

The runner calls expand_skill(name, args) to get the action list, then executes
each action in order (recording each in history).
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Dict, List


def _wait(ms: int) -> Dict[str, Any]:
    return {"type": "wait", "delay_ms": ms}


def _hotkey(*keys: str) -> Dict[str, Any]:
    return {"type": "hotkey", "keys": list(keys)}


def _type(text: str) -> Dict[str, Any]:
    return {"type": "type", "text": text}


def _open_app(app: str) -> Dict[str, Any]:
    return {"type": "open_app", "app": app}


# ── individual skill builders ────────────────────────────────────────────────

def open_url(url: str) -> List[Dict[str, Any]]:
    """Navigate the active browser to a URL via the address bar (Ctrl+L)."""
    return [
        _hotkey("ctrl", "l"),   # focus address bar (works in Chrome/Edge/Firefox)
        _wait(300),
        _type(url),
        _wait(200),
        _hotkey("enter"),
        _wait(2500),            # let the page load
    ]


def web_search(query: str) -> List[Dict[str, Any]]:
    """Google search for a query, directly via the results URL."""
    q = urllib.parse.quote_plus(query)
    return open_url(f"https://www.google.com/search?q={q}")


def youtube_search(query: str) -> List[Dict[str, Any]]:
    """Open YouTube search results for a query directly (most reliable path)."""
    q = urllib.parse.quote_plus(query)
    return open_url(f"https://www.youtube.com/results?search_query={q}")


def start_search(query: str) -> List[Dict[str, Any]]:
    """Open the Start menu and type a keyword, but DO NOT press Enter.
    This leaves the search RESULTS on screen so the agent can SEE them and click
    the exact correct result on the next step (the reliable, human-like way)."""
    return [
        _hotkey("winleft"),   # click the Windows/Start icon
        _wait(1200),
        _type(query),
        _wait(1800),          # let the results list populate; agent will perceive + pick
    ]


def search_app(query: str) -> List[Dict[str, Any]]:
    """Open an app via Windows Search and launch the TOP result directly (Win → type → Enter).
    Use start_search instead when you want to SEE the results and pick the exact one."""
    return [
        _hotkey("winleft"),
        _wait(1200),
        _type(query),
        _wait(1800),
        _hotkey("enter"),
        _wait(2800),
    ]


def open_chrome() -> List[Dict[str, Any]]:
    return [_open_app("chrome"), _wait(2500)]


def open_file_explorer() -> List[Dict[str, Any]]:
    return [_hotkey("winleft", "e"), _wait(1500)]


def new_tab() -> List[Dict[str, Any]]:
    return [_hotkey("ctrl", "t"), _wait(500)]


def close_tab() -> List[Dict[str, Any]]:
    return [_hotkey("ctrl", "w"), _wait(500)]


def select_all() -> List[Dict[str, Any]]:
    return [_hotkey("ctrl", "a"), _wait(150)]


def save() -> List[Dict[str, Any]]:
    return [_hotkey("ctrl", "s"), _wait(800)]


def handle_common_dialogs() -> List[Dict[str, Any]]:
    """Dismiss a common popup/dialog by pressing Escape (safe no-commit)."""
    return [_hotkey("esc"), _wait(300)]


# ── registry + metadata for the Navigator prompt ─────────────────────────────

SKILLS = {
    "start_search":         {"fn": start_search,         "args": ["query"], "desc": "Click the Windows icon and type a keyword (NO Enter) — leaves the search RESULTS on screen so you can SEE them and click the exact correct one next. PREFERRED way to open apps/files."},
    "search_app":           {"fn": search_app,           "args": ["query"], "desc": "Open an app by launching the TOP Windows Search result directly (Win → type → Enter). Only if you're sure the top result is correct."},
    "open_url":              {"fn": open_url,              "args": ["url"],   "desc": "Navigate the browser to a URL (Ctrl+L → type → Enter)."},
    "web_search":           {"fn": web_search,           "args": ["query"], "desc": "Google search a query (goes straight to results)."},
    "youtube_search":       {"fn": youtube_search,       "args": ["query"], "desc": "Open YouTube search results for a query (most reliable)."},
    "open_chrome":          {"fn": open_chrome,          "args": [],        "desc": "Launch Google Chrome."},
    "open_file_explorer":   {"fn": open_file_explorer,   "args": [],        "desc": "Open Windows File Explorer (Win+E)."},
    "new_tab":              {"fn": new_tab,              "args": [],        "desc": "Open a new browser tab (Ctrl+T)."},
    "close_tab":            {"fn": close_tab,            "args": [],        "desc": "Close the current browser tab (Ctrl+W)."},
    "select_all":           {"fn": select_all,           "args": [],        "desc": "Select all (Ctrl+A)."},
    "save":                 {"fn": save,                 "args": [],        "desc": "Save the current document (Ctrl+S)."},
    "handle_common_dialogs":{"fn": handle_common_dialogs,"args": [],        "desc": "Dismiss a popup/dialog (Escape)."},
}


def expand_skill(name: str, args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the executor action sequence for a named skill."""
    spec = SKILLS.get(name)
    if spec is None:
        raise ValueError(f"unknown skill '{name}'. Available: {', '.join(SKILLS)}")
    fn = spec["fn"]
    needed = spec["args"]
    kwargs = {k: args.get(k) for k in needed}
    missing = [k for k in needed if not kwargs.get(k)]
    if missing:
        raise ValueError(f"skill '{name}' missing args: {missing}")
    return fn(**kwargs)


def skills_help() -> str:
    """Human/LLM-readable list of skills for the Navigator system prompt."""
    lines = []
    for name, spec in SKILLS.items():
        if spec["args"]:
            args_obj = ",".join(f'"{a}":"..."' for a in spec["args"])
            args = f',"args":{{{args_obj}}}'
        else:
            args = ""
        lines.append(f'  {{"type":"skill","name":"{name}"{args}}}  — {spec["desc"]}')
    return "\n".join(lines)
