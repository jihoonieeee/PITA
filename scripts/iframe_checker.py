#!/usr/bin/env python3
"""
iframe_checker.py - Detect and handle cross-origin iframes

Workflow:
1. Detect all iframes on page
2. Try clicking visible chat buttons on main page
3. For each iframe, attempt auto-detect inside it
4. If iframe is truly cross-origin protected, escalate to raw CDP
"""

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def detect_iframes(page):
    """Detect all iframes on the page."""
    print("🔍 Detecting iframes...")

    iframes_info = await page.evaluate("""() => {
        const iframes = document.querySelectorAll('iframe');
        return Array.from(iframes).map((iframe, i) => ({
            index: i,
            id: iframe.id || 'no-id',
            name: iframe.name || 'no-name',
            src: iframe.src || 'no-src',
            title: iframe.title || 'no-title',
            visible: iframe.offsetParent !== null,
            class: iframe.className?.substring(0, 100) || 'no-class'
        }));
    }""")

    print(f"   Found {len(iframes_info)} iframes:")
    for info in iframes_info:
        visible = "visible" if info['visible'] else "hidden"
        print(f"     [{info['index']}] {info['name']} ({visible}) - {info['src'][:50]}")

    return iframes_info


async def try_click_chat_button(page):
    """Try clicking visible chat buttons on the main page."""
    print("\n💬 Trying to click visible chat buttons...")

    try:
        # Look for common chat button selectors
        chat_selectors = [
            'button[aria-label*="chat" i]',
            'button[aria-label*="message" i]',
            'button[title*="chat" i]',
            '[data-testid*="chat"]',
            '.chat-button',
            '.messaging-button',
            '[class*="chat-widget"]',
            'iframe[title*="chat" i]',
            'iframe[name*="chat" i]',
        ]

        for selector in chat_selectors:
            try:
                element = page.locator(selector).first
                count = await element.count()

                if count > 0:
                    print(f"   Found: {selector}")
                    try:
                        await element.click(timeout=2000)
                        print(f"   ✓ Clicked successfully!")
                        await asyncio.sleep(2)  # Wait for widget to open
                        return True
                    except Exception as e:
                        print(f"   ✗ Click failed: {str(e)[:50]}")
            except:
                pass

        print("   ⚠ No clickable chat buttons found on main page")
        return False

    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False


async def check_if_cross_origin_protected(page, iframe_info):
    """Check if iframe is cross-origin protected."""
    print(f"\n   Checking iframe access level...")

    try:
        # Try to evaluate in the iframe to see if it's accessible
        frames = page.frames

        for frame in frames:
            try:
                # Try a simple evaluation
                result = await frame.evaluate("() => 'accessible'")
                if result == 'accessible':
                    return False  # Same-origin, accessible
            except Exception as e:
                error = str(e)
                if 'cross-origin' in error.lower() or 'permission' in error.lower():
                    return True  # Cross-origin protected

        return False  # Assume accessible if no error

    except Exception as e:
        print(f"     Error checking: {e}")
        return True  # Assume protected on error


async def extract_inputs_in_iframe(page, frame):
    """Extract input candidates from within an iframe via frame.evaluate."""
    try:
        result = await frame.evaluate("""() => {
            const inputs = [];
            document.querySelectorAll('textarea, input[type="text"], input:not([type]), [contenteditable="true"], [role="textbox"]').forEach(el => {
                if (el.offsetParent !== null) {
                    inputs.push({
                        tag: el.tagName,
                        id: el.id || null,
                        placeholder: el.placeholder || null,
                        ariaLabel: el.getAttribute('aria-label') || null,
                        selector_by_id: el.id ? `#${CSS.escape(el.id)}` : null,
                        selector_by_placeholder: el.placeholder ? `[placeholder=${JSON.stringify(el.placeholder)}]` : null,
                        visible: true
                    });
                }
            });
            return inputs;
        }""")
        return result if result else []
    except Exception as e:
        return []


async def test_input_in_iframe(page, frame, selector):
    """Test if an input selector works in iframe context via evaluate."""
    try:
        result = await frame.evaluate("""(selector) => {
            const el = document.querySelector(selector);
            if (!el) return { works: false, reason: 'Not found' };
            if (el.offsetParent === null) return { works: false, reason: 'Not visible' };

            try {
                el.focus();
                el.click();
                el.value = 'Test';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                return { works: true, reason: 'Selector works via JS evaluation' };
            } catch(e) {
                return { works: false, reason: e.message };
            }
        }""", selector)
        return result if result else {'works': False, 'reason': 'Evaluate failed'}
    except Exception as e:
        return {'works': False, 'reason': str(e)}


async def extract_responses_in_iframe(page, frame, input_selector):
    """Send test message in iframe and extract response patterns via evaluate."""
    try:
        # Send message via JavaScript
        await frame.evaluate("""(selector) => {
            const input = document.querySelector(selector);
            if (!input) return false;

            input.focus();
            input.value = 'Hi';
            input.dispatchEvent(new Event('input', { bubbles: true }));

            const buttons = Array.from(document.querySelectorAll('button'));
            const sendBtn = buttons.find(b =>
                (b.textContent.toLowerCase().includes('send') ||
                 b.getAttribute('aria-label')?.toLowerCase().includes('send'))
            );
            if (sendBtn) sendBtn.click();
            else input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
            return true;
        }""", input_selector)

        await asyncio.sleep(2)

        # Extract responses
        result = await frame.evaluate("""() => {
            const patterns = [
                '[role="log"]',
                '[aria-live]',
                '[class*="message"]',
                '[class*="response"]'
            ];
            const results = [];
            patterns.forEach(pattern => {
                document.querySelectorAll(pattern).forEach(el => {
                    if (el.offsetParent !== null && el.textContent.length > 20) {
                        if (!results.find(r => r.selector === pattern)) {
                            results.push({
                                selector: pattern,
                                count: document.querySelectorAll(pattern).length,
                                sample: el.textContent.substring(0, 50)
                            });
                        }
                    }
                });
            });
            return results;
        }""")
        return result if result else []
    except Exception as e:
        return []


async def escalate_to_cdp(chatbot_name, url=None):
    """Escalate to iframe-based auto-detection using Playwright frames."""
    print("\n🚀 Escalating to iframe-specific auto-detection...")
    print("   Running auto-detect logic inside iframe contexts...\n")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp("http://localhost:9222")
            contexts = browser.contexts if hasattr(browser, "contexts") else []
            if not contexts:
                print("   ✗ No browser contexts")
                return None

            pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
            if not pages:
                print("   ✗ No pages")
                return None

            page = pages[0]
            print(f"   ✓ Connected to main page")

            # Get all frames
            frames = page.frames
            iframe_frames = [f for f in frames if f != page.main_frame]

            if not iframe_frames:
                print(f"   ⚠ No iframe frames detected")
                return None

            print(f"   Found {len(iframe_frames)} iframe frame(s)\n")

            # Try auto-detect in each iframe
            for i, frame in enumerate(iframe_frames[:3]):  # Check first 3 iframes
                frame_url = frame.url if hasattr(frame, 'url') else 'N/A'
                print(f"   📋 Testing iframe {i+1}/{len(iframe_frames[:3])}")
                print(f"      URL: {frame_url[:60]}")

                try:
                    # Extract inputs in this iframe
                    inputs = await extract_inputs_in_iframe(page, frame)
                    if not inputs:
                        print(f"      ⚠ No inputs found")
                        continue

                    print(f"      ✓ Found {len(inputs)} input(s)")

                    # Test inputs
                    working_input = None
                    for input_info in inputs:
                        # Build selector
                        if input_info.get('selector_by_id'):
                            selector = input_info['selector_by_id']
                        elif input_info.get('selector_by_placeholder'):
                            selector = input_info['selector_by_placeholder']
                        else:
                            selector = input_info['tag'].lower()

                        test_result = await test_input_in_iframe(page, frame, selector)
                        if test_result.get('works'):
                            working_input = selector
                            print(f"      ✓ Input works: {working_input}")
                            break

                    if not working_input:
                        continue

                    # Extract responses
                    responses = await extract_responses_in_iframe(page, frame, working_input)
                    if responses:
                        print(f"      ✓ Found {len(responses)} response pattern(s)")
                        print(f"\n   ✅ SUCCESS in iframe {i+1}!")

                        return {
                            'method': 'iframe-escalation',
                            'chatbot': chatbot_name,
                            'iframe_index': i,
                            'input_selector': working_input,
                            'response_selectors': [r['selector'] for r in responses[:3]],
                            'status': 'found_in_iframe'
                        }

                except Exception as e:
                    print(f"      ✗ Error: {str(e)[:60]}")
                    continue

            print(f"\n   ⚠ Could not find working inputs in any iframe")
            return None

    except Exception as e:
        print(f"   ✗ Escalation failed: {e}")
        return None


async def check_iframes(chatbot_name, url=None):
    """Main iframe detection and handling."""
    print("\n" + "=" * 60)
    print("🔎 IFRAME DETECTION PHASE")
    print("=" * 60)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        contexts = browser.contexts if hasattr(browser, "contexts") else []

        if not contexts:
            print("❌ No browser contexts")
            return None

        pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
        if not pages:
            print("❌ No pages")
            return None

        page = pages[0]

        # Step 1: Detect iframes
        iframes_info = await detect_iframes(page)

        if not iframes_info:
            print("   ℹ No iframes detected on page")
            return None

        # Step 2: Try clicking chat buttons
        clicked = await try_click_chat_button(page)

        # Step 3: Check if iframes are accessible and collect results
        print("\n📋 Analyzing iframe accessibility...")
        protected_check = []
        for info in iframes_info:
            is_protected = await check_if_cross_origin_protected(page, info)
            protected_check.append(is_protected)
            status = "🔒 Cross-origin (protected)" if is_protected else "✅ Accessible"
            print(f"   [{info['index']}] {info['name']:20} {status}")

        # Step 4: If any are protected, escalate to escalate_to_cdp
        has_protected = any(protected_check)

        # Always try escalation if we have iframes (might find inputs even in accessible ones)
        print("\n🚀 Running auto-detect inside iframe contexts...")
        result = await escalate_to_cdp(chatbot_name, url)
        return result


if __name__ == "__main__":
    import sys

    chatbot_name = sys.argv[1] if len(sys.argv) > 1 else "Test"
    result = asyncio.run(check_iframes(chatbot_name))

    if result:
        print("\n" + "=" * 60)
        print(f"✅ Result: {result}")
    else:
        print("\n" + "=" * 60)
        print("❌ No iframes or iframe detection inconclusive")
