#!/usr/bin/env python3
"""Analyze DOM structure for chatbot debugging."""

import asyncio
import io
import sys
from playwright.async_api import async_playwright

# Fix encoding on Windows
if sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        contexts = browser.contexts if hasattr(browser, "contexts") else []
        if contexts:
            pages = contexts[0].pages if hasattr(contexts[0], "pages") else []
            if pages:
                page = pages[0]

                title = await page.title()
                url = page.url
                print(f"Page: {title}")
                print(f"URL: {url}\n")

                analysis = await page.evaluate("""() => {
                    const results = {
                        "all_inputs": [],
                        "textareas": [],
                        "contenteditable": [],
                        "divs_with_input_keywords": [],
                        "all_buttons": []
                    };

                    // All textareas
                    document.querySelectorAll("textarea").forEach(el => {
                        results.textareas.push({
                            visible: el.offsetParent !== null,
                            id: el.id || null,
                            class: el.className?.substring(0, 100) || null,
                            placeholder: el.placeholder || null
                        });
                    });

                    // All inputs
                    document.querySelectorAll("input").forEach(el => {
                        results.all_inputs.push({
                            type: el.type,
                            visible: el.offsetParent !== null,
                            id: el.id || null,
                            placeholder: el.placeholder || null,
                            class: el.className?.substring(0, 100) || null
                        });
                    });

                    // Contenteditable
                    document.querySelectorAll("[contenteditable='true']").forEach(el => {
                        results.contenteditable.push({
                            tag: el.tagName,
                            id: el.id || null,
                            class: el.className?.substring(0, 100) || null
                        });
                    });

                    // Divs with input-related keywords
                    document.querySelectorAll("div").forEach(el => {
                        const text = el.textContent.toLowerCase();
                        if ((text.includes("message") || text.includes("input") || text.includes("type")) && el.offsetParent !== null && el.offsetHeight > 50) {
                            if (!results.divs_with_input_keywords.find(d => d.id === el.id)) {
                                results.divs_with_input_keywords.push({
                                    id: el.id || "no-id",
                                    class: el.className?.substring(0, 80) || "no-class",
                                    height: el.offsetHeight,
                                    textLen: el.textContent.length
                                });
                            }
                        }
                    });

                    // All visible buttons
                    document.querySelectorAll("button").forEach(el => {
                        if (el.offsetParent !== null) {
                            results.all_buttons.push({
                                text: el.textContent?.substring(0, 50) || null,
                                id: el.id || null,
                                class: el.className?.substring(0, 80) || null,
                                ariaLabel: el.getAttribute("aria-label")?.substring(0, 50) || null
                            });
                        }
                    });

                    return results;
                }""")

                print("=== TEXTAREAS ===")
                print(f"Found: {len(analysis['textareas'])}")
                for ta in analysis['textareas']:
                    print(f"  {ta}")

                print("\n=== INPUTS ===")
                print(f"Found: {len(analysis['all_inputs'])}")
                for inp in analysis['all_inputs'][:10]:
                    print(f"  {inp}")

                print("\n=== CONTENTEDITABLE ===")
                print(f"Found: {len(analysis['contenteditable'])}")
                for ce in analysis['contenteditable']:
                    print(f"  {ce}")

                print("\n=== DIVS WITH INPUT KEYWORDS ===")
                print(f"Found: {len(analysis['divs_with_input_keywords'])}")
                for div in analysis['divs_with_input_keywords'][:10]:
                    print(f"  {div}")

                print("\n=== BUTTONS (first 20) ===")
                print(f"Found: {len(analysis['all_buttons'])}")
                for btn in analysis['all_buttons'][:20]:
                    print(f"  {btn}")


asyncio.run(main())
