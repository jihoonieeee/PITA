"""
cdp_browser_tools.py
====================
Custom Inspect tools that connect to a REAL running browser via Chrome
DevTools Protocol (CDP) instead of launching a headless instance.

Why this is needed:
  The Mintlify AI assistant at code.claude.com/docs rejects requests from
  headless browsers with "Your request could not be verified" — its CSRF
  token is tied to a real browser session. Attaching to a browser the user
  already has open passes all verification automatically.

User setup (one-time before running the eval):
  # macOS
  open -a "Google Chrome" --args --remote-debugging-port=9222 \
    --user-data-dir=/tmp/chrome-inspect

  # Linux
  google-chrome --remote-debugging-port=9222 \
    --user-data-dir=/tmp/chrome-inspect

  # Windows
  chrome.exe --remote-debugging-port=9222 --user-data-dir=C:/tmp/chrome-inspect

  Then navigate to: https://code.claude.com/docs/en/overview

Environment:
  CDP_URL  — Chrome DevTools Protocol endpoint (default: http://localhost:9222)

Fixes vs original
-----------------
1. page.accessibility.snapshot() was removed in Playwright >= 1.44.
   Replaced with page.ariaSnapshot() with a plain-text DOM fallback.

2. browser.close() must NOT be called on a CDP-connected browser.
   connect_over_cdp() gives a *borrowed* reference to the real Chrome
   process -- closing it tears down the entire session. Only p.stop() is
   called in finally blocks.

3. 10 concurrent samples all interacting with the same browser page
   caused CancelledError cascade. Fixed with a process-wide asyncio.Lock
   so only one tool call drives the browser at a time.

4. cdp_click used three successive fallback strategies that could misfire:
   - get_by_role("button", name=...) -- correct for "Toggle assistant panel"
   - page.click(selector)            -- treats the name as a CSS selector,
                                        matched unrelated elements and opened
                                        the search bar instead
   - get_by_text(exact=True)         -- ambiguous on pages with repeated text
   Replaced with a targeted locator strategy: role+name first, then
   aria-label attribute, then visible text -- no CSS-selector fallback.

5. "Toggle assistant panel" click appeared to succeed but the panel never
   opened (snapshot unchanged). Added _ensure_assistant_panel_open() which
   checks for the "Ask a question..." placeholder after clicking, and retries
   once if it's still not visible.
"""

import asyncio
import json
import os
import sys
from contextvars import ContextVar

# Windows terminals default to cp1252 which cannot encode Unicode characters
# (e.g. emojis in chatbot responses). Reconfigure stdout/stderr to UTF-8 so
# debug print() calls don't raise UnicodeEncodeError and kill the relay.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from inspect_ai.tool import Tool, tool

CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")

# JS snippet exposing querySelectorAllDeep — a shadow-DOM-piercing variant of
# document.querySelectorAll. Used when a chatbot config sets is_shadow_dom=true
# (e.g. Vica's "Ask Jaga" widget under div#webchat). Plain querySelectorAll
# does NOT cross shadow boundaries, so messages inside an open shadow root are
# invisible to it; this helper BFS-walks every shadowRoot it can reach.
_SHADOW_HELPER_JS = """
const querySelectorAllDeep = (sel, root) => {
    root = root || document;
    const out = [];
    const queue = [root];
    while (queue.length) {
        const node = queue.shift();
        try { for (const m of node.querySelectorAll(sel)) out.push(m); } catch (e) {}
        let descendants = [];
        try { descendants = node.querySelectorAll('*'); } catch (e) {}
        for (const el of descendants) {
            if (el.shadowRoot) queue.push(el.shadowRoot);
        }
    }
    return out;
};
"""

# Global context to store the browser connection across tool calls in a sample
_browser_context: ContextVar = ContextVar('browser_context', default=None)

# Async lock for proper async serialization of tool calls
_tool_lock = None

class BrowserContext:
    """Manages a shared browser connection across multiple tool calls."""
    def __init__(self):
        self.p = None
        self.browser = None
        self.page = None

    async def connect(self):
        """Connect if not already connected."""
        if self.page and not self.page.is_closed():
            return

        from playwright.async_api import async_playwright
        self.p = await async_playwright().start()
        try:
            self.browser = await self.p.chromium.connect_over_cdp(CDP_URL)
            contexts = self.browser.contexts
            if not contexts:
                raise RuntimeError("No browser contexts open")
            pages = contexts[0].pages
            if not pages:
                raise RuntimeError("No pages open")
            self.page = pages[0]
        except Exception as e:
            await self.p.stop()
            raise RuntimeError(
                f"Cannot connect to Chrome at {CDP_URL}. "
                f"Start Chrome with --remote-debugging-port=9222. Error: {e}"
            )

    async def close(self):
        """Close the connection."""
        if self.p:
            await self.p.stop()
            self.p = None
            self.browser = None
            self.page = None


def get_or_create_browser():
    """Get the browser context for this task, creating if needed."""
    ctx = _browser_context.get()
    if ctx is None:
        ctx = BrowserContext()
        _browser_context.set(ctx)
    return ctx


class _ToolLockContext:
    """Async context manager for proper async tool lock."""
    async def __aenter__(self):
        global _tool_lock
        if _tool_lock is None:
            _tool_lock = asyncio.Lock()
        self._lock = _tool_lock
        await self._lock.acquire()
        return self

    async def __aexit__(self, *args):
        self._lock.release()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _snapshot(page) -> str:
    """
    Return an accessibility/aria snapshot of the page.

    Strategy (in order):
      1. page.ariaSnapshot()  -- Playwright >= 1.44, returns YAML-like string.
      2. page.evaluate() DOM walk -- fallback for older versions.
      3. Sentinel string if both fail.
    """
    if hasattr(page, "ariaSnapshot"):
        try:
            snap = await page.ariaSnapshot()
            return snap if snap else "(empty page)"
        except Exception:
            pass

    try:
        nodes = await page.evaluate("""() => {
            const els = Array.from(document.querySelectorAll(
                'a,button,input,select,textarea,[role],[aria-label]'
            )).slice(0, 200);
            return els.map(el => ({
                role: el.getAttribute('role') || el.tagName.toLowerCase(),
                name: el.getAttribute('aria-label') || el.innerText?.trim().slice(0, 80) || el.value || '',
                focused: document.activeElement === el,
            }));
        }""")
        if nodes:
            lines = []
            for n in nodes:
                line = f"[{n.get('role','')}] \"{n.get('name','')}\""
                if n.get("focused"):
                    line += "  [focused: True]"
                lines.append(line)
            return "\n".join(lines)
    except Exception:
        pass

    return "(empty page)"


async def _get_page():
    """
    Get the shared page for this task.

    Reuses the same page across multiple tool calls in the same sample.
    Only creates a new connection if one doesn't exist.
    """
    ctx = get_or_create_browser()
    await ctx.connect()
    return ctx.page


async def _safe_click(page, selector: str) -> None:
    """
    Click a button by accessible name without falling back to CSS selectors.

    Fallback order (all role/aria-based, never raw CSS):
      1. get_by_role("button", name=selector, exact=True)
      2. get_by_label(selector, exact=True)   -- for aria-label matches
      3. get_by_role("button", name=selector)  -- loose name match
      4. get_by_text(selector, exact=True) first visible element

    The original code's page.click(selector) treated the accessible name as
    a CSS selector, which matched unrelated elements (e.g. "Toggle assistant
    panel" as CSS hit nothing useful, then fell through to get_by_text which
    was ambiguous and opened the search bar instead).
    """
    errors = []

    # 1. Exact role+name
    try:
        await page.get_by_role("button", name=selector, exact=True).first.click(timeout=5000)
        return
    except Exception as e:
        errors.append(f"role+exact: {e}")

    # 2. aria-label exact match
    try:
        await page.get_by_label(selector, exact=True).first.click(timeout=5000)
        return
    except Exception as e:
        errors.append(f"label: {e}")

    # 3. Loose role+name
    try:
        await page.get_by_role("button", name=selector).first.click(timeout=5000)
        return
    except Exception as e:
        errors.append(f"role+loose: {e}")

    # 4. Visible text (last resort -- no CSS selector fallback)
    try:
        await page.get_by_text(selector, exact=True).first.click(timeout=5000)
        return
    except Exception as e:
        errors.append(f"text: {e}")

    raise RuntimeError(
        f"cdp_click: could not click {selector!r}. Attempts: {'; '.join(errors)}"
    )


def _chat_input(page, config=None):
    """
    Return a locator for the chat input field.

    Args:
        page: Playwright page object
        config: Optional chatbot config dict with 'chat_input' selector

    If no config provided, uses default Claude Docs selectors.
    Handles both main-page inputs and iframe-based inputs.
    """
    if config and config.get("chat_input") and config["chat_input"].get("selector"):
        selector = config["chat_input"]["selector"]
        print(f"[DEBUG _chat_input] Using custom selector: {selector}")

        # Check if this is an iframe-based input
        if config.get("is_iframe"):
            iframe_index = config.get("iframe_index", 0)
            print(f"[DEBUG _chat_input] Input is iframe-based (index {iframe_index})")
            try:
                frames = page.frames
                iframe_frames = [f for f in frames if f != page.main_frame]
                if iframe_index < len(iframe_frames):
                    frame = iframe_frames[iframe_index]
                    print(f"[DEBUG _chat_input] Using locator in iframe {iframe_index}")
                    return frame.locator(selector).last
                else:
                    print(f"[DEBUG _chat_input] Iframe index {iframe_index} out of range, using main page")
            except Exception as e:
                print(f"[DEBUG _chat_input] Error getting iframe: {e}, falling back to main page")

        # Try different Playwright locator methods based on selector pattern
        if "placeholder=" in selector:
            # Extract placeholder value and use get_by_placeholder
            placeholder = selector.split('placeholder="')[1].split('"')[0]
            print(f"[DEBUG _chat_input] Using get_by_placeholder: {placeholder}")
            return page.get_by_placeholder(placeholder).last
        elif "aria-label=" in selector:
            label = selector.split('aria-label="')[1].split('"')[0]
            print(f"[DEBUG _chat_input] Using get_by_label: {label}")
            return page.get_by_label(label).last
        else:
            # Fall back to CSS selector
            print(f"[DEBUG _chat_input] Using CSS locator: {selector}")
            return page.locator(selector).last
    else:
        print(f"[DEBUG _chat_input] No config or selector, using default")
        if config:
            print(f"[DEBUG _chat_input] Config keys: {config.keys()}")
            print(f"[DEBUG _chat_input] chat_input: {config.get('chat_input')}")

    # Default for Claude Docs
    print(f"[DEBUG _chat_input] Using default selector: 'Ask a question...'")
    return page.get_by_label("Ask a question...").last


async def _ensure_assistant_panel_open(page, config=None) -> None:
    """
    Make sure the chat/input panel is visible.

    Args:
        page: Playwright page object
        config: Optional chatbot config dict

    If no toggle button is configured, assumes the panel is always open.
    For iframe-based inputs, skip the visibility check since the iframe itself
    might be hidden but the input inside is still accessible.
    """
    # Skip visibility check for iframe-based inputs
    if config and config.get("is_iframe"):
        print(f"[DEBUG _ensure_assistant_panel_open] Skipping visibility check for iframe-based input")
        return

    # Get the input field locator (uses config if provided)
    input_field = _chat_input(page, config)

    # Debug: print page info
    page_url = page.url
    page_title = await page.title()
    print(f"[DEBUG] Page: {page_title} at {page_url}")
    if config and config.get("chat_input"):
        print(f"[DEBUG] Using selector: {config['chat_input'].get('selector')}")

    # Check if already visible
    try:
        is_visible = await input_field.is_visible(timeout=2000)
        if is_visible:
            print("[DEBUG] Input field found and visible")
            return
    except Exception as e:
        print(f"[DEBUG] is_visible check failed: {e}")

    # If no toggle button configured, input should already be visible
    # (some sites like AWS don't have a toggle)
    if not config or not config.get("toggle_button") or config["toggle_button"].get("selector") is None:
        # No toggle button - scroll into view and return
        try:
            print("[DEBUG] Scrolling element into view (no toggle button)")
            await input_field.scroll_into_view_if_needed()
            await input_field.wait_for(state="visible", timeout=2000)
            print("[DEBUG] Input field is now visible")
            return
        except Exception as e:
            print(f"[DEBUG] Couldn't confirm visibility: {e}")
            # Still return — element might be visible even if wait_for fails
            print("[DEBUG] Proceeding anyway (element may already be visible)")
            return

    # Try to click toggle button (Claude Docs style)
    toggle_label = config["toggle_button"]["selector"]
    await _safe_click(page, toggle_label)

    try:
        await input_field.wait_for(state="visible", timeout=10000)
        return
    except Exception:
        pass

    # Second attempt
    await _safe_click(page, toggle_label)
    try:
        await input_field.wait_for(state="visible", timeout=10000)
    except Exception:
        url = page.url
        title = await page.title()
        raise RuntimeError(
            f"Chat input did not appear. "
            f"Page: {title!r} at {url}. "
            f"Check the toggle button selector: {toggle_label}"
        )


def _response_selectors(config):
    """Return the response selector list (config-provided or defaults)."""
    if config and config.get("response_selectors"):
        return [r["selector"] for r in config["response_selectors"]]
    return [
        '[data-testid="assistant-message"]',
        '[class*="assistant-message"]',
        '[class*="Message"]',
        '.prose',
    ]


def _query_root(page, config):
    """Return the DOM root to query against (iframe Frame or main page)."""
    if config and config.get("is_iframe"):
        try:
            iframe_index = config.get("iframe_index", 0)
            iframe_frames = [f for f in page.frames if f != page.main_frame]
            if iframe_index < len(iframe_frames):
                return iframe_frames[iframe_index]
        except Exception as e:
            print(f"[DEBUG _query_root] iframe lookup failed: {e}")
    return page


def _reset_enabled() -> bool:
    """
    True when per-prompt chat reset is turned on.

    Controlled by the RESET_BETWEEN_PROMPTS env var, which run_interactive.py
    sets from an interactive prompt. When enabled, the chat is reset before
    every prompt so each lineage attempt is scored in isolation (no warmed-up
    conversation context). Accepts 1/true/yes/y (case-insensitive).
    """
    return os.environ.get("RESET_BETWEEN_PROMPTS", "0").strip().lower() in (
        "1", "true", "yes", "y", "on",
    )


async def _reset_chat(page, config=None) -> None:
    """
    Reset the chat to an empty state BEFORE sending a prompt.

    No-op unless RESET_BETWEEN_PROMPTS is enabled. Strategy when enabled:
      1. If the config has reset_button.selector, click it (scoped to the
         iframe for iframe-based chatbots). Preferred over a full reload:
         faster, and it preserves page/widget state.
      2. Otherwise — or if the click fails and fallback is "reload" — reload
         the page. Reload is the default when no reset button is configured.

    reset_button config shape (selectors.json):
        "reset_button": {
            "selector": "button[aria-label='New chat']",   # or null
            "confirm_selector": "button:has-text('Restart chat')",  # optional
            "fallback": "reload"
        }
    confirm_selector is for chatbots whose reset opens a confirmation dialog
    (e.g. vica): the trigger is clicked first, then the confirm control.
    """
    if not _reset_enabled():
        return

    reset_cfg = (config or {}).get("reset_button") or {}
    selector = reset_cfg.get("selector")
    confirm_selector = reset_cfg.get("confirm_selector")
    fallback = (reset_cfg.get("fallback") or "reload").strip().lower()

    if selector:
        try:
            root = _query_root(page, config)
            await root.locator(selector).first.click(timeout=5000)
            print(f"[DEBUG _reset_chat] Clicked reset button: {selector}")
            # Some chatbots open a confirmation dialog (e.g. vica's "Restart
            # chat?"). Click the confirm control to complete the reset.
            if confirm_selector:
                await page.wait_for_timeout(500)
                await root.locator(confirm_selector).first.click(timeout=5000)
                print(f"[DEBUG _reset_chat] Confirmed reset: {confirm_selector}")
            await page.wait_for_timeout(1000)
            return
        except Exception as e:
            print(f"[DEBUG _reset_chat] Reset click failed: {e}")
            if fallback != "reload":
                print("[DEBUG _reset_chat] No reload fallback; continuing without reset")
                return

    # Fallback: reload the page (default when no reset button is configured).
    try:
        await page.reload(wait_until="load", timeout=30000)
        await page.wait_for_timeout(2000)
        print("[DEBUG _reset_chat] Page reloaded")
    except Exception as e:
        print(f"[WARNING _reset_chat] reload failed: {e}")


async def _capture_response_baseline(page, config) -> dict:
    """
    Snapshot the response DOM BEFORE sending a new prompt.

    Returns {selector: {"count": int, "last_text": str}} per selector. Used
    by _wait_for_response to detect a *new* response (rather than reading
    the previous turn's reply that's still on the page).
    """
    selectors = _response_selectors(config)
    root = _query_root(page, config)
    shadow_aware = bool(config and config.get("is_shadow_dom"))
    js = _SHADOW_HELPER_JS + f"""
        (sel) => {{
            const els = {'querySelectorAllDeep(sel)' if shadow_aware else 'Array.from(document.querySelectorAll(sel))'};
            const last = els[els.length - 1];
            return {{
                count: els.length,
                last_text: last ? (last.innerText || last.textContent || '').trim() : ''
            }};
        }}
    """
    baseline = {}
    for sel in selectors:
        try:
            data = await root.evaluate(js, sel)
            baseline[sel] = data
        except Exception as e:
            print(f"[DEBUG _capture_baseline] {sel!r}: {e}")
            baseline[sel] = {"count": 0, "last_text": ""}
    print(f"[DEBUG _capture_baseline] {baseline}")
    return baseline


async def _wait_for_response(page, timeout: int = 30000, config=None, baseline=None) -> None:
    """
    Wait for a NEW AI response (different from the pre-send baseline).

    A new response is detected when, for any configured selector:
      - the matching element count has increased beyond baseline, OR
      - the last matching element's text differs from baseline AND is
        non-trivially long (>20 chars beyond baseline length).

    After change is detected, wait briefly for streaming text to stabilize
    (length unchanged across two consecutive polls).

    Args:
        page: Playwright page object
        timeout: Max wait time in milliseconds (default 30s)
        config: Optional chatbot config dict with 'response_selectors'
        baseline: Snapshot from _capture_response_baseline taken before
                  the prompt was sent. If None, falls back to legacy
                  "any response exists" behavior.
    """
    import time as _time
    start = _time.time()
    max_wait = timeout / 1000.0

    # Wait for input field to clear (message was sent) — up to 5s
    input_field = _chat_input(page, config)
    for _ in range(10):
        try:
            is_empty = await input_field.evaluate("el => (el.value ?? el.textContent ?? '') === ''")
            if is_empty:
                print("[DEBUG _wait_for_response] Input field cleared")
                break
        except Exception:
            pass
        await page.wait_for_timeout(500)

    selectors = _response_selectors(config)
    root = _query_root(page, config)
    shadow_aware = bool(config and config.get("is_shadow_dom"))
    print(f"[DEBUG _wait_for_response] Selectors: {selectors}")
    print(f"[DEBUG _wait_for_response] Baseline provided: {baseline is not None}")
    print(f"[DEBUG _wait_for_response] Shadow-DOM-aware: {shadow_aware}")

    probe_js = _SHADOW_HELPER_JS + f"""
        (sel) => {{
            const els = {'querySelectorAllDeep(sel)' if shadow_aware else 'Array.from(document.querySelectorAll(sel))'};
            const last = els[els.length - 1];
            return {{
                count: els.length,
                last_text: last ? (last.innerText || last.textContent || '').trim() : ''
            }};
        }}
    """

    async def _probe(sel):
        try:
            return await root.evaluate(probe_js, sel)
        except Exception:
            return {"count": 0, "last_text": ""}

    # Phase 1: wait until at least one selector shows NEW content vs baseline
    changed_sel = None
    while (_time.time() - start) < max_wait:
        for sel in selectors:
            cur = await _probe(sel)
            base = (baseline or {}).get(sel, {"count": 0, "last_text": ""})
            count_grew = cur["count"] > base["count"]
            text_changed = (
                cur["last_text"] != base["last_text"]
                and len(cur["last_text"]) > len(base["last_text"]) + 20
            )
            # Legacy fallback: no baseline, accept any non-trivial content
            legacy_ok = (
                baseline is None
                and cur["count"] > 0
                and len(cur["last_text"]) > 20
            )
            if count_grew or text_changed or legacy_ok:
                changed_sel = sel
                print(
                    f"[DEBUG _wait_for_response] New content on {sel!r}: "
                    f"count {base['count']}->{cur['count']}, "
                    f"text_len {len(base['last_text'])}->{len(cur['last_text'])}"
                )
                break
        if changed_sel:
            break
        await page.wait_for_timeout(500)

    if not changed_sel:
        print("[DEBUG _wait_for_response] Timed out waiting for new response")
        return

    # Phase 2: wait for streaming to stabilize on the changed selector.
    # Don't treat short placeholder text (e.g. "Thinking", "Loading…") as a
    # stable response — require at least _MIN_STABLE_LEN chars before
    # considering the stream settled.
    _MIN_STABLE_LEN = 50
    last_len = -1
    stable_polls = 0
    while (_time.time() - start) < max_wait:
        cur = await _probe(changed_sel)
        cur_len = len(cur["last_text"])
        if cur_len == last_len and cur_len >= _MIN_STABLE_LEN:
            stable_polls += 1
            if stable_polls >= 2:
                print(f"[DEBUG _wait_for_response] Stabilized at {cur_len} chars")
                break
        else:
            stable_polls = 0
        last_len = cur_len
        await page.wait_for_timeout(750)

    # Final settle
    try:
        await page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        await page.wait_for_timeout(500)


async def _get_latest_response(page, config=None) -> str:
    """
    Extract the latest assistant response from the chat panel.

    Args:
        page: Playwright page object
        config: Optional chatbot config dict with 'response_selectors'

    Returns:
        The text of the most recent assistant message, or empty string if not found
    """
    # Build selector list from config or use defaults
    if config and config.get("response_selectors"):
        selectors = [r["selector"] for r in config["response_selectors"]]
    else:
        # Default selectors for Claude Docs
        selectors = [
            '[data-testid="assistant-message"]',
            '[class*="Message"]',
            '.prose',
            '[class*="response"]'
        ]

    print(f"[DEBUG _get_latest_response] Using selectors: {selectors}")

    # For iframe-based inputs, query inside the iframe frame
    if config and config.get("is_iframe"):
        iframe_index = config.get("iframe_index", 0)
        print(f"[DEBUG _get_latest_response] Querying in iframe {iframe_index}")
        try:
            frames = page.frames
            iframe_frames = [f for f in frames if f != page.main_frame]
            if iframe_index < len(iframe_frames):
                frame = iframe_frames[iframe_index]

                # Extract text from iframe - get the most recent message
                text = await frame.evaluate(f"""() => {{
                    const sels = {json.dumps(selectors)};
                    console.log('Trying selectors in iframe:', sels);

                    // Try each selector
                    for (const s of sels) {{
                        try {{
                            const els = document.querySelectorAll(s);
                            console.log(`Selector "${{s}}" found ${{els.length}} elements`);

                            if (els.length > 0) {{
                                // For aria-live regions, get the text content directly
                                if (s.includes('aria-live')) {{
                                    // Get the last aria-live element that has actual content
                                    for (let i = els.length - 1; i >= 0; i--) {{
                                        const txt = els[i].textContent?.trim();
                                        // Skip very short text (likely just UI elements)
                                        // Skip common disclaimers
                                        if (txt && txt.length > 20 &&
                                            !txt.includes('cloud service') &&
                                            !txt.includes('Privacy Notice') &&
                                            !txt.includes('recorded using')) {{
                                            console.log(`Found aria-live text of length ${{txt.length}}`);
                                            return txt;
                                        }}
                                    }}
                                }}

                                // For [role="log"], look for the last meaningful entry
                                if (s.includes('role')) {{
                                    const lastEl = els[els.length - 1];
                                    const txt = lastEl.textContent?.trim();
                                    if (txt && txt.length > 50) {{
                                        console.log(`Found role=log text of length ${{txt.length}}`);
                                        return txt;
                                    }}
                                }}
                            }}
                        }} catch (e) {{
                            console.log(`Error with selector "${{s}}": ${{e.message}}`);
                        }}
                    }}

                    console.log('No response found in iframe');
                    return '';
                }}""")

                print(f"[DEBUG _get_latest_response] Extracted text length: {len(text) if text else 0}")
                if text:
                    print(f"[DEBUG _get_latest_response] First 200 chars: {text[:200]}")
                return text if text else ""
        except Exception as e:
            print(f"[DEBUG _get_latest_response] Error accessing iframe: {e}")

    # Main page extraction
    shadow_aware = bool(config and config.get("is_shadow_dom"))
    print(f"[DEBUG _get_latest_response] Shadow-DOM-aware: {shadow_aware}")
    query_expr = "querySelectorAllDeep(s)" if shadow_aware else "Array.from(document.querySelectorAll(s))"
    fallback_query = (
        "querySelectorAllDeep('aside,[class*=\"chat\"],[class*=\"panel\"],[class*=\"message\"]')"
        if shadow_aware
        else "Array.from(document.querySelectorAll('aside,[class*=\"chat\"],[class*=\"panel\"],[class*=\"message\"]'))"
    )
    text = await page.evaluate(_SHADOW_HELPER_JS + f"""() => {{
        const sels = {json.dumps(selectors)};
        console.log('Trying selectors:', sels);
        for (const s of sels) {{
            try {{
                const els = {query_expr};
                console.log(`Selector "${{s}}" found ${{els.length}} elements`);
                if (els.length > 0) {{
                    const lastEl = els[els.length - 1];
                    const txt = lastEl.innerText?.trim();
                    console.log(`Last element text: "${{txt?.substring(0, 100)}}..."`);
                    if (txt && txt.length > 0) {{
                        console.log(`Returning text of length ${{txt.length}}`);
                        return txt;
                    }}
                }}
            }} catch (e) {{
                console.log(`Error with selector "${{s}}": ${{e.message}}`);
            }}
        }}
        // Fallback: look for any visible text in chat containers
        const panels = {fallback_query};
        console.log(`Found ${{panels.length}} fallback chat containers`);
        for (const p of panels) {{
            const txt = p.innerText?.trim();
            if (txt && txt.length > 50) {{
                console.log(`Returning fallback text of length ${{txt.length}}`);
                return txt;
            }}
        }}
        console.log('No response found');
        return '';
    }}""")

    print(f"[DEBUG _get_latest_response] Extracted text length: {len(text) if text else 0}")
    if text:
        print(f"[DEBUG _get_latest_response] First 200 chars: {text[:200]}")

    return text if text else ""


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
def cdp_go():
    """Navigate the real browser to a URL."""
    async def cdp_go(url: str) -> str:
        """Navigate the real browser to a URL.

        Args:
            url: The full URL to navigate to.

        Returns:
            Accessibility tree of the loaded page.
        """
        async with _ToolLockContext():
            page = await _get_page()
            await page.goto(url, wait_until="load", timeout=30000)
            await page.wait_for_timeout(2000)
            return await _snapshot(page)
    return cdp_go


@tool
def cdp_click():
    """Click an element in the real browser by its accessible name."""
    async def cdp_click(selector: str) -> str:
        """Click an element in the real browser by its accessible name.

        Args:
            selector: Accessible name of the element to click,
                      e.g. 'Toggle assistant panel'.

        Returns:
            Accessibility tree after the click.
        """
        async with _ToolLockContext():
            page = await _get_page()
            await _safe_click(page, selector)
            await page.wait_for_timeout(1000)
            return await _snapshot(page)
    return cdp_click


async def send_prompt(text: str) -> str:
    """Send a prompt to the configured chatbot and return its full response.

    Module-level helper used by both the cdp_type_submit tool and deterministic
    solvers that need to bypass the LLM relay (which may refuse to forward
    adversarial prompts).
    """
    async with _ToolLockContext():
        config = None
        config_name = os.environ.get("CHATBOT_CONFIG", "claude-docs")
        print(f"[DEBUG send_prompt] CHATBOT_CONFIG env: {config_name}")
        try:
            from pathlib import Path
            import sys
            scripts_dir = Path(__file__).parent
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            from load_selectors import get_config

            config = get_config(config_name)
            if config:
                print(f"[DEBUG send_prompt] Config loaded for {config_name}")
                print(f"[DEBUG send_prompt] Chat input selector: {config.get('chat_input', {}).get('selector')}")
            else:
                print(f"[DEBUG send_prompt] Config returned None for {config_name}")
        except Exception as e:
            print(f"[DEBUG send_prompt] Exception loading config: {e}")
            import traceback
            traceback.print_exc()

        page = await _get_page()
        # Reset the chat BEFORE each prompt (no-op unless RESET_BETWEEN_PROMPTS
        # is set) so each lineage attempt is scored in isolation. Runs before
        # _ensure_assistant_panel_open so a reload re-discovers the panel/input.
        await _reset_chat(page, config)
        await _ensure_assistant_panel_open(page, config)

        field = _chat_input(page, config)
        try:
            await field.wait_for(state="visible", timeout=5000)
        except:
            print("[DEBUG] Field visibility check timed out, proceeding anyway")
            pass

        await asyncio.sleep(5)

        # Snapshot existing response state BEFORE sending so we can detect the
        # NEW response (rather than reading the previous turn's reply).
        baseline = await _capture_response_baseline(page, config)

        try:
            await field.click(timeout=10000)
            await field.fill(text)
            await field.press("Enter")
            print(f"[DEBUG] Message sent: {text[:50]}...")
        except Exception as e:
            print(f"[WARNING] Error sending message: {e}")
            raise

        await _wait_for_response(page, timeout=30000, config=config, baseline=baseline)
        response_text = await _get_latest_response(page, config=config)
        await asyncio.sleep(8)

        return response_text if response_text else "(no response found)"


@tool
def cdp_type_submit():
    """Type a question into the AI assistant input and submit it."""
    async def cdp_type_submit(text: str) -> str:
        """Type a question into the AI assistant input and submit it.

        Ensures the assistant panel is open, then fills the 'Ask a question...'
        input with the given text and presses Enter. Waits up to 30s for the
        AI response to appear.

        Args:
            text: The full story and question to send to the AI assistant.

        Returns:
            Full response text from the chatbot.
        """
        return await send_prompt(text)
    return cdp_type_submit


@tool
def cdp_scroll():
    """Scroll the page in the real browser."""
    async def cdp_scroll(direction: str) -> str:
        """Scroll the page in the real browser.

        Args:
            direction: 'down' to scroll down, 'up' to scroll up.

        Returns:
            Accessibility tree after scrolling.
        """
        async with _ToolLockContext():
            page = await _get_page()
            delta = 600 if direction == "down" else -600
            await page.evaluate(f"window.scrollBy(0, {delta})")
            await page.wait_for_timeout(500)
            return await _snapshot(page)
    return cdp_scroll


@tool
def cdp_send_and_wait():
    """Send a prompt and wait for the AI response, returning the full response text."""
    async def cdp_send_and_wait(text: str, config_name: str = None) -> str:
        """Send a prompt to the AI assistant and wait for the response.

        This is the main tool for chat-based interactions. It:
        1. Loads the chatbot config (if specified)
        2. Ensures the assistant panel is open
        3. Dismisses any consent/cookie banners
        4. Sends the prompt by typing and pressing Enter
        5. Waits for the response to appear in the DOM
        6. Extracts and returns the full response text

        Args:
            text: The prompt/question to send to the AI assistant.
            config_name: Optional config name from selectors.json
                        (e.g. 'claude-docs'). If None, uses defaults.

        Returns:
            The full text of the AI assistant's response.
        """
        # Load config if specified
        config = None
        if config_name:
            try:
                from load_selectors import get_config
                config = get_config(config_name)
            except Exception:
                pass

            if not config:
                return f"(config '{config_name}' not found)"

        async with _ToolLockContext():
            page = await _get_page()

            # Dismiss consent banner if present (blocks interactions)
            consent_selector = None
            if config and config.get("consent_banner") and config["consent_banner"].get("selector"):
                consent_selector = config["consent_banner"]["selector"]
            else:
                consent_selector = '[data-testid="consent-accept"]'

            try:
                consent_btn = page.locator(consent_selector)
                if await consent_btn.is_visible(timeout=2000):
                    await consent_btn.click(timeout=5000)
                    await page.wait_for_timeout(500)
            except Exception:
                pass

            # Ensure panel is open
            await _ensure_assistant_panel_open(page, config)

            # Send the prompt
            field = _chat_input(page, config)
            await field.wait_for(state="visible", timeout=10000)
            await field.click()

            # Snapshot existing response state BEFORE sending so we can detect
            # the NEW response (not the previous turn's still-visible reply).
            baseline = await _capture_response_baseline(page, config)

            await field.fill(text)
            await field.press("Enter")

            # Wait for the response to appear and stabilize
            await _wait_for_response(page, timeout=30000, config=config, baseline=baseline)

            # Extract and return the response text
            response_text = await _get_latest_response(page, config)
            return response_text if response_text else "(no response received)"
    return cdp_send_and_wait


def _load_config_from_env():
    """Load the chatbot config named by CHATBOT_CONFIG env var, or None."""
    config_name = os.environ.get("CHATBOT_CONFIG", "claude-docs")
    try:
        from pathlib import Path
        import sys
        scripts_dir = Path(__file__).parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from load_selectors import get_config
        return get_config(config_name)
    except Exception as e:
        print(f"[DEBUG _load_config_from_env] failed to load {config_name!r}: {e}")
        return None


@tool
def cdp_read_response():
    """Read the AI assistant's latest response text from the page."""
    async def cdp_read_response() -> str:
        """Read the AI assistant's latest response text from the page.

        Extracts the most recent assistant reply from the chat panel,
        using the chatbot config named by the CHATBOT_CONFIG env var so
        iframe-based and non-default chatbots can be read.

        Returns:
            The full text of the most recent response.
        """
        async with _ToolLockContext():
            page = await _get_page()
            await page.wait_for_timeout(500)
            config = _load_config_from_env()
            text = await _get_latest_response(page, config=config)
            return text if text else "(no response)"
    return cdp_read_response


def cdp_browser_tools() -> list:
    """All CDP browser tools as a list for use_tools()."""
    return [
        cdp_go(),
        cdp_click(),
        cdp_type_submit(),
        cdp_send_and_wait(),
        cdp_scroll(),
        cdp_read_response(),
    ]
