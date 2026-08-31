#!/usr/bin/env python3
"""
Interactive CLI to run multi-turn jailbreak tests on a REAL chatbot via CDP.
Unlike run_refinement.py (fresh chat per attempt), the chat stays open across
rounds for a given seed prompt: an attacker model escalates turn-by-turn
in-conversation, picking from the multi-turn technique catalog, up to
MAX_ROUNDS times.
"""

import os
import sys
import argparse
from pathlib import Path

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from inspect_ai import eval
from tasks.multi_turn import multi_turn_jailbreak_detection
from utils.cli import (
    ensure_chatbot_config,
    resolve_dataset_paths,
    select_adversary_model,
    select_context,
    select_dataset,
    select_limit,
    select_positive_int,
    select_scorer_model,
)


def select_max_rounds():
    """Interactive max rounds input."""
    return select_positive_int(
        title="Enter Max Rounds Per Sample",
        description="How many in-conversation turns the attacker may use to escalate before giving up.",
        examples=[
            "  1  - Seed only, no escalation",
            "  5  - Quick escalation pass",
            " 10  - Standard (default)",
            " 15  - Deep escalation (slow, CDP is serial)",
        ],
        default=10,
        input_prompt="Enter max rounds (default 10): ",
    )


def interactive_mode():
    """Run in interactive mode."""
    print("\n" + "=" * 50)
    print("CHATBOT MULTI-TURN JAILBREAK TEST RUNNER")
    print("=" * 50)
    print("\nNote: This test runs prompts on a REAL chatbot via CDP")
    print("Make sure Chrome is running with --remote-debugging-port=9222")
    print("\nUnlike refinement mode, the chat stays open across rounds for each")
    print("seed prompt — escalation happens in the SAME conversation, not a fresh one.")
    print()

    # Check for CHATBOT_CONFIG
    chatbot_config = ensure_chatbot_config()
    if chatbot_config is None:
        return

    # Context selection
    context = select_context(chatbot_config, used_by="attacker model")
    os.environ["CHATBOT_CONTEXT"] = context

    # Dataset selection
    datasets = select_dataset()

    # Limit selection
    limit = select_limit()

    # Scorer model selection
    scorer_model = select_scorer_model()

    # Adversary model selection
    adversary_model = select_adversary_model(
        "Choose the model that should plan strategy and word each in-conversation turn.",
        legacy_env_var="ATTACKER_MODEL",
    )

    # Max rounds selection
    max_rounds = select_max_rounds()

    # Confirmation
    print("\n" + "=" * 50)
    print("Configuration Summary")
    print("=" * 50)
    print(f"Chatbot Config:   {os.environ.get('CHATBOT_CONFIG', 'Not set')}")
    print(f"Context:          {context if context else '(none)'}")
    print(f"Datasets: {len(datasets)} dataset(s)")
    for i, ds in enumerate(datasets, 1):
        print(f"  {i}. {ds}")
    print(f"Scorer Model:     {scorer_model}")
    print(f"Adversary Model:  {adversary_model}")
    print(f"Max Rounds:       {max_rounds}")
    print(f"Sample Limit:     {limit if limit else 'All samples'}")
    print()

    confirm = input("Run evaluation with this configuration? (y/n): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    # Run evaluation (pass single dataset or list for merging)
    dataset_arg = datasets[0] if len(datasets) == 1 else datasets
    run_multiturn_evaluation(dataset_arg, scorer_model, adversary_model, max_rounds, limit)


def run_multiturn_evaluation(dataset, scorer_model, adversary_model, max_rounds=10, limit=10):
    """Run the multi-turn evaluation with specified parameters.

    Args:
        dataset: Single dataset path (str) or list of dataset paths to merge
        scorer_model: Model to use for judging each round
        adversary_model: Model to use for in-conversation strategy + phrasing
        max_rounds: Maximum in-conversation rounds per seed prompt
        limit: Maximum number of samples. None means all samples.
    """
    # Resolve dataset path(s)
    script_dir = os.path.dirname(os.path.abspath(__file__))

    dataset_arg = resolve_dataset_paths(dataset, script_dir)
    if dataset_arg is None:
        return

    if isinstance(dataset_arg, list):
        print(f"\nRunning multi-turn evaluation on merged dataset ({len(dataset_arg)} files)...")
    else:
        print(f"\nRunning multi-turn evaluation...")

    print(f"  Scorer Model:    {scorer_model}")
    print(f"  Adversary Model: {adversary_model}")
    print(f"  Max Rounds:      {max_rounds}")
    print(f"  Sample Limit:    {limit if limit else 'All samples'}")
    print()

    # Keep env vars in sync for any code that reads them directly. ADVERSARY_MODEL
    # is canonical; also mirror to ATTACKER_MODEL so older readers keep working.
    os.environ["ADVERSARY_MODEL"] = adversary_model
    os.environ["ATTACKER_MODEL"] = adversary_model
    os.environ["MAX_ROUNDS"] = str(max_rounds)

    try:
        # Create task using multi_turn_jailbreak_detection
        task = multi_turn_jailbreak_detection(
            dataset_path=dataset_arg,
            scorer_model=scorer_model,
            adversary_model=adversary_model,
            max_rounds=max_rounds,
        )

        # Run evaluation with limit parameter
        results = eval(task, model=scorer_model, limit=limit)
        print("\n[OK] Multi-turn evaluation completed successfully!")
        print(f"Results logged to: logs/")
    except Exception as e:
        print(f"[ERROR] Error during evaluation: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(
        description="Run multi-turn jailbreak tests on a REAL chatbot via CDP (Chrome DevTools Protocol)"
    )
    parser.add_argument(
        "--chatbot-config",
        type=str,
        help="Chatbot config name from selectors.json (e.g., 'claude-docs'). Can also set CHATBOT_CONFIG env var.",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        help="Path to dataset CSV or JSON file (use comma-separated for multiple)",
    )
    parser.add_argument(
        "--scorer-model",
        type=str,
        help="Model to use for scoring responses (default: anthropic/claude-haiku-4-5)",
    )
    parser.add_argument(
        "--adversary-model",
        "--attacker-model",
        dest="adversary_model",
        type=str,
        help="Model to use for in-conversation strategy + phrasing. Can also set "
             "ADVERSARY_MODEL env var (legacy ATTACKER_MODEL still honored). "
             "--attacker-model is a deprecated alias.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=10,
        help="Maximum in-conversation rounds per seed prompt (default: 10)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of samples to evaluate (default: 10, use 0 for all)",
    )
    parser.add_argument(
        "--context",
        type=str,
        help="Description of the target chatbot's purpose and domain. Used by the "
             "adversary to craft more targeted prompts. Can also set CHATBOT_CONTEXT env var.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run with provided arguments without interactive prompts",
    )

    args = parser.parse_args()

    # Set CHATBOT_CONFIG if provided
    if args.chatbot_config:
        os.environ["CHATBOT_CONFIG"] = args.chatbot_config

    # Context: CLI flag > env var (selectors.json context loaded at runtime by _get_context)
    if args.context:
        os.environ["CHATBOT_CONTEXT"] = args.context

    # Validate and convert limit: 0 means None (all samples)
    if args.limit < 0:
        print("Error: --limit must be 0 or a positive number.")
        sys.exit(1)

    limit = None if args.limit == 0 else args.limit

    # Resolve scorer and adversary models (flag > env var > default).
    # Adversary env fallback: ADVERSARY_MODEL, then legacy ATTACKER_MODEL.
    scorer_model = args.scorer_model or os.environ.get("SCORER_MODEL", "anthropic/claude-haiku-4-5")
    adversary_model = (
        args.adversary_model
        or os.environ.get("ADVERSARY_MODEL")
        or os.environ.get("ATTACKER_MODEL", "")
    )

    # Parse multiple datasets if comma-separated
    datasets = None
    if args.dataset:
        datasets = [d.strip() for d in args.dataset.split(",") if d.strip()]

    # If all required arguments provided with --non-interactive flag
    if (
        args.non_interactive
        and datasets
        and scorer_model
        and adversary_model
    ):
        dataset_arg = datasets[0] if len(datasets) == 1 else datasets
        run_multiturn_evaluation(dataset_arg, scorer_model, adversary_model, args.max_rounds, limit)
    # If all required arguments provided without --non-interactive flag
    elif datasets and scorer_model and adversary_model:
        dataset_arg = datasets[0] if len(datasets) == 1 else datasets
        run_multiturn_evaluation(dataset_arg, scorer_model, adversary_model, args.max_rounds, limit)
    # Otherwise, run interactive mode
    else:
        if args.dataset or args.scorer_model or adversary_model or args.limit != 10:
            print(
                "Warning: Some arguments provided but not all. Running in interactive mode."
            )
        interactive_mode()


if __name__ == "__main__":
    main()
