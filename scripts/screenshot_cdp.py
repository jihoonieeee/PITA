#!/usr/bin/env python3
"""
Take screenshot from already-open Chrome via CDP.
Used after user manually navigates to the chatbot state they want to analyze.

Usage: python3 helper/screenshot_cdp.py
Saves: helper/screenshot.png, helper/dom.txt
"""
import asyncio, sys, base64, os
from pathlib import Path
from playwright.async_api import async_playwright

OUT_DIR = Path(__file__).parent
CDP_URL = os.environ.get("CDP_URL", "http://localhost:9222")

async def main():
    try:
        async with async_playwright() as p:
            print(f"Connecting to Chrome at {CDP_URL}...")
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            contexts = browser.contexts
            if not contexts:
                print("❌ No browser contexts open. Make sure Chrome is running with CDP enabled.")
                sys.exit(1)

            pages = contexts[0].pages
            if not pages:
                print("❌ No pages open in the context.")
                sys.exit(1)

            page = pages[0]
            print(f"✓ Connected to page: {page.url}")

            # Take screenshot and save DOM
            await page.screenshot(path=str(OUT_DIR / "screenshot.png"), full_page=False)
            (OUT_DIR / "dom.txt").write_text(await page.content(), encoding="utf-8")

            print(f"✓ Screenshot saved to {OUT_DIR}/screenshot.png")
            print(f"✓ DOM saved to {OUT_DIR}/dom.txt")
            print(f"\nNext: Claude will analyze this screenshot to find chatbot selectors.")

    except Exception as e:
        print(f"❌ Error: {e}")
        print("Make sure Chrome is running with:")
        print("  google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-inspect")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
