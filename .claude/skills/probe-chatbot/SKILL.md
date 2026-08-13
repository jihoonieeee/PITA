---
name: probe-chatbot
description: Systematically detect and validate AI chatbot CSS selectors. User opens a site in Chrome with the chatbot visible; the skill runs the current scripts/ auto-detector, saves selectors.json, and validates with tasks/single_turn.py.
user-invocable: true
argument-hint: <chatbot-name>
allowed-tools:
  - Bash
  - Read
---

# /probe-chatbot - Systematic Chatbot Selector Detection

The user opens a website in Chrome showing the chatbot. This skill uses the current PITA workflow: screenshot current page, identify input candidates, test selectors, send a probe message, find response selectors, save `selectors.json`, then validate through Inspect.

## Before You Start

The user must:

1. Have Chrome running with CDP enabled.
2. Navigate to the website and make the chatbot input visible.
3. Invoke this skill with a chatbot name.

Repo launch helpers:

```bash
./launch_chrome_cdp.sh "https://example.com"
```

```powershell
.\launch_chrome_cdp.ps1 "https://example.com"
```

Manual Chrome launch examples:

```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-inspect
```

```powershell
chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\tmp\chrome-inspect
```

## Workflow

### Step 1 - Get Chatbot Name

Parse the skill argument for the chatbot name. If missing, ask: "What is the name of this chatbot? (e.g., 'AWS', 'Claude Docs', 'Vica')"

### Step 2 - Run The Auto-Detector

From the repo root, run:

```bash
source .venv/bin/activate
python scripts/auto_detect_chatbot.py "Chatbot Name"
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python scripts\auto_detect_chatbot.py "Chatbot Name"
```

The detector:

1. Takes a screenshot of the current CDP page.
2. Analyzes the DOM for visible chat input candidates.
3. Prefers stable selectors such as semantic `data-*`, `aria-label`, `name`, placeholder, and stable IDs.
4. Tests input interaction.
5. Sends probe messages.
6. Extracts response selector candidates.
7. Detects reset/new-chat controls when available.
8. Saves the chatbot config to `selectors.json`.
9. Validates with `tasks/single_turn.py` using `dataset/auto_detect_chatbot.json`.

### Step 3 - Interpret Results

Success output looks like:

```text
SUCCESS! Found working selectors:
   Input:    #chat-textarea
   Response: .bot-message

Saved as: aws-chatbot
Usage: CHATBOT_CONFIG=aws-chatbot inspect eval tasks/single_turn.py --model anthropic/claude-sonnet-5 --limit 10
```

If detection fails:

- `No input fields found`: input may be hidden, inside an iframe, or needs a click to reveal.
- `No working input selector`: selector exists but interaction failed; investigate shadow DOM or iframe behavior.
- `No response selectors found`: response timing or DOM structure may need manual inspection.
- `No working combination found`: candidate selectors did not validate through Inspect.

### Step 4 - Manual Investigation

Use these current helper scripts from the repo root:

```bash
python scripts/analyze_dom.py
```

```bash
python scripts/test_selectors.py "#input-id" ".response-class"
```

```bash
python scripts/screenshot_cdp.py
```

For a fresh non-CDP screenshot of a URL:

```bash
python scripts/screenshot.py "https://example.com"
```

Manual investigation should identify:

- Input selector.
- Response selector.
- Whether the input lives in an iframe or open shadow DOM.
- Optional reset/new-chat selector.
- Optional `maxlength` / `char_limit`.

### Step 5 - Full Validation

Once selectors are saved, validate through the current single-turn task:

```bash
CHATBOT_CONFIG="<key>" inspect eval tasks/single_turn.py --model anthropic/claude-sonnet-5 --limit 1 -T dataset_path=dataset/auto_detect_chatbot.json -T scorer_model=anthropic/claude-haiku-4-5
```

Selectors are valid if:

- Input selector is found and can be filled.
- Message sends successfully.
- Response text is extracted.
- The eval completes without selector or response timeout errors.

### Step 6 - Report Result

Tell the user:

```text
Input:  #chat-textarea
Response: .bot-message
Key: aws-chatbot
Reset: configured or reload fallback
Char limit: 500 or none
```

## Troubleshooting

| Issue | Root Cause | Solution |
| --- | --- | --- |
| `No browser contexts open` | Chrome is not running with CDP | Run `launch_chrome_cdp.sh` or `launch_chrome_cdp.ps1` |
| `No input fields found` | Input hidden, iframe, or not opened | Make chatbot input visible and rerun |
| `No working input selector` | Selector exists but cannot be interacted with | Use `scripts/analyze_dom.py`; check shadow DOM/iframe |
| `No response selectors found` | Response not appearing or nonstandard DOM | Send a manual message, then inspect response containers |
| Inspect test timeout | Slow or blocked chatbot | Confirm authentication/session state and retry with a small limit |

## Key Files

- `scripts/auto_detect_chatbot.py` - Main detector.
- `scripts/analyze_dom.py` - DOM inspection helper.
- `scripts/test_selectors.py` - Quick selector tester.
- `scripts/screenshot_cdp.py` - Screenshot/DOM capture from CDP Chrome.
- `scripts/cdp_browser_tools.py` - Runtime CDP browser interaction.
- `tasks/single_turn.py` - Current validation/evaluation task.
- `selectors.json` - Saved chatbot configurations.
