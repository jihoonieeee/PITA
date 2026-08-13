#!/usr/bin/env python3
"""
Take a screenshot and save DOM of a URL for Claude to analyze.
Saves: test/scripts/screenshot.png, test/scripts/dom.txt
Usage: python3 screenshot.py <url>
"""
import asyncio, sys, base64
from pathlib import Path
from playwright.async_api import async_playwright

OUT_DIR = Path(__file__).parent

async def main(url):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        try:
            print(f"Navigating to {url}...")
            await page.goto(url, wait_until="load", timeout=30000)
            await page.wait_for_timeout(2000)

            # Try to open any chat panel
            for label in ["assistant", "chat", "help"]:
                try:
                    btn = page.locator(f"button[aria-label*='{label}' i]").first
                    if await btn.is_visible(timeout=1000):
                        await btn.click()
                        await page.wait_for_timeout(1000)
                        print(f"✓ Clicked button with aria-label containing '{label}'")
                        break
                except:
                    pass

            await page.screenshot(path=str(OUT_DIR / "screenshot.png"), full_page=False)
            (OUT_DIR / "dom.txt").write_text(await page.content(), encoding="utf-8")
            print(f"✓ Screenshot saved to {OUT_DIR}/screenshot.png")
            print(f"✓ DOM saved to {OUT_DIR}/dom.txt")
        finally:
            await browser.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 screenshot.py <url>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
