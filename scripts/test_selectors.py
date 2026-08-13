#!/usr/bin/env python3
"""Quick selector testing without Inspect."""

import argparse
import asyncio
import io
import sys
from playwright.async_api import async_playwright

# Fix encoding on Windows
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


async def main():
    parser = argparse.ArgumentParser(description="Test selectors")
    parser.add_argument("input_selector", help="Input selector")
    parser.add_argument("response_selector", nargs="?", default=None, help="Response selector (optional)")
    args = parser.parse_args()

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        contexts = browser.contexts if hasattr(browser, "contexts") else []
        if contexts:
            pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
            if pages:
                page = pages[0]

                # Test input selector
                print(f"Testing input selector: {args.input_selector}")
                input_el = await page.query_selector(args.input_selector)
                if not input_el:
                    print("  ❌ Not found")
                    return

                visible = await input_el.is_visible()
                print(f"  ✓ Found, visible: {visible}")

                # Try interact
                try:
                    await input_el.click(timeout=5000)
                    await input_el.type("Test", delay=30)
                    print(f"  ✓ Click and type successful")
                except Exception as e:
                    print(f"  ❌ Interaction failed: {e}")
                    return

                # Test response selector if provided
                if args.response_selector:
                    print(f"\nTesting response selector: {args.response_selector}")
                    response_els = await page.query_selector_all(args.response_selector)
                    print(f"  ✓ Found {len(response_els)} element(s)")
                    if response_els:
                        text = await response_els[0].text_content()
                        print(f"    First element text: {text[:100]}")


asyncio.run(main())
