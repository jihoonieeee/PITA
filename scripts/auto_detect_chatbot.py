#!/usr/bin/env python3
"""
auto_detect_chatbot.py - Chatbot selector detection with multi-step fallback

Workflow:
1. Take screenshot
2. Extract input candidates (prioritized)
3. Test each with click + type (basic interaction)
4. Extract response selector candidates
5. Test combinations with Inspect
6. Fallback to iframe detection if needed
7. Save working selectors
"""

import argparse
import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path

# Fix encoding on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

from playwright.async_api import async_playwright

# Allow sibling scripts to be imported when running from the repo root
sys.path.insert(0, str(Path(__file__).parent))
from iframe_checker import check_iframes


async def get_current_page_url() -> str | None:
    """Return the URL of the first open page in the CDP browser."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts if hasattr(browser, "contexts") else []
            if contexts:
                pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
                if pages:
                    return pages[0].url
    except Exception:
        pass
    return None


async def take_screenshot(url=None):
    """Take a screenshot via CDP."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts if hasattr(browser, "contexts") else []
            if contexts:
                pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
                if pages:
                    page = pages[0]
                    if url:
                        await page.goto(
                            url, wait_until="domcontentloaded", timeout=60000
                        )
                    screenshot = await page.screenshot(path=None, full_page=False)
                    return screenshot
    except Exception as e:
        print(f"❌ Error taking screenshot: {e}")
    return None


_UNSTABLE_ID_PATTERNS = (
    "mantine-",
    "rc-",
    "radix-",
    "headlessui-",
    "floating-ui-",
)


def _escape_attr_value(s: str) -> str:
    """Escape a string for use as a quoted CSS attribute value."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _is_stable_id(id_str: str) -> bool:
    """Return False for framework-generated IDs that change on every page load."""
    if not id_str:
        return False
    lower = id_str.lower()
    if any(lower.startswith(p) for p in _UNSTABLE_ID_PATTERNS):
        return False
    # Reject IDs that are mostly hex/alphanumeric noise (uuid-style or hash-style)
    import re

    if re.fullmatch(
        r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", lower
    ):
        return False
    if re.fullmatch(r"[a-z]+-[a-z0-9]{6,}", lower):
        return False
    return True


async def extract_input_candidates(url=None):
    """Extract input field candidates from DOM, ranked by relevance."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts if hasattr(browser, "contexts") else []
            if contexts:
                pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
                if pages:
                    page = pages[0]
                    if url:
                        await page.goto(
                            url, wait_until="domcontentloaded", timeout=60000
                        )

                    result = await page.evaluate("""() => {
                        const inputs = [];

                        document.querySelectorAll('textarea, input[type="text"], input:not([type]), [contenteditable="true"], [role="textbox"]').forEach(el => {
                            if (el.offsetParent !== null) {
                                const tag = el.tagName.toLowerCase();
                                let priority = 0;
                                const height = el.offsetHeight;
                                const width = el.offsetWidth;

                                if (tag === 'textarea') priority += 100;
                                else if (tag === 'input') priority += 50;
                                else if (el.hasAttribute('contenteditable')) priority += 75;
                                else if (el.getAttribute('role') === 'textbox') priority += 60;

                                if (height > 80) priority += 20;
                                if (width > 300) priority += 10;

                                const id = el.id?.toLowerCase() || '';
                                if (id.includes('chat') || id.includes('message') || id.includes('input') || id.includes('ask') || id.includes('query')) priority += 15;
                                if (id.includes('search')) priority -= 20;

                                const placeholder = (el.placeholder || '').toLowerCase();
                                if (placeholder.includes('ask') || placeholder.includes('chat') || placeholder.includes('message') || placeholder.includes('question')) priority += 15;
                                if (placeholder.includes('search') || placeholder.includes('find')) priority -= 20;

                                // Collect stable data-* attributes, ranked by specificity.
                                // Prefer semantic identifiers (path, testid, id, cy, qa, name)
                                // over generic flags (autofocus, variant, size, disabled).
                                const SPECIFIC_DATA = ['data-testid', 'data-test', 'data-cy',
                                    'data-qa', 'data-id', 'data-path', 'data-name', 'data-key',
                                    'data-component', 'data-field', 'data-input'];
                                const GENERIC_DATA_PREFIXES = ['data-variant', 'data-size',
                                    'data-autofocus', 'data-disabled', 'data-invalid',
                                    'data-focused', 'data-hovered', 'data-active',
                                    'data-mantine', 'data-radix', 'data-state',
                                    'data-slot', 'data-scope', 'data-part'];
                                const specificAttrs = {};
                                const genericAttrs = {};
                                for (const attr of el.attributes) {
                                    if (!attr.name.startsWith('data-')) continue;
                                    if (!attr.value || attr.value.length >= 80) continue;
                                    if (SPECIFIC_DATA.includes(attr.name)) {
                                        specificAttrs[attr.name] = attr.value;
                                    } else if (!GENERIC_DATA_PREFIXES.some(p => attr.name.startsWith(p))) {
                                        genericAttrs[attr.name] = attr.value;
                                    }
                                }
                                // Specific attrs first, then other non-generic data-* attrs
                                const stableAttrs = Object.assign({}, specificAttrs, genericAttrs);

                                inputs.push({
                                    tag: tag,
                                    id: el.id || null,
                                    class: el.className?.substring(0, 150) || null,
                                    placeholder: el.placeholder || null,
                                    ariaLabel: el.getAttribute('aria-label') || null,
                                    name: el.getAttribute('name') || null,
                                    maxlength: el.getAttribute('maxlength') || null,
                                    stableAttrs: stableAttrs,
                                    selector_by_id: el.id ? `#${CSS.escape(el.id)}` : null,
                                    selector_by_class: el.className ? `.${CSS.escape(el.className.split(' ')[0])}` : null,
                                    selector_by_placeholder: el.placeholder ? `[placeholder=${JSON.stringify(el.placeholder)}]` : null,
                                    visible: true,
                                    height: height,
                                    width: width,
                                    priority: priority
                                });
                            }
                        });

                        inputs.sort((a, b) => b.priority - a.priority);
                        return inputs;
                    }""")

                    return result
    except Exception as e:
        print(f"❌ Error extracting inputs: {e}")
    return None


async def test_input_selector(selector):
    """Test if a selector works for basic interaction."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts if hasattr(browser, "contexts") else []
            if contexts:
                pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
                if pages:
                    page = pages[0]

                    element = await page.query_selector(selector)
                    if not element:
                        return {"works": False, "reason": "Selector not found"}

                    visible = await element.is_visible()
                    if not visible:
                        return {"works": False, "reason": "Element not visible"}

                    try:
                        await element.click(timeout=5000)
                        await element.type("Test", delay=30)
                        value = await element.input_value()
                        await element.evaluate("el => el.value = ''")

                        return {
                            "works": True,
                            "reason": "Selector works - click and type successful",
                            "value": value,
                        }
                    except Exception as e:
                        return {
                            "works": False,
                            "reason": f"Interaction failed: {str(e)[:100]}",
                        }

            raise RuntimeError("No browser pages available")
    except Exception as e:
        return {"works": False, "reason": f"Error: {str(e)[:100]}"}


async def extract_response_selectors(input_selector):
    """Send test message and extract response selector candidates."""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts if hasattr(browser, "contexts") else []
            if contexts:
                pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
                if pages:
                    page = pages[0]

                    async def send_prompt(text):
                        await page.evaluate("""([selector, msg]) => {
                            const input = document.querySelector(selector);
                            if (input) {
                                input.focus();
                                input.value = msg;
                                input.dispatchEvent(new Event('input', { bubbles: true }));
                                input.dispatchEvent(new Event('change', { bubbles: true }));

                                const buttons = Array.from(document.querySelectorAll('button'));
                                const sendBtn = buttons.find(b =>
                                    (b.textContent.toLowerCase().includes('send') ||
                                     b.getAttribute('aria-label')?.toLowerCase().includes('send'))
                                );

                                if (sendBtn) {
                                    sendBtn.disabled = false;
                                    sendBtn.click();
                                } else {
                                    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
                                }
                            }
                        }""", [input_selector, text])
                        await asyncio.sleep(2)

                    await send_prompt("Hi")
                    print("   ⏳ Waiting for response to first prompt...")
                    max_wait = 5
                    start = asyncio.get_event_loop().time()
                    response_appeared = False

                    while asyncio.get_event_loop().time() - start < max_wait:
                        response_check = await page.evaluate("""() => {
                            const patterns = ['[role="log"]', '[aria-live]', '[data-testid*="message"]', '[class*="message"]'];
                            for (const pattern of patterns) {
                                const els = document.querySelectorAll(pattern);
                                if (els.length > 0) {
                                    for (const el of els) {
                                        if (el.textContent.length > 50 && el.offsetParent !== null) {
                                            return true;
                                        }
                                    }
                                }
                            }
                            return false;
                        }""")
                        if response_check:
                            response_appeared = True
                            break
                        await asyncio.sleep(0.5)

                    if response_appeared:
                        print("   ✓ Response received")
                    else:
                        print("   ⚠ No response within 5s (proceeding anyway)")

                    await asyncio.sleep(1)
                    await send_prompt("Test")

                    responses = await page.evaluate("""() => {
                        const results = [];
                        const patterns = [
                            '[role="log"]',
                            '[aria-live]',
                            '[data-testid*="message"]',
                            '[data-testid*="response"]',
                            '[class*="message"]',
                            '[class*="response"]',
                            '[class*="bot"]',
                            '[class*="assistant"]'
                        ];

                        patterns.forEach(pattern => {
                            document.querySelectorAll(pattern).forEach(el => {
                                if (el.offsetParent !== null && el.textContent.length > 20) {
                                    if (!results.find(r => r.selector === pattern)) {
                                        results.push({
                                            selector: pattern,
                                            count: document.querySelectorAll(pattern).length,
                                            sample_text: el.textContent.substring(0, 50)
                                        });
                                    }
                                }
                            });
                        });

                        document.querySelectorAll('[id*="message"], [id*="response"], [id*="chat"]').forEach(el => {
                            if (el.offsetParent !== null && el.textContent.length > 20) {
                                const selector = el.id ? `#${CSS.escape(el.id)}` : null;
                                if (selector && !results.find(r => r.selector === selector)) {
                                    results.push({
                                        selector: selector,
                                        count: 1,
                                        sample_text: el.textContent.substring(0, 50)
                                    });
                                }
                            }
                        });

                        return results;
                    }""")

                    return responses

            raise RuntimeError("No browser pages available")
    except Exception as e:
        print(f"⚠ Error sending test message: {e}")
        return []


async def run_inspect_test(chatbot_key):
    """Run Inspect test to validate selectors."""
    env = os.environ.copy()
    env["CHATBOT_CONFIG"] = chatbot_key

    try:
        result = subprocess.run(
            [
                "inspect",
                "eval",
                "tasks/single_turn.py",
                "--model",
                "anthropic/claude-haiku-4-5",
                "--limit",
                "1",
                "-T",
                f"dataset_path={Path(__file__).parent.parent / 'dataset' / 'auto_detect_chatbot.json'}",
                "-T",
                "scorer_model=anthropic/claude-haiku-4-5",
            ],
            cwd=Path(__file__).parent.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=150,
        )
        return result.returncode == 0
    except:
        return False


async def detect_reset_button():
    """Find a 'new chat' / 'clear' / 'reset' button for per-prompt isolation.

    Scans visible buttons/links for reset-related keywords in their aria-label,
    title, or text, and builds a stable selector (prefers aria-label, then id,
    then title). Returns a list of candidates ranked by score (best first), or
    an empty list. When empty, the runtime falls back to page.reload().
    """
    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts if hasattr(browser, "contexts") else []
            if contexts:
                pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
                if pages:
                    page = pages[0]
                    result = await page.evaluate(r"""() => {
                        const KEYWORDS = ['new chat', 'new conversation', 'clear chat',
                            'clear conversation', 'start over', 'new thread', 'reset', 'clear'];
                        const out = [];
                        const els = document.querySelectorAll(
                            'button, [role="button"], a[role="button"], a[href]');
                        els.forEach(el => {
                            if (el.offsetParent === null) return;
                            const aria = (el.getAttribute('aria-label') || '').trim();
                            const title = (el.getAttribute('title') || '').trim();
                            const text = (el.innerText || el.textContent || '').trim();
                            const hay = (aria + ' ' + title + ' ' + text).toLowerCase();
                            let score = 0, matched = '';
                            for (const kw of KEYWORDS) {
                                if (hay.includes(kw)) {
                                    // longer/more specific keywords rank higher
                                    score = Math.max(score, 10 + kw.length);
                                    if (!matched) matched = kw;
                                }
                            }
                            // bare "+" icon buttons near a chat input are often "new chat"
                            if (!score && text === '+') { score = 3; matched = '+'; }
                            if (!score) return;

                            let selector = null;
                            if (aria) selector = `[aria-label="${aria.replace(/"/g, '\\"')}"]`;
                            else if (el.id) selector = `#${CSS.escape(el.id)}`;
                            else if (title) selector = `[title="${title.replace(/"/g, '\\"')}"]`;
                            if (!selector) return;

                            out.push({selector, matched, aria, title,
                                      text: text.slice(0, 40), score});
                        });
                        out.sort((a, b) => b.score - a.score);
                        return out;
                    }""")
                    return result or []
    except Exception as e:
        print(f"❌ Error detecting reset button: {e}")
    return []


async def main():
    parser = argparse.ArgumentParser(
        description="Systematic chatbot selector detection"
    )
    parser.add_argument("chatbot_name", help="Chatbot name")
    parser.add_argument("url", nargs="?", default=None, help="URL (optional)")
    args = parser.parse_args()

    chatbot_name = args.chatbot_name
    key = chatbot_name.lower().replace(" ", "-")
    url = args.url
    if not url:
        url = await get_current_page_url()

    print(f"🔍 Systematic selector detection for: {chatbot_name}\n")

    # Step 1: Screenshot
    print("📸 Taking screenshot...")
    screenshot = await take_screenshot(url)
    if not screenshot:
        print("❌ Failed to take screenshot")
        sys.exit(1)
    print("✓ Screenshot captured\n")

    # Step 2: Extract input candidates
    print("🔎 Analyzing input fields...")
    candidates = await extract_input_candidates(url)
    if not candidates:
        print("❌ No input fields found on main page")
        candidates = []

    if candidates:
        print(f"✓ Found {len(candidates)} input field(s):\n")
        for i, candidate in enumerate(candidates):
            print(f"  [{i+1}] {candidate['tag']}")
            if candidate["id"]:
                stable = _is_stable_id(candidate["id"])
                print(
                    f"      ID: {candidate['id']}{'' if stable else ' (unstable — will skip)'}"
                )
            if candidate["placeholder"]:
                print(f"      Placeholder: {candidate['placeholder']}")
            if candidate["ariaLabel"]:
                print(f"      Aria-label: {candidate['ariaLabel']}")
            if candidate.get("name"):
                print(f"      Name: {candidate['name']}")
            if candidate.get("maxlength"):
                print(f"      Char limit: {candidate['maxlength']}")
            if candidate.get("stableAttrs"):
                for attr, val in candidate["stableAttrs"].items():
                    print(f"      {attr}: {val}")
            print()

    # Step 3: Test input selectors — stable attributes first, ID only if stable
    if candidates:
        print("🧪 Testing input selectors...\n")
    working_input = None

    for candidate in candidates:
        if working_input:
            break

        # Build ordered list: stable data-* attrs → aria-label → name → placeholder → stable ID → class
        ordered = []
        for attr, val in (candidate.get("stableAttrs") or {}).items():
            ordered.append((f'[{attr}="{_escape_attr_value(val)}"]', f"data-attr:{attr}"))
        if candidate["ariaLabel"]:
            ordered.append((f'[aria-label="{_escape_attr_value(candidate["ariaLabel"])}"]', "aria-label"))
        if candidate.get("name"):
            ordered.append((f'[name="{_escape_attr_value(candidate["name"])}"]', "name"))
        if candidate["placeholder"]:
            ordered.append((candidate["selector_by_placeholder"], "placeholder"))
        if candidate["id"] and _is_stable_id(candidate["id"]):
            ordered.append((candidate["selector_by_id"], "stable-id"))
        # class and unstable ID as last resort
        if candidate["selector_by_class"]:
            ordered.append((candidate["selector_by_class"], "class"))
        if candidate["id"] and not _is_stable_id(candidate["id"]):
            ordered.append((candidate["selector_by_id"], "unstable-id"))

        for selector, selector_type in ordered:
            if not selector or working_input:
                continue
            test_result = await test_input_selector(selector)
            if test_result["works"]:
                working_input = selector
                print(f"✓ Selector works: {working_input}")
                if selector_type == "unstable-id":
                    print(
                        f"  ⚠ Using unstable ID — consider adding a stable data-* attribute"
                    )
                if candidate.get("maxlength"):
                    print(f"  Char limit detected: {candidate['maxlength']}")
                print(f"  {test_result['reason']}\n")
                break

    if not working_input:
        print("❌ No working input selector found on main page")
        print("\n📋 Checking for inputs in iframes...")
        iframe_result = await check_iframes(chatbot_name, url)
        if iframe_result:
            selectors_path = Path(__file__).parent.parent / "selectors.json"
            if selectors_path.exists():
                with open(selectors_path) as f:
                    data = json.load(f)
            else:
                data = {"chatbots": {}, "metadata": {}}
            config = {
                "url": url or "https://example.com",
                "name": chatbot_name,
                "chat_input": {
                    "selector": iframe_result["input_selector"],
                    "description": "Chat input (iframe)",
                },
                "response_selectors": [
                    {"selector": s, "description": "Response", "priority": i + 1}
                    for i, s in enumerate(iframe_result["response_selectors"])
                ],
                "toggle_button": {"selector": None, "description": "Toggle"},
                "consent_banner": {"selector": None, "description": "Consent"},
                "reset_button": {
                    "selector": None,
                    "fallback": "reload",
                    "description": "Reset",
                },
                "is_iframe": True,
                "iframe_index": iframe_result["iframe_index"],
                "is_shadow_dom": False,
                "detected_by": "iframe-escalation",
                "validated": False,
            }
            data["chatbots"][key] = config
            data["metadata"]["last_updated"] = (
                __import__("datetime").datetime.now().isoformat()
            )
            with open(selectors_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"\n✅ Saved iframe config as: {key}")
            print(f"   Input:    {iframe_result['input_selector']}")
            print(f"   Response: {iframe_result['response_selectors']}")
            sys.exit(0)
        else:
            print("❌ No inputs found in iframes either")
            print("   Try: python3 scripts/analyze_dom.py")
            sys.exit(1)

    # Step 4: Extract response selectors
    print("💬 Extracting response selector patterns...")
    response_candidates = await extract_response_selectors(working_input)

    if response_candidates:
        print(f"✓ Found {len(response_candidates)} response selector candidate(s):\n")
        for i, candidate in enumerate(response_candidates):
            print(f"  [{i+1}] {candidate['selector']}")
            print(f"      Count: {candidate['count']}")
            print(f"      Sample: {candidate['sample_text'][:50]}...\n")
    else:
        print("⚠ No response selectors found. Using fallback patterns.\n")
        response_candidates = [
            {"selector": "[role='log']"},
            {"selector": "[aria-live]"},
            {"selector": "[class*='message']"},
        ]

    # Step 4.5: Detect reset / new-chat button (for per-prompt isolation)
    print("🔄 Detecting reset / new-chat button...")
    reset_candidates = await detect_reset_button()
    reset_selector = reset_candidates[0]["selector"] if reset_candidates else None
    if reset_selector:
        print(
            f"✓ Reset button: {reset_selector} "
            f"(matched '{reset_candidates[0]['matched']}')\n"
        )
    else:
        print("⚠ No reset button found — runtime will fall back to page reload\n")

    # Step 5: Test with Inspect
    print("🧪 Testing with Inspect framework...\n")
    selectors_path = Path(__file__).parent.parent / "selectors.json"

    for candidate in response_candidates[:3]:
        response_sel = candidate["selector"]
        print(f"  Testing: {response_sel}...", end=" ", flush=True)

        if selectors_path.exists():
            with open(selectors_path) as f:
                data = json.load(f)
        else:
            data = {"chatbots": {}, "metadata": {}}

        # Find char_limit from the candidate that owns working_input
        char_limit = None
        for c in candidates:
            ordered_sels = []
            for attr, val in (c.get("stableAttrs") or {}).items():
                ordered_sels.append(f'[{attr}="{_escape_attr_value(val)}"]')
            if c["ariaLabel"]:
                ordered_sels.append(f'[aria-label="{_escape_attr_value(c["ariaLabel"])}"]')
            if c.get("name"):
                ordered_sels.append(f'[name="{_escape_attr_value(c["name"])}"]')
            if c["placeholder"]:
                ordered_sels.append(c["selector_by_placeholder"])
            if c["id"]:
                ordered_sels.append(c["selector_by_id"])
            if c["selector_by_class"]:
                ordered_sels.append(c["selector_by_class"])
            if working_input in ordered_sels and c.get("maxlength"):
                char_limit = int(c["maxlength"])
                break

        config = {
            "url": url or "https://example.com",
            "name": chatbot_name,
            "chat_input": {"selector": working_input, "description": "Chat input"},
            "response_selectors": [
                {"selector": response_sel, "description": "Response", "priority": 1}
            ],
            "toggle_button": {"selector": None, "description": "Toggle"},
            "consent_banner": {"selector": None, "description": "Consent"},
            "reset_button": {
                "selector": reset_selector,
                "fallback": "reload",
                "description": "New chat / reset button (falls back to page reload)",
            },
            "detected_by": "systematic",
            "validated": False,
        }
        if char_limit is not None:
            config["char_limit"] = char_limit

        data["chatbots"][key] = config
        data["metadata"]["last_updated"] = (
            __import__("datetime").datetime.now().isoformat()
        )
        with open(selectors_path, "w") as f:
            json.dump(data, f, indent=2)

        if await run_inspect_test(key):
            print("✅ WORKS!")
            print(f"\n✅ SUCCESS! Found working selectors:")
            print(f"   Input:    {working_input}")
            print(f"   Response: {response_sel}")
            print(f"\n✅ Saved as: {key}")
            print(
                f"   Usage: CHATBOT_CONFIG={key} inspect eval tasks/single_turn.py --model anthropic/claude-sonnet-5 --limit 10"
            )
            config["validated"] = True
            data["chatbots"][key] = config
            with open(selectors_path, "w") as f:
                json.dump(data, f, indent=2)
            sys.exit(0)
        else:
            print("❌")

    print("\n⚠ No working combination found with Inspect test")
    print(f"   Input selector: {working_input}")
    print(
        f"   Tried response selectors: {[c['selector'] for c in response_candidates[:3]]}"
    )
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
