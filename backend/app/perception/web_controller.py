"""
WebController — DOM-based browser automation (the nanobrowser / browser-use approach)
====================================================================================
Instead of screenshots + OCR + pixel clicks, this drives a real Chromium via
Playwright and works directly with the page's DOM:

  - get_state()  -> indexed list of interactive elements (from real HTML, exact labels)
  - click(index) -> clicks the actual DOM element (no pixel coordinates, no OCR)
  - type(index)  -> fills the actual input field
  - goto(url), scroll(), press_enter(), screenshot()

This is FAR more reliable than vision for websites: exact labels, exact clicks,
sees the whole page, no DPI/coordinate issues. (Desktop apps still use vision.)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# JS injected into the page: tag every visible interactive element with
# data-agent-index and return a compact description list.
_BUILD_DOM_JS = r"""
() => {
  const SEL = [
    'a[href]', 'button', 'input', 'textarea', 'select',
    '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
    '[role=checkbox]', '[role=radio]', '[role=switch]', '[role=searchbox]',
    '[onclick]', '[tabindex]:not([tabindex="-1"])', '[contenteditable="true"]'
  ].join(',');

  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = window.getComputedStyle(el);
    if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
    return true;
  };

  // Clear stale indices from previous scans so numbers never collide
  document.querySelectorAll('[data-agent-index]').forEach((e) => e.removeAttribute('data-agent-index'));

  const seen = new Set();
  const out = [];
  let idx = 0;
  document.querySelectorAll(SEL).forEach((el) => {
    if (seen.has(el)) return;
    if (!isVisible(el)) return;
    seen.add(el);
    el.setAttribute('data-agent-index', String(idx));
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value || el.getAttribute('aria-label')
                  || el.getAttribute('placeholder') || el.getAttribute('title') || '')
                 .replace(/\s+/g, ' ').trim().slice(0, 120);
    const r = el.getBoundingClientRect();
    out.push({
      index: idx,
      tag: tag,
      type: el.getAttribute('type') || '',
      text: text,
      placeholder: el.getAttribute('placeholder') || '',
      role: el.getAttribute('role') || '',
      href: tag === 'a' ? (el.getAttribute('href') || '').slice(0, 100) : '',
      top: Math.round(r.top), left: Math.round(r.left),
    });
    idx++;
  });
  return out;
}
"""


class WebController:
    def __init__(self, headless: bool = False,
                 use_real_profile: bool = False,
                 user_data_dir: Optional[str] = None,
                 profile_dir: str = "Default") -> None:
        self.headless = headless
        self.use_real_profile = use_real_profile
        self.user_data_dir = user_data_dir
        self.profile_dir = profile_dir or "Default"
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._elements: List[Dict[str, Any]] = []

    @staticmethod
    def _default_user_data_dir() -> str:
        import os
        return os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()

        common_args = [
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",          # hide the automation/notice infobar
            "--no-first-run",
            "--no-default-browser-check",
        ]
        # Strip Playwright's automation flags that trigger the warning banners
        ignore = ["--enable-automation", "--no-sandbox"]

        if self.use_real_profile:
            # Use the user's REAL Chrome profile (logins, history, extensions).
            # NOTE: the user's Chrome must be FULLY CLOSED — a profile can't be
            # opened by two processes at once.
            udd = self.user_data_dir or self._default_user_data_dir()
            import os as _os
            _os.makedirs(udd, exist_ok=True)   # dedicated agent profile dir
            self._context = self._pw.chromium.launch_persistent_context(
                user_data_dir=udd,
                channel="chrome",            # use installed Chrome, not bundled Chromium
                headless=self.headless,
                no_viewport=True,
                ignore_default_args=ignore,
                args=common_args + [f"--profile-directory={self.profile_dir}"],
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            # Fresh isolated Chromium (default — clean, no profile)
            self._browser = self._pw.chromium.launch(
                headless=self.headless, ignore_default_args=ignore, args=common_args)
            self._context = self._browser.new_context(no_viewport=True)
            self._page = self._context.new_page()

        # Land on a real starting page so the first DOM scan isn't an empty about:blank
        try:
            if not self._page.url or self._page.url.startswith("about:"):
                self._page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=20000)
        except Exception:
            pass

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None

    # ── perception ───────────────────────────────────────────────────────────
    def goto(self, url: str) -> None:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._page.wait_for_timeout(1200)

    def get_state(self) -> Dict[str, Any]:
        """Return the current page's URL/title + indexed interactive elements.
        Retries a few times if the page is still loading (heavy JS sites like Amazon)."""
        elements: List[Dict[str, Any]] = []
        for attempt in range(4):
            try:
                # let dynamic content settle (network + render)
                try:
                    self._page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:
                    pass
                self._page.wait_for_timeout(500)
                elements = self._page.evaluate(_BUILD_DOM_JS)
            except Exception:
                elements = []
            if elements:
                break
            self._page.wait_for_timeout(1200)  # wait and retry — page may still be rendering
        self._elements = elements
        return {
            "url": self._page.url,
            "title": self._page.title(),
            "elements": elements,
        }

    def screenshot_png(self) -> Optional[bytes]:
        try:
            return self._page.screenshot(type="png", full_page=False)
        except Exception:
            return None

    def bring_to_front(self) -> None:
        """Raise the browser window so the user can see the page (e.g. when asked for input)."""
        try:
            self._page.bring_to_front()
        except Exception:
            pass

    # ── actions (by element index from the last get_state) ───────────────────
    def _sel(self, index: int) -> str:
        return f'[data-agent-index="{index}"]'

    def click(self, index: int) -> Dict[str, Any]:
        sel = self._sel(index)
        # 1) normal click (waits for visible/stable)
        try:
            self._page.click(sel, timeout=3000)
            self._page.wait_for_timeout(800)
            return {"ok": True}
        except Exception:
            pass
        # 2) force click (skip visibility/stability checks)
        try:
            self._page.click(sel, timeout=2000, force=True)
            self._page.wait_for_timeout(800)
            return {"ok": True}
        except Exception:
            pass
        # 3) direct DOM click via JS (works even when Playwright deems it "not visible")
        try:
            ok = self._page.evaluate(
                """(i) => { const el = document.querySelector('[data-agent-index="'+i+'"]');
                           if (el) { el.click(); return true; } return false; }""",
                str(index),
            )
            self._page.wait_for_timeout(800)
            if ok:
                return {"ok": True}
            return {"ok": False, "error": f"click[{index}] failed: element not found"}
        except Exception as e:
            return {"ok": False, "error": f"click[{index}] failed: {e}"}

    def type(self, index: int, text: str, enter: bool = False) -> Dict[str, Any]:
        try:
            self._page.fill(self._sel(index), text, timeout=5000)
            if enter:
                self._page.keyboard.press("Enter")
                self._page.wait_for_timeout(1200)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"type[{index}] failed: {e}"}

    def press_enter(self) -> Dict[str, Any]:
        try:
            self._page.keyboard.press("Enter")
            self._page.wait_for_timeout(1000)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"enter failed: {e}"}

    def scroll(self, amount: int) -> Dict[str, Any]:
        try:
            self._page.mouse.wheel(0, amount)
            self._page.wait_for_timeout(500)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"scroll failed: {e}"}
