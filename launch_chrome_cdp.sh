#!/bin/bash
#
# launch_chrome_cdp.sh
#
# Launches Google Chrome with Chrome DevTools Protocol (CDP) for Inspect AI testing.
# Automatically navigates to a target URL and keeps the browser open for testing.
#
# Usage:
#   ./launch_chrome_cdp.sh [URL] [CDP_PORT]
#
# Examples:
#   ./launch_chrome_cdp.sh  # Uses defaults
#   ./launch_chrome_cdp.sh "https://example.com"
#   ./launch_chrome_cdp.sh "https://code.claude.com/docs/en/overview" 9222
#
# Environment:
#   TARGET_URL      - Override default URL (env var)
#   CDP_PORT        - Override default CDP port (env var, default: 9222)
#   CHROME_PROFILE  - Custom Chrome profile dir (default: /tmp/chrome-inspect)

set -euo pipefail

# Defaults
DEFAULT_URL="https://code.claude.com/docs/en/overview"
DEFAULT_CDP_PORT=9222
DEFAULT_PROFILE="/tmp/chrome-inspect"

# Parse arguments
TARGET_URL="${1:-${TARGET_URL:-$DEFAULT_URL}}"
CDP_PORT="${2:-${CDP_PORT:-$DEFAULT_CDP_PORT}}"
CHROME_PROFILE="${CHROME_PROFILE:-$DEFAULT_PROFILE}"

# Detect Chrome binary (cross-platform: macOS, Linux)
find_chrome() {
    # macOS: check Applications bundles first
    if [[ "$(uname)" == "Darwin" ]]; then
        local mac_paths=(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
            "/Applications/Chromium.app/Contents/MacOS/Chromium"
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
        )
        for path in "${mac_paths[@]}"; do
            if [ -x "$path" ]; then
                echo "$path"
                return
            fi
        done
    fi

    # Linux / PATH-based fallback
    if command -v google-chrome &> /dev/null; then
        echo "google-chrome"
    elif command -v google-chrome-stable &> /dev/null; then
        echo "google-chrome-stable"
    elif command -v chromium-browser &> /dev/null; then
        echo "chromium-browser"
    elif command -v chromium &> /dev/null; then
        echo "chromium"
    elif command -v chrome &> /dev/null; then
        echo "chrome"
    else
        echo ""
    fi
}

CHROME_BIN=$(find_chrome)
if [ -z "$CHROME_BIN" ]; then
    echo "❌ Error: Google Chrome not found. Install it with:"
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "   brew install --cask google-chrome           # macOS (Homebrew)"
        echo "   or download from https://www.google.com/chrome/"
    else
        echo "   sudo apt-get install google-chrome-stable   # Debian/Ubuntu"
        echo "   or use your package manager"
    fi
    exit 1
fi

echo "📍 Chrome binary: $CHROME_BIN"
echo "🔌 CDP port: $CDP_PORT"
echo "📂 Profile dir: $CHROME_PROFILE"
echo "🔗 Target URL: $TARGET_URL"
echo ""

# Check if Chrome is already running on the CDP port
if nc -z localhost $CDP_PORT 2>/dev/null; then
    echo "✓ Chrome already running on port $CDP_PORT"
    echo "  Opening $TARGET_URL in existing instance..."

    # Open URL in the SAME Chrome instance (not the default browser).
    # Invoking the Chrome binary with a URL while Chrome is already running
    # opens the URL as a new tab in the existing process.
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS: bypass `open` (which would use Safari/default browser) and
        # call Chrome directly so the URL lands in the CDP-enabled instance.
        "$CHROME_BIN" --user-data-dir="$CHROME_PROFILE" "$TARGET_URL" \
            > /dev/null 2>&1 &
    elif command -v xdg-open &> /dev/null; then
        xdg-open "$TARGET_URL" &
    else
        "$CHROME_BIN" --user-data-dir="$CHROME_PROFILE" "$TARGET_URL" \
            > /dev/null 2>&1 &
    fi
    echo ""
    echo "✅ Ready for testing. CDP endpoint: http://localhost:$CDP_PORT"
    exit 0
fi

# Kill any lingering Chrome processes (optional cleanup)
if pgrep -f "remote-debugging-port=$CDP_PORT" > /dev/null; then
    echo "🔄 Cleaning up old Chrome process on port $CDP_PORT..."
    pkill -f "remote-debugging-port=$CDP_PORT" || true
    sleep 2
fi

# Clean up profile if requested (optional for fresh state)
# Uncomment to start fresh: rm -rf "$CHROME_PROFILE"

# Ensure profile directory exists
mkdir -p "$CHROME_PROFILE"

echo "🚀 Launching Chrome with CDP..."
echo ""

# Launch Chrome with CDP
# --disable-background-networking: Reduce noise
# --disable-sync: Prevent sync dialogs
# --no-first-run: Skip first-run UI
# --no-default-browser-check: Skip browser check
"$CHROME_BIN" \
    --remote-debugging-port=$CDP_PORT \
    --user-data-dir="$CHROME_PROFILE" \
    --disable-background-networking \
    --disable-sync \
    --no-first-run \
    --no-default-browser-check \
    "$TARGET_URL" \
    > /dev/null 2>&1 &

CHROME_PID=$!
echo "✓ Chrome launched (PID: $CHROME_PID)"
echo ""

# Wait for CDP to be ready
echo "⏳ Waiting for CDP to be ready..."
for i in {1..30}; do
    if nc -z localhost $CDP_PORT 2>/dev/null; then
        echo "✅ CDP ready on http://localhost:$CDP_PORT"
        echo ""
        echo "Browser is open and ready for testing."
        echo "You can now run: python run_interactive.py"
        echo ""
        echo "To stop Chrome: kill $CHROME_PID"
        exit 0
    fi
    echo -n "."
    sleep 0.5
done

echo ""
echo "❌ CDP did not respond within 15 seconds"
echo "Chrome may still be starting. Try waiting a moment and reconnecting."
exit 1
