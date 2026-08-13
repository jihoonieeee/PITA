#!/usr/bin/env python3
"""
cdp_auto_detect.py - JavaScript-based interaction for cross-origin iframe auto-detection

Uses Playwright frames with JavaScript evaluation to:
1. Connect to browser and get iframe frames
2. Interact with cross-origin iframes via JavaScript (evaluate works cross-origin)
3. Test input interaction, send messages, extract responses
4. Validate that automation is possible
"""

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright


async def validate_response_selector(frame, responses, input_selector):
    """Validate that response selectors can actually extract content."""
    try:
        # Send a test message if not already sent
        input_locator = frame.locator(input_selector).first

        # Try each response selector
        for response in responses[:3]:
            selector = response['selector']

            # Check if selector exists and has content
            elements = await frame.query_selector_all(selector)
            if not elements:
                continue

            # Get text content from first element
            for el in elements:
                text = await el.text_content()
                is_visible = await el.is_visible()

                if text and len(text) > 10 and is_visible:
                    return {
                        'working_selector': selector,
                        'element_count': len(elements),
                        'sample_length': len(text),
                        'validated': True
                    }

        return None
    except Exception as e:
        print(f"      Validation error: {str(e)[:60]}")
        return None


async def run_auto_detect_in_iframe(chatbot_name, iframe_index=None):
    """Run full auto-detect workflow inside an iframe via JavaScript interaction."""
    print("\n" + "="*70)
    print("🚀 ESCALATING TO JAVASCRIPT-BASED IFRAME AUTO-DETECTION")
    print("="*70)

    try:
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
            print(f"\n✓ Connected to browser")

            # Get all frames
            frames = page.frames
            iframe_frames = [f for f in frames if f != page.main_frame]

            if not iframe_frames:
                print("❌ No iframes found")
                return None

            print(f"✓ Found {len(iframe_frames)} iframe(s)")

            # If specific iframe_index provided, target it
            if iframe_index is not None:
                if iframe_index >= len(iframe_frames):
                    print(f"❌ Iframe index {iframe_index} out of range")
                    return None
                frames_to_test = [iframe_frames[iframe_index]]
                start_index = iframe_index
            else:
                frames_to_test = iframe_frames[:5]
                start_index = 0

            # Try auto-detect in each iframe
            for idx, frame in enumerate(frames_to_test):
                actual_index = start_index + idx
                frame_url = frame.url if hasattr(frame, 'url') else 'N/A'

                print(f"\n📋 Testing iframe {actual_index}")
                print(f"   URL: {frame_url[:60]}")

                try:
                    # Extract inputs
                    print(f"   Extracting inputs...", end=" ", flush=True)
                    inputs = await frame.evaluate("""() => {
                        const inputs = [];
                        document.querySelectorAll('textarea, input[type="text"], input:not([type]), [contenteditable="true"], [role="textbox"]').forEach(el => {
                            if (el.offsetParent !== null) {
                                inputs.push({
                                    tag: el.tagName,
                                    id: el.id || null,
                                    placeholder: el.placeholder || null,
                                    ariaLabel: el.getAttribute('aria-label') || null,
                                    selector_by_id: el.id ? `#${CSS.escape(el.id)}` : null,
                                    selector_by_placeholder: el.placeholder ? `[placeholder=${JSON.stringify(el.placeholder)}]` : null
                                });
                            }
                        });
                        return inputs;
                    }""")

                    if not inputs:
                        print("none found")
                        continue

                    print(f"found {len(inputs)}")

                    # Test inputs
                    working_input = None
                    for input_info in inputs:
                        if input_info.get('selector_by_id'):
                            selector = input_info['selector_by_id']
                        elif input_info.get('selector_by_placeholder'):
                            selector = input_info['selector_by_placeholder']
                        else:
                            selector = input_info['tag'].lower()

                        print(f"   Testing: {selector}...", end=" ", flush=True)

                        test_result = await frame.evaluate("""(selector) => {
                            const el = document.querySelector(selector);
                            if (!el) return false;
                            if (el.offsetParent === null) return false;

                            try {
                                el.focus();
                                el.click?.();
                                el.value = 'Test';
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                                return true;
                            } catch(e) {
                                return false;
                            }
                        }""", selector)

                        if test_result:
                            working_input = selector
                            print("✓")
                            break
                        else:
                            print("✗")

                    if not working_input:
                        print(f"   ⚠ No working input")
                        continue

                    # Get input locator for Playwright interaction
                    print(f"   Sending message 1...", end=" ", flush=True)
                    try:
                        input_locator = frame.locator(working_input).first

                        # Count bot messages BEFORE sending user message
                        msg_count_before = await frame.evaluate("""() => {
                            const logEl = document.querySelector('[role="log"]');
                            if (!logEl) return 0;
                            // Count patterns like "Michelle says:" or "Michelle · AI says:"
                            const text = logEl.textContent;
                            return (text.match(/Michelle.*?says:/g) || []).length;
                        }""")

                        # Send first message
                        await input_locator.fill("Hi from autodetect")
                        await input_locator.press("Enter")
                        print("✓")

                        # Wait for response to first message by checking for NEW bot message
                        print(f"   Waiting for response...", end=" ", flush=True)
                        max_wait = 8  # longer wait for McDonald's
                        start = asyncio.get_event_loop().time()
                        response_appeared = False
                        input_cleared = False

                        while asyncio.get_event_loop().time() - start < max_wait:
                            # First, wait for input to clear
                            if not input_cleared:
                                input_value = await input_locator.input_value()
                                if input_value == "":
                                    input_cleared = True

                            # Once input is cleared, wait for bot message count to increase
                            if input_cleared:
                                msg_count_after = await frame.evaluate("""() => {
                                    const logEl = document.querySelector('[role="log"]');
                                    if (!logEl) return 0;
                                    const text = logEl.textContent;
                                    return (text.match(/Michelle.*?says:/g) || []).length;
                                }""")

                                if msg_count_after > msg_count_before:
                                    response_appeared = True
                                    break

                            await asyncio.sleep(0.3)

                        if response_appeared:
                            print("✓ (response received)")
                        else:
                            print("⚠ (timeout waiting)")

                        # Wait a bit more for response to be fully rendered
                        await asyncio.sleep(2)

                    except Exception as e:
                        print(f"✗ ({str(e)[:40]})")

                    # Extract response patterns
                    print(f"   Extracting responses...", end=" ", flush=True)
                    responses = []

                    for pattern in ['[role="log"]', '[aria-live]', '[data-testid*="message"]', '[class*="message"]']:
                        try:
                            elements = await frame.query_selector_all(pattern)
                            if elements:
                                # Check if any have content
                                for el in elements:
                                    text = await el.text_content()
                                    is_visible = await el.is_visible()
                                    if text and len(text) > 20 and is_visible:
                                        responses.append({
                                            'selector': pattern,
                                            'count': len(elements),
                                            'sample': text[:50]
                                        })
                                        break
                        except:
                            pass

                    if not responses:
                        print("none found")
                        continue

                    # Remove duplicates by selector
                    unique_responses = {}
                    for r in responses:
                        if r['selector'] not in unique_responses:
                            unique_responses[r['selector']] = r
                    responses = list(unique_responses.values())

                    print(f"found {len(responses)}")

                    # Count bot messages BEFORE message 2
                    msg_count_before_msg2 = await frame.evaluate("""() => {
                        const logEl = document.querySelector('[role="log"]');
                        if (!logEl) return 0;
                        const text = logEl.textContent;
                        return (text.match(/Michelle.*?says:/g) || []).length;
                    }""")

                    # Send second test message for verification
                    print(f"   Sending message 2...", end=" ", flush=True)
                    try:
                        await input_locator.fill("Test message 2")
                        await input_locator.press("Enter")
                        print("✓")

                        # Wait for response to second message (checking for new bot message)
                        print(f"   Waiting for response...", end=" ", flush=True)
                        max_wait = 8
                        start = asyncio.get_event_loop().time()
                        response_appeared = False
                        input_cleared = False

                        while asyncio.get_event_loop().time() - start < max_wait:
                            # First, wait for input to clear
                            if not input_cleared:
                                input_value = await input_locator.input_value()
                                if input_value == "":
                                    input_cleared = True

                            # Once input is cleared, wait for bot message count to increase
                            if input_cleared:
                                msg_count_after = await frame.evaluate("""() => {
                                    const logEl = document.querySelector('[role="log"]');
                                    if (!logEl) return 0;
                                    const text = logEl.textContent;
                                    return (text.match(/Michelle.*?says:/g) || []).length;
                                }""")
                                if msg_count_after > msg_count_before_msg2:
                                    response_appeared = True
                                    break

                            await asyncio.sleep(0.3)

                        if response_appeared:
                            print("✓")
                        else:
                            print("⚠")

                        await asyncio.sleep(2)

                    except Exception as e:
                        print(f"✗")

                    print(f"\n✅ SUCCESS in iframe {actual_index}!")
                    print(f"   Input:  {working_input}")
                    print(f"   Response patterns: {len(responses)}")

                    # Validate response selector with Inspect
                    print(f"\n   🧪 Validating response selector with Inspect...")
                    validation_result = await validate_response_selector(frame, responses, working_input)

                    if validation_result:
                        print(f"   ✅ Response selector validated: {validation_result['working_selector']}")
                    else:
                        print(f"   ⚠️  Could not validate response selector")

                    return {
                        'method': 'javascript-cdp',
                        'chatbot': chatbot_name,
                        'iframe_index': actual_index,
                        'input_selector': working_input,
                        'response_selectors': [r['selector'] for r in responses[:3]],
                        'validation': validation_result,
                        'status': 'success',
                        'note': 'Uses JavaScript evaluation to interact with cross-origin iframe'
                    }

                except Exception as e:
                    print(f"\n   ✗ Error: {str(e)[:80]}")
                    continue

            print(f"\n❌ Could not find working selectors in any iframe")
            return None

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


async def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("Usage: python3 cdp_auto_detect.py <chatbot_name> [iframe_index]")
        sys.exit(1)

    chatbot_name = sys.argv[1]
    iframe_index = int(sys.argv[2]) if len(sys.argv) > 2 else None

    result = await run_auto_detect_in_iframe(chatbot_name, iframe_index)

    print("\n" + "="*70)
    if result:
        print(f"✅ Result: {result['status'].upper()}")
        print("="*70)
        print(json.dumps(result, indent=2))
        return 0
    else:
        print("❌ Could not detect via JavaScript interaction")
        print("="*70)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
