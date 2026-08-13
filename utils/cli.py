"""Shared interactive-CLI helpers for the run_*.py eval runners.

Both runners — run_interactive.py (single-turn) and run_refinement.py (Mode 2)
— collect the same kinds of terminal input (model IDs, dataset paths, sample
limits, target-chatbot context) and resolve dataset paths the same way. These
helpers live here so a fix or wording change lands in one place instead of two.
"""

import os


def select_scorer_model() -> str:
    """Interactive scorer-model input (no env fallback)."""
    print(f"\n{'=' * 50}")
    print("Enter Scorer Model")
    print(f"{'=' * 50}")
    print("Examples:")
    print("  anthropic/claude-haiku-4-5")
    print("  anthropic/claude-sonnet-5")
    print("  anthropic/claude-opus-4-8")
    print()

    while True:
        model = input("Enter model name: ").strip()
        if model:
            return model
        print("Model name cannot be empty. Please try again.")


def select_adversary_model(description: str, legacy_env_var: str | None = None) -> str:
    """Interactive adversary-model input; a blank answer falls back to an env var.

    The adversary model is the one that generates adversarial prompts by mutating
    failed prompts in refinement mode.

    Env fallback order: ADVERSARY_MODEL, then ``legacy_env_var`` (REFINER_MODEL
    for refinement) so existing scripts keep working.
    """
    print(f"\n{'=' * 50}")
    print("Enter Adversary Model")
    print(f"{'=' * 50}")
    print(description)
    print()
    print("Examples:")
    print("  anthropic/claude-sonnet-5")
    print("  openai/gpt-4o")
    print("  openai/gpt-4o-mini")
    print("  together_ai/meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo")
    print("  together_ai/deepseek-ai/DeepSeek-R1")
    print()

    env_model = os.environ.get("ADVERSARY_MODEL", "")
    env_source = "ADVERSARY_MODEL"
    if not env_model and legacy_env_var:
        env_model = os.environ.get(legacy_env_var, "")
        env_source = legacy_env_var
    if env_model:
        print(f"[ENV] {env_source} is set: {env_model}")
        print()

    while True:
        model = input("Enter adversary model name: ").strip()
        if not model:
            if env_model:
                return env_model
            print("Adversary model cannot be empty. Please try again.")
            continue
        return model


def select_dataset() -> list[str]:
    """Interactive dataset input - supports multiple datasets (CSV or JSON)."""
    print(f"\n{'=' * 50}")
    print("Enter Dataset Paths")
    print(f"{'=' * 50}")
    print("You can enter multiple datasets (comma-separated or one per line)")
    print("Supports both CSV and JSON formats")
    print("\nExamples:")
    print("  Single CSV: dataset/Dataset_walledai_520.csv")
    print("  Single JSON: dataset/jailbreak_data.json")
    print("  Multiple (comma): dataset/Dataset_1.csv, dataset/Dataset_2.json")
    print("  Multiple (lines): Enter each path and type 'done' when finished")
    print()

    datasets: list[str] = []
    while True:
        dataset_input = input("Enter dataset path (or 'done' to finish): ").strip()

        if dataset_input.lower() == "done":
            if datasets:
                return datasets
            print("Please enter at least one dataset path.")
            continue

        # Check if comma-separated
        if "," in dataset_input:
            multiple = [d.strip() for d in dataset_input.split(",") if d.strip()]
            datasets.extend(multiple)
            return datasets
        elif dataset_input:
            datasets.append(dataset_input)
            if len(datasets) == 1:
                # After first entry, show option to add more or finish
                print("  (Enter another path, or type 'done' to finish)")
        else:
            print("Dataset path cannot be empty. Please try again.")


def select_limit():
    """Interactive limit input. Returns an int, or None for 'all samples'."""
    print("\n" + "=" * 50)
    print("Enter Sample Limit")
    print("=" * 50)
    print("Examples:")
    print("  10   - Run 10 samples (default)")
    print("  50   - Run 50 samples")
    print("  100  - Run 100 samples")
    print("  0    - Run all samples (no limit)")
    print()

    while True:
        try:
            limit_input = input("Enter sample limit (or 0 for all): ").strip()
            if not limit_input:
                return 10  # Default to 10

            limit = int(limit_input)
            if limit < 0:
                print("Invalid input. Please enter 0 or a positive number.")
                continue
            elif limit == 0:
                return None  # 0 means all samples
            else:
                return limit
        except ValueError:
            print("Invalid input. Please enter a valid number.")


def select_positive_int(
    title: str, description: str, examples: list[str], default: int, input_prompt: str
) -> int:
    """Interactive positive-integer input with a default (min value 1).

    Used for max-iterations (refinement).
    """
    print("\n" + "=" * 50)
    print(title)
    print("=" * 50)
    print(description)
    print()
    print("Examples:")
    for line in examples:
        print(line)
    print()

    while True:
        try:
            val = input(input_prompt).strip()
            if not val:
                return default
            n = int(val)
            if n < 1:
                print("Must be at least 1. Please try again.")
                continue
            return n
        except ValueError:
            print("Invalid input. Please enter a valid number.")


def select_context(config_name: str = "", used_by: str = "refiner") -> str:
    """Ask the user for optional context about the target chatbot.

    Pre-fills from selectors.json if a context field exists there.
    The user can accept it, override it, or leave blank to skip.

    ``used_by`` names the consumer of the context (e.g. "refiner" or
    "attacker model") for the on-screen explanation only.
    """
    from scripts.load_selectors import get_config
    existing = ""
    if config_name:
        cfg = get_config(config_name) or {}
        existing = (cfg.get("context") or "").strip()

    print("\n" + "=" * 50)
    print("Target Chatbot Context (optional)")
    print("=" * 50)
    print("Describe what this chatbot does and any domain-specific details.")
    print(f"The {used_by} uses this to craft more targeted prompts.")
    print("Example: 'Customer service bot for Prompt Airlines. Handles flight")
    print("  bookings, cancellations, and baggage queries. Refuses off-topic requests.'")
    print()
    if existing:
        print(f"[config] Context from selectors.json: {existing}")
        print("Press Enter to use it, or type a new description to override.")
    else:
        print("Press Enter to skip.")
    print()

    print("Options:")
    print("  Enter        — use saved context (shown above)")
    print("  Type text    — use new context and save it")
    print("  none         — skip context for this run only")
    print()

    user_input = input("Context: ").strip()

    if user_input.lower() == "none":
        print("[OK] No context will be used for this run.")
        return ""

    result = user_input if user_input else existing

    if user_input and config_name:
        from scripts.load_selectors import save_context
        if save_context(config_name, result):
            print(f"[OK] Context saved to selectors.json for '{config_name}'")

    return result


def ensure_chatbot_config():
    """Ensure CHATBOT_CONFIG is set, prompting for it if needed.

    Returns the config name, or None if the user cancelled (entered nothing).
    On success the value is stored in the CHATBOT_CONFIG env var.
    """
    chatbot_config = os.environ.get("CHATBOT_CONFIG")
    if not chatbot_config:
        print("[WARN] CHATBOT_CONFIG environment variable not set!")
        print("Example: CHATBOT_CONFIG=\"claude-docs\"")
        print()
        chatbot_config = input("Enter chatbot config name (from selectors.json): ").strip()
        if chatbot_config:
            os.environ["CHATBOT_CONFIG"] = chatbot_config
        else:
            print("Cancelled.")
            return None

    print(f"[OK] Using chatbot config: {chatbot_config}\n")
    return chatbot_config


def resolve_dataset_paths(dataset, base_dir):
    """Resolve one or more dataset paths relative to base_dir and verify each exists.

    Args:
        dataset: A single dataset path (str) or a list of paths to merge.
        base_dir: Directory that relative paths are resolved against.

    Returns:
        The resolved path (str) or list of paths, ready to hand to a task.
        Returns None if any path is missing (an error is printed in that case).
    """
    def _resolve_one(ds):
        return ds if os.path.isabs(ds) else os.path.join(base_dir, ds)

    if isinstance(dataset, list):
        resolved_paths = []
        for ds in dataset:
            resolved_path = _resolve_one(ds)
            if not os.path.exists(resolved_path):
                print(f"Error: Dataset not found at {resolved_path}")
                return None
            resolved_paths.append(resolved_path)
        return resolved_paths

    resolved_path = _resolve_one(dataset)
    if not os.path.exists(resolved_path):
        print(f"Error: Dataset not found at {resolved_path}")
        return None
    return resolved_path
