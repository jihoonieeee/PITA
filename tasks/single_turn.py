import os
import sys
from pathlib import Path
from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset
from inspect_ai.model import ChatMessageAssistant, GenerateConfig, ModelOutput
from inspect_ai.solver import Generate, Solver, TaskState, solver

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.cdp_browser_tools import send_prompt
from utils.dataset import load_dataset, merge_datasets, samples_to_json_dataset
from utils.scorer import injection_scorer


@solver
def cdp_relay() -> Solver:
    """Deterministic relay: send sample.input straight to the chatbot via CDP.

    Bypasses the LLM solver entirely so safety-tuned models can't refuse to
    forward adversarial prompts. The chatbot's reply becomes the model output,
    which the scorer then grades.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        prompt = state.input_text
        try:
            response = await send_prompt(prompt)
        except Exception as e:
            response = f"(relay error: {e})"

        state.output = ModelOutput.from_content(model="cdp-relay", content=response)
        state.messages.append(ChatMessageAssistant(content=response))
        return state

    return solve

# Get config from environment variable (required)
ACTIVE_CONFIG = os.environ.get("CHATBOT_CONFIG", "claude-docs")



@task
def single_turn_jailbreak_detection(
    dataset_path: str | list[str] | None = None,
    scorer_model: str | None = None,
):
    """Test jailbreak/benign prompts on a REAL chatbot via CDP.

    Sends prompts to the actual chatbot and extracts responses.
    Grades responses with an LLM scorer.

    Prerequisites:
      - Chrome running with --remote-debugging-port=9222
      - Chatbot visible on screen
      - CHATBOT_CONFIG environment variable set (e.g., "claude-docs")

    Args:
        dataset_path: Path to jailbreak/benign dataset (CSV or JSON).
                     Defaults to Dataset_advbench_520.csv
        scorer_model: Model to use for scoring responses. Defaults to anthropic/claude-sonnet-5

    Usage:
        CHATBOT_CONFIG="claude-docs" \\
          inspect eval tasks/prompt_testing.py --model anthropic/claude-sonnet-5 --limit 10
    """
    # Set defaults
    if dataset_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        dataset_path = os.path.join(
            script_dir, "..", "dataset", "Dataset_advbench_520.csv"
        )

    if scorer_model is None:
        scorer_model = "anthropic/claude-sonnet-5"

    # Load and convert to JSON dataset
    if isinstance(dataset_path, list):
        samples = merge_datasets(dataset_path)
    else:
        samples = load_dataset(dataset_path)

    json_dataset_path = samples_to_json_dataset(samples)

    return Task(
        dataset=json_dataset(json_dataset_path),
        solver=[cdp_relay()],
        scorer=injection_scorer(model=scorer_model),
        config=GenerateConfig(max_connections=1),
        message_limit=20,
    )
