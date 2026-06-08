from __future__ import annotations

import base64
import io
import json
from typing import Any, Dict, Optional

from PIL import Image, ImageDraw, ImageFont

from ..perception.schema_v2 import ScreenSchemaV2
from .agents import Action, PlanStep
from .skills import skills_help
from .llm import agent_llm


_SYSTEM = """You are an autonomous computer-use agent controlling a Windows PC.
You are given an OVERALL TASK, a history of what you have already done, and the
CURRENT screen (a screenshot with numbered elements + a list). Decide the SINGLE
best next action to make progress toward the overall task.

You are ADAPTIVE: react to whatever is actually on screen RIGHT NOW. If an
unexpected screen appears (a Chrome profile picker, a cookie/consent popup, a
login wall, a "no thanks" dialog), HANDLE IT FIRST, then continue the task.
Do not blindly follow a fixed plan — follow the real screen.

To click something, you pick its ELEMENT NUMBER (#NNN) — never raw coordinates.

Available actions — return ONLY ONE valid JSON object:

  click:        {"type":"click","element_id":NNN}
  double_click: {"type":"double_click","element_id":NNN}
  right_click:  {"type":"right_click","element_id":NNN}
  type:         {"type":"type","text":"STRING","element_id":NNN}
                (element_id = the input field to click then type into; "type" is ALWAYS literally "type")
  hotkey:       {"type":"hotkey","keys":["ctrl","a"]}   (e.g. ["enter"], ["ctrl","a"])
  scroll:       {"type":"scroll","scroll_amount":-3}    (negative = down, positive = up)
  wait:         {"type":"wait","delay_ms":1500}         (use after navigation/loading)
  open_app:     {"type":"open_app","app":"chrome"}
  skill:        {"type":"skill","name":"<skill>","args":{...}}  (reliable shortcuts — see SKILLS below)
  ask_user:     {"type":"ask_user","question":"...","secret":true|false}
                (PAUSE and ask the human for input you cannot/should not provide:
                 OTP, password, payment confirmation, an address/choice. secret=true
                 hides the answer from logs. Use this instead of guessing sensitive info.)
  switch_mode:  {"type":"switch_mode","to":"web","reason":"..."}
                (Switch to the BROWSER if this part of the task needs a website. Use
                 "to":"desktop" to switch back to native Windows apps. Only switch when
                 the current environment genuinely cannot do the next step.)
  done:         {"type":"done","reason":"the task is fully complete because ..."}

PREFER SKILLS for common flows — they use reliable keyboard shortcuts instead of
hunting for elements. Especially: to browse the web use the skills, not manual clicks.

Rules:
- Return ONLY one JSON action object, nothing else.
- element_id MUST be a number from the DETECTED ELEMENTS list shown to you.
- Read each element's content text + look at the screenshot to pick the RIGHT one.
- To OPEN/LAUNCH any app or find a file — do it the reliable human way, in TWO steps:
  STEP 1: {"type":"skill","name":"start_search","args":{"query":"Notepad"}}
          This clicks the Windows icon and types the keyword, then shows the RESULTS.
  STEP 2: On the next turn you will SEE the search results in the elements list +
          screenshot. CLICK the exact correct result by its element_id (the app whose
          name matches what you want). This way you confirm you're opening the RIGHT app.
  * Use a real keyword (e.g. "Notepad", "VLC", "WhatsApp", "File Explorer").
  * NEVER click desktop icons or guess. NEVER press Enter blindly — look, then click.
- To type/search/enter text → use the "type" action WITH element_id of the field.
  After typing a URL or search query, the next action is usually hotkey ["enter"].
- If the page looks like it is still loading or blank → use wait.
- CRITICAL — read the HISTORY before acting:
  * If a recent action is marked "NO CHANGE", that action did NOTHING. Do NOT repeat
    it. Switch approach completely — type instead of click, pick a different element,
    scroll, or wait for loading.
  * NEVER click the same coordinates/element more than twice. If clicking isn't
    working, the thing you want is probably a TEXT FIELD that needs the "type" action.
- For searching (YouTube, Google, etc.): click the search box ONCE, then use the
  "type" action with the query text, then hotkey ["enter"]. Do not keep clicking.
- SAFETY — before any IRREVERSIBLE or SENSITIVE action, use ask_user to confirm first:
  * placing an order / making a payment / confirming a purchase
  * deleting files, sending an email/message, submitting a form with personal data
  * anything involving money, passwords, OTP, or personal/financial details
  Ask the human (ask_user) and wait — never enter a password/OTP/card number yourself.
- When the OVERALL TASK is fully achieved on screen, return the "done" action.
- Ignore any "Phase B" control-panel window — it is not part of the task."""


_POINT_ACTIONS = {"click", "double_click", "right_click", "move"}


def _element_center(schema: ScreenSchemaV2, element_id: int) -> Optional[tuple[int, int]]:
    """Look up the absolute screen center of a detected element by its list index."""
    items = schema.omni_items
    if not (0 <= element_id < len(items)):
        return None
    bb = items[element_id].get("bbox_abs_xywh")
    if not (isinstance(bb, list) and len(bb) == 4):
        return None
    cx = int(bb[0] + bb[2] / 2)
    cy = int(bb[1] + bb[3] / 2)
    return cx, cy


def _normalize_payload(action_type: str, raw: dict, schema: ScreenSchemaV2) -> dict:
    """Coerce the LLM's action payload into the exact format the executor schema expects."""
    # scroll: executor forbids 'target'. Keep only scroll_amount.
    if action_type == "scroll":
        amt = raw.get("scroll_amount")
        if amt is None:
            amt = raw.get("dy", -3)
        return {"scroll_amount": int(amt)}

    # type: optional element_id = the field to click before typing (ensures focus)
    if action_type == "type":
        out: dict = {"text": raw.get("text", "")}
        eid = raw.get("element_id")
        if eid is None:
            eid = raw.get("id")
        if eid is not None:
            center = _element_center(schema, int(eid))
            if center is not None:
                out["target"] = {"point": {"x": center[0], "y": center[1]}}
        return out

    # click/double_click/right_click/move: resolve element_id → exact coordinates
    if action_type in _POINT_ACTIONS:
        # Preferred path: LLM gave an element_id, we look up OmniParser's real coords
        eid = raw.get("element_id")
        if eid is None:
            eid = raw.get("id")  # tolerate alternate key
        if eid is not None:
            center = _element_center(schema, int(eid))
            if center is None:
                raise ValueError(f"element_id {eid} out of range (0..{len(schema.omni_items)-1})")
            return {"target": {"point": {"x": center[0], "y": center[1]}}}

        # Fallback: LLM gave raw coordinates (older behavior)
        if "target" in raw and isinstance(raw["target"], dict):
            return {"target": raw["target"]}
        x = raw.get("x")
        y = raw.get("y")
        if (x is None or y is None) and isinstance(raw.get("point"), dict):
            x = raw["point"].get("x")
            y = raw["point"].get("y")
        if x is None or y is None:
            raise ValueError(f"{action_type} needs element_id or x/y, got: {raw}")
        return {"target": {"point": {"x": int(x), "y": int(y)}}}

    return raw


def _draw_numbered_overlay(img: Image.Image, schema: ScreenSchemaV2) -> Image.Image:
    """Draw each element's index number on the screenshot (Set-of-Mark).
    Numbers shown match the element_id the LLM must return.
    Numbers are drawn LARGE with a contrasting background so the model can read them."""
    out = img.copy().convert("RGB")
    d = ImageDraw.Draw(out)
    vx, vy = schema.virtual_bbox.x, schema.virtual_bbox.y

    # Large font — readable even after the image is downscaled for the API
    font = None
    for fname in ("arialbd.ttf", "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(fname, 28)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    for i, it in enumerate(schema.omni_items):
        bb = it.get("bbox_abs_xywh")
        if not (isinstance(bb, list) and len(bb) == 4):
            continue
        x, y, w, h = [int(v) for v in bb]
        x -= vx
        y -= vy

        interactive = bool(it.get("interactivity"))
        color = (255, 0, 0) if interactive else (0, 120, 255)

        # thin box outline
        d.rectangle([x, y, x + w, y + h], outline=color, width=2)

        # large number tag at the TOP-LEFT corner of the element
        label = str(i)
        tb = d.textbbox((0, 0), label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        pad = 4
        lx, ly = x, y
        # background box (yellow for max contrast) + black text
        d.rectangle([lx, ly, lx + tw + pad * 2, ly + th + pad * 2], fill=(255, 255, 0))
        d.text((lx + pad, ly + pad), label, fill=(0, 0, 0), font=font)

    return out


def _encode_image(img: Image.Image, max_side: int = 1568) -> str:
    w, h = img.size
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _format_elements(schema: ScreenSchemaV2) -> str:
    lines = []
    for i, it in enumerate(schema.omni_items):
        itype   = str(it.get("type", ""))[:4]
        content = str(it.get("content") or "").strip().replace("\n", " ")[:50]
        inter   = "YES" if it.get("interactivity") else "no "
        bb = it.get("bbox_abs_xywh", [])
        if bb and len(bb) == 4:
            cx, cy = int(bb[0] + bb[2] / 2), int(bb[1] + bb[3] / 2)
            center = f"({cx:4d},{cy:4d})"
        else:
            center = "(?)"
        lines.append(f"#{i:03d} [{itype}] interact={inter} center={center} | {content}")
    return "\n".join(lines)


class Navigator:
    def __init__(self) -> None:
        self._client, self._model = agent_llm("navigator")

    def decide(
        self,
        goal: str,
        plan_text: str,
        history: list,
        screen: Optional[ScreenSchemaV2] = None,
        frame: Any = None,
    ) -> Action:
        """Adaptive ReAct decision: given the overall goal, a loose plan, the
        history of past actions, and the CURRENT screen, choose the next action."""
        if screen is None or frame is None:
            raise ValueError("Navigator requires screen and frame — is OmniParser running?")

        elements_text = _format_elements(screen)
        n = len(screen.omni_items)

        # Draw numbered marks ON the screenshot so the LLM sees each element's id
        marked = _draw_numbered_overlay(frame.rgb, screen)
        img_b64 = _encode_image(marked)

        try:
            import os as _os
            marked.save(_os.path.join(
                _os.path.dirname(__file__), "..", "..", "..",
                "logs", "perception", "last_navigator_view.png"))
        except Exception:
            pass

        # Last ~8 actions as history text
        recent = history[-8:]
        if recent:
            history_text = "\n".join(f"  {i+1}. {h}" for i, h in enumerate(recent))
        else:
            history_text = "  (nothing done yet — this is the first action)"

        user_text = (
            f"OVERALL TASK: {goal}\n\n"
            f"ROUGH PLAN (guidance only, adapt as needed):\n{plan_text}\n\n"
            f"WHAT YOU HAVE ALREADY DONE:\n{history_text}\n\n"
            f"CURRENT ACTIVE WINDOW: {screen.active_app.title}\n\n"
            f"The screenshot has each clickable element marked with its NUMBER.\n"
            f"Valid element_id values are 0 to {n - 1} ONLY.\n\n"
            f"DETECTED ELEMENTS ({n} total):\n"
            f"{elements_text}\n\n"
            "Look at the screenshot. Decide the SINGLE next action toward the overall task.\n"
            "If the task is already fully complete, return the done action.\n"
            "Return ONE JSON action."
        )

        try:
            import os, json as _json
            _debug_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs", "perception")
            os.makedirs(_debug_dir, exist_ok=True)
            with open(os.path.join(_debug_dir, "last_navigator_prompt.json"), "w", encoding="utf-8") as _f:
                _json.dump({"goal": goal, "active_app": screen.active_app.title,
                            "element_count": n, "history": recent,
                            "prompt": user_text}, _f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        system_content = _SYSTEM + "\n\nAVAILABLE SKILLS:\n" + skills_help()

        resp = self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}",
                                "detail": "high",
                            },
                        },
                        {"type": "text", "text": user_text},
                    ],
                },
            ],
            max_tokens=200,
            temperature=0.0,
        )

        content = resp.choices[0].message.content or ""
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            # Model may wrap JSON in ```fences``` or add stray text — extract the object
            import re
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if not m:
                raise ValueError(f"Navigator returned non-JSON: {content[:200]}")
            raw = json.loads(m.group(0))

        action_type = str(raw.get("type", "")).strip().lower()
        if not action_type:
            raise ValueError(f"Navigator returned no action type: {raw}")

        # Safety net: coerce malformed variants back to canonical action types
        _ALIASES = {
            "type+focus": "type", "type_focus": "type", "typefocus": "type",
            "input": "type", "write": "type",
            "leftclick": "click", "left_click": "click",
            "doubleclick": "double_click", "rightclick": "right_click",
            "press": "hotkey", "key": "hotkey", "keypress": "hotkey",
            "finish": "done", "complete": "done", "completed": "done",
        }
        action_type = _ALIASES.get(action_type, action_type)

        # Terminal signals — no executor payload to build
        if action_type in ("done", "fail"):
            return Action(kind=action_type, payload={"reason": raw.get("reason", "")})

        # Skill invocation (Phase I) — runner expands into an action sequence
        if action_type == "skill":
            args = raw.get("args", {})
            if not isinstance(args, dict):
                args = {}
            # Fallback: if the model put args at the top level (e.g. "query":"..."),
            # gather any extra keys besides type/name/args into args.
            if not args:
                args = {k: v for k, v in raw.items() if k not in ("type", "name", "args")}
            return Action(kind="skill", payload={"name": raw.get("name", ""), "args": args})

        # Human-input request (Phase J) — runner pauses and asks the user
        if action_type in ("ask_user", "request_user_input", "request_input"):
            return Action(kind="ask_user", payload={
                "question": raw.get("question", raw.get("prompt", "Input needed")),
                "secret": bool(raw.get("secret", False)),
            })

        # Mode switch (web <-> desktop)
        if action_type in ("switch_mode", "switch"):
            return Action(kind="switch_mode", payload={
                "to": str(raw.get("to", "web")).lower(),
                "reason": raw.get("reason", ""),
            })

        payload = {k: v for k, v in raw.items() if k != "type"}
        payload = _normalize_payload(action_type, payload, screen)

        # debug: record what element was chosen and its resolved coords
        try:
            import os, json as _json
            _dbg = os.path.join(os.path.dirname(__file__), "..", "..", "..", "logs", "perception", "last_action.json")
            with open(_dbg, "w", encoding="utf-8") as _f:
                _json.dump({"raw_llm": raw, "resolved": {"kind": action_type, "payload": payload}}, _f, indent=2, ensure_ascii=False)
        except Exception:
            pass

        return Action(kind=action_type, payload=payload)

    # ── WEB (DOM) MODE ───────────────────────────────────────────────────────
    def decide_web(self, goal: str, history: list, web_state: dict,
                   screenshot_png: bytes = None) -> Action:
        """DOM-based decision: given the overall goal, history, and the page's
        indexed interactive elements (exact HTML, not OCR), choose the next web action.
        An optional screenshot lets the agent visually confirm state (e.g. video playing)."""
        elements = web_state.get("elements", [])
        url = web_state.get("url", "")
        title = web_state.get("title", "")

        lines = []
        for it in elements:
            i = it.get("index")
            tag = it.get("tag", "")
            typ = it.get("type", "")
            txt = (it.get("text") or it.get("placeholder") or "").replace("\n", " ")[:90]
            extra = f" type={typ}" if typ else ""
            href = it.get("href", "")
            hpart = f" -> {href}" if href else ""
            lines.append(f"[{i}] <{tag}{extra}> {txt}{hpart}")
        elements_text = "\n".join(lines) if lines else "(no interactive elements found)"

        recent = history[-8:]
        history_text = "\n".join(f"  {i+1}. {h}" for i, h in enumerate(recent)) if recent else "  (nothing yet)"

        user_text = (
            f"OVERALL TASK: {goal}\n\n"
            f"WHAT YOU HAVE ALREADY DONE:\n{history_text}\n\n"
            f"CURRENT PAGE: {title}\nURL: {url}\n\n"
            f"INTERACTIVE ELEMENTS ON THE PAGE (use the [number]):\n{elements_text}\n\n"
            "A screenshot of the page is attached so you can see the current state.\n"
            "Choose the SINGLE next web action toward the task. If the task is already\n"
            "achieved on screen (e.g. the requested video is playing), return done.\n"
            "Return ONE JSON object."
        )

        # Build the user message — include the screenshot if available
        user_content: Any = user_text
        if screenshot_png:
            b64 = base64.b64encode(screenshot_png).decode("utf-8")
            user_content = [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}},
                {"type": "text", "text": user_text},
            ]

        resp = self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _WEB_SYSTEM},
                {"role": "user", "content": user_content},
            ],
            max_tokens=500,
            temperature=0.0,
        )

        content = resp.choices[0].message.content or ""
        try:
            raw = json.loads(content)
        except json.JSONDecodeError:
            import re
            m = re.search(r"\{.*\}", content, re.DOTALL)
            if not m:
                raise ValueError(f"Web navigator returned non-JSON: {content[:200]}")
            raw = json.loads(m.group(0))

        kind = str(raw.get("type", "")).strip().lower()
        _ALIAS = {"goto": "web_goto", "navigate": "web_goto", "open": "web_goto",
                  "click": "web_click", "type": "web_type", "fill": "web_type",
                  "scroll": "web_scroll", "enter": "web_enter",
                  "wait": "web_wait", "reload": "web_wait", "refresh": "web_wait",
                  "finish": "done", "complete": "done", "completed": "done"}
        kind = _ALIAS.get(kind, kind)

        if kind in ("done", "fail"):
            return Action(kind=kind, payload={"reason": raw.get("reason", "")})
        if kind in ("ask_user", "request_user_input"):
            return Action(kind="ask_user", payload={
                "question": raw.get("question", "Input needed"),
                "secret": bool(raw.get("secret", False))})
        if kind in ("switch_mode", "switch"):
            return Action(kind="switch_mode", payload={
                "to": str(raw.get("to", "desktop")).lower(),
                "reason": raw.get("reason", "")})

        payload = {k: v for k, v in raw.items() if k != "type"}
        return Action(kind=kind, payload=payload)


_WEB_SYSTEM = """You are an autonomous WEB agent. You control a real Chrome browser
through its DOM (not screenshots). You are given the overall task, your action
history, and the page's INTERACTIVE ELEMENTS each with a [number].

Choose ONE action. Return ONLY valid JSON:

  web_goto:   {"type":"web_goto","url":"https://www.youtube.com"}
  web_click:  {"type":"web_click","index":N}
  web_type:   {"type":"web_type","index":N,"text":"...","enter":true}
              (fills element N with text; enter=true presses Enter after, e.g. to search)
  web_scroll: {"type":"web_scroll","amount":600}   (positive = down, negative = up)
  web_enter:  {"type":"web_enter"}
  web_wait:   {"type":"web_wait"}   (wait for the page to finish loading, then look again)
  ask_user:   {"type":"ask_user","question":"...","secret":true|false}
              (pause for OTP / password / payment confirmation — never enter these yourself)
  switch_mode:{"type":"switch_mode","to":"desktop","reason":"..."}
              (switch to native Windows apps if the next step needs a desktop app, e.g.
               to paste web content into Word. Only switch when the browser can't do it.)
  done:       {"type":"done","reason":"..."}
  fail:       {"type":"fail","reason":"..."}

Rules:
- index MUST be a [number] from the elements list. Read each element's text to pick the right one.
- To go to a site, use web_goto with the full URL.
- To search a site: web_type into the search box with enter=true (one action does it).
- For YouTube: web_goto "https://www.youtube.com/results?search_query=YOUR+QUERY" lands on results directly.
- After results load, web_click the first relevant link/video by its index.
- If you don't see what you need, web_scroll down to reveal more, then look again.
- SAFETY: before payment/purchase/delete/sending, use ask_user to confirm. Never type passwords/OTP yourself.
- Look at history: if an action shows NO CHANGE or failed, try a DIFFERENT element or approach.
- COMPLETION: if the goal was to play/watch a video and the URL now contains "/watch",
  the video is playing — return done. Use the screenshot to confirm the page shows the
  expected result. Do NOT keep clicking once the goal is visibly achieved.
- Never click sidebar/menu items (Subscriptions, Shorts, Home) unless the task needs them.
- If the page shows FEW or NO elements, it is probably still LOADING — do NOT fail
  and do NOT reload. Use web_wait to let it finish, or web_scroll to reveal content.
- NEVER use web_goto to the same URL you are already on. Do NOT reload/refresh the
  page repeatedly — if you just navigated, use web_wait instead of navigating again.
- NEVER click the same element index more than twice. If a click did not help, that
  path is a dead-end (e.g. an item that can't ship) — try a DIFFERENT element, scroll,
  or go to another retailer. Do not loop on the same element.
- Only use the "fail" action as a LAST resort after several genuinely different
  attempts have all failed — never fail just because a page looks empty once.
- When the task is fully complete, return done."""
