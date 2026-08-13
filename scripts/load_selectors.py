"""
load_selectors.py

Load chatbot selectors from selectors.json and convert to the format
expected by cdp_browser_tools.py.

Usage:
    from load_selectors import get_config
    config = get_config("claude-code-docs")
"""

import json
import os
from pathlib import Path


def get_config(chatbot_name):
    """
    Load selector config from selectors.json for a given chatbot.

    Returns a dict compatible with cdp_browser_tools.py:
    {
        "url": "https://...",
        "name": "...",
        "chat_input": {"selector": "...", "description": "..."},
        "response_selectors": [{"selector": "...", "description": "..."}, ...],
        "toggle_button": {"selector": "..." or None, "description": "..."},
        "consent_banner": {"selector": "..." or None, "description": "..."},
    }
    """
    # Try selectors.json in parent directory
    selectors_path = Path(__file__).parent.parent / "selectors.json"

    if not selectors_path.exists():
        print(f"⚠ selectors.json not found at {selectors_path}")
        return None

    with open(selectors_path) as f:
        data = json.load(f)

    chatbot = data.get("chatbots", {}).get(chatbot_name)
    if not chatbot:
        print(f"❌ Chatbot '{chatbot_name}' not found in selectors.json")
        print(f"   Available: {list(data.get('chatbots', {}).keys())}")
        return None

    # Convert from stored format to expected format
    config = {
        "url": chatbot.get("url"),
        "name": chatbot.get("name"),
        "chat_input": {
            "selector": chatbot.get("chat_input", {}).get("selector"),
            "description": chatbot.get("chat_input", {}).get("description", "Chat input field"),
        },
        "response_selectors": chatbot.get("response_selectors", []),
        "toggle_button": {
            "selector": chatbot.get("toggle_button", {}).get("selector"),
            "description": chatbot.get("toggle_button", {}).get("description", "Toggle assistant panel"),
        },
        "consent_banner": {
            "selector": chatbot.get("consent_banner", {}).get("selector"),
            "description": chatbot.get("consent_banner", {}).get("description", "Consent banner"),
        },
    }

    # Include reset_button if present. Consumed by cdp_browser_tools._reset_chat
    # to isolate each prompt: when RESET_BETWEEN_PROMPTS is enabled the button is
    # clicked before sending; with no selector it falls back to page.reload().
    reset_button = chatbot.get("reset_button")
    if reset_button:
        config["reset_button"] = {
            "selector": reset_button.get("selector"),
            "confirm_selector": reset_button.get("confirm_selector"),
            "fallback": reset_button.get("fallback", "reload"),
            "description": reset_button.get("description", "Reset / new chat button"),
        }

    # Include char_limit if present
    if chatbot.get("char_limit") is not None:
        config["char_limit"] = int(chatbot["char_limit"])

    # Include context if present
    if chatbot.get("context"):
        config["context"] = chatbot["context"]

    # Include iframe metadata if present
    if chatbot.get("is_iframe"):
        config["is_iframe"] = True
        config["iframe_index"] = chatbot.get("iframe_index", 0)

    # Include shadow-DOM metadata if present (consumed by cdp_browser_tools.py
    # to switch response queries to a shadow-piercing helper).
    if chatbot.get("is_shadow_dom"):
        config["is_shadow_dom"] = True
        if chatbot.get("shadow_host"):
            config["shadow_host"] = chatbot["shadow_host"]

    return config


def save_context(chatbot_name: str, context: str) -> bool:
    """Persist a context string for a chatbot back into selectors.json.

    Returns True on success, False if the chatbot wasn't found.
    """
    selectors_path = Path(__file__).parent.parent / "selectors.json"
    if not selectors_path.exists():
        return False

    with open(selectors_path, encoding="utf-8") as f:
        data = json.load(f)

    if chatbot_name not in data.get("chatbots", {}):
        return False

    if context:
        data["chatbots"][chatbot_name]["context"] = context
    else:
        data["chatbots"][chatbot_name].pop("context", None)

    with open(selectors_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    return True


def list_chatbots():
    """List all available chatbots in selectors.json"""
    selectors_path = Path(__file__).parent.parent / "selectors.json"

    if not selectors_path.exists():
        return {}

    with open(selectors_path) as f:
        data = json.load(f)

    return data.get("chatbots", {})


if __name__ == "__main__":
    print("Available chatbots:")
    for name, config in list_chatbots().items():
        print(f"  - {name}: {config.get('url')}")
