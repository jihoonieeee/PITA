"""
Convert an Inspect AI eval log (.json or .eval) to CSV for easy review.

Works for single-turn and refinement logs, and both file formats.
- Single-turn: one row per sample.
- Refinement:  one row per iteration (_iterations.csv) +
               one summary row per sample (_summary.csv).

Usage:
    python eval_to_csv.py <path/to/log.json>
    python eval_to_csv.py <path/to/log.eval>
    python eval_to_csv.py <path/to/log.json> --output results.csv

Single-turn columns:
    id, type, grade, score, closeness, prompt, chatbot_response, reasoning

Refinement iteration columns:
    sample_id, type, iteration, grade, score, closeness,
    prompt, chatbot_response, reasoning,
    next_technique, next_explanation, next_prompt

Refinement summary columns:
    sample_id, type, seed_prompt, final_grade, score,
    bypass_iteration, best_closeness, total_iterations, explanation
"""

import argparse
import csv
import json
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Load data from .json or .eval
# ---------------------------------------------------------------------------

def load_data(path: Path) -> dict:
    """Load eval data from a .json or .eval file."""
    if path.suffix == ".eval":
        path = _convert_eval_to_json(path)
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _convert_eval_to_json(eval_path: Path) -> Path:
    """
    Run 'inspect log convert' to produce a .json alongside the .eval,
    then return the path to that .json file.
    """
    import subprocess

    output_dir = eval_path.parent
    json_path = eval_path.with_suffix(".json")

    if json_path.exists():
        print(f"Found existing JSON: {json_path}")
        return json_path

    print(f"Converting {eval_path.name} to JSON...")
    result = subprocess.run(
        ["inspect", "log", "convert", "--to", "json", "--output-dir", str(output_dir), str(eval_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("Error running 'inspect log convert':")
        print(result.stderr)
        sys.exit(1)

    if not json_path.exists():
        print(f"Error: expected {json_path} to be created but it was not found.")
        sys.exit(1)

    print(f"Converted -> {json_path}")
    return json_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_prompt(input_field) -> str:
    if isinstance(input_field, list):
        for msg in input_field:
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                return str(content)
    return str(input_field)


def extract_response(messages) -> str:
    for msg in reversed(messages or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = msg.get("content", "")
            if isinstance(content, list):
                return " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            return str(content)
    return ""


def detect_mode(samples: list) -> str:
    for s in samples[:5]:
        scores = s.get("scores") or {}
        if "refinement_scorer" in scores:
            return "refinement"
        lineage = (s.get("metadata") or {}).get("lineage") or []
        if lineage:
            return "refinement"
    return "single_turn"


# ---------------------------------------------------------------------------
# Single-turn
# ---------------------------------------------------------------------------

SINGLE_COLS = ["id", "type", "grade", "score", "closeness", "prompt", "chatbot_response", "reasoning"]


def single_turn_row(sample: dict) -> dict:
    scorer_data = (sample.get("scores") or {}).get("injection_scorer", {})
    meta = scorer_data.get("metadata") or {}
    sample_type = meta.get("type") or (sample.get("metadata") or {}).get("type", "")
    reasoning = meta.get("reasoning") or scorer_data.get("explanation", "")
    return {
        "id": sample.get("id", ""),
        "type": sample_type,
        "grade": meta.get("grade_label", ""),
        "score": scorer_data.get("value", ""),
        "closeness": meta.get("closeness", ""),
        "prompt": extract_prompt(sample.get("input", [])),
        "chatbot_response": extract_response(sample.get("messages", [])),
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# Refinement
# ---------------------------------------------------------------------------

ITER_COLS = [
    "sample_id", "type", "iteration", "grade", "score", "closeness",
    "prompt", "chatbot_response", "reasoning",
    "next_technique", "next_explanation", "next_prompt",
]

SUMMARY_COLS = [
    "sample_id", "type", "seed_prompt",
    "final_grade", "score", "bypass_iteration",
    "best_closeness", "total_iterations", "explanation",
]


def refinement_rows(sample: dict):
    sample_id = sample.get("id", "")
    sample_type = (sample.get("metadata") or {}).get("type", "")
    lineage = (sample.get("metadata") or {}).get("lineage", [])

    scorer_data = (sample.get("scores") or {}).get("refinement_scorer", {})
    scorer_meta = scorer_data.get("metadata") or {}

    iter_rows = []
    for entry in lineage:
        iter_rows.append({
            "sample_id": sample_id,
            "type": sample_type,
            "iteration": entry.get("iteration", ""),
            "grade": entry.get("grade_label", ""),
            "score": entry.get("score", ""),
            "closeness": entry.get("closeness", ""),
            "prompt": entry.get("prompt", ""),
            "chatbot_response": entry.get("response", ""),
            "reasoning": entry.get("judge_reasoning", ""),
            "next_technique": entry.get("next_refiner_technique", ""),
            "next_explanation": entry.get("next_refiner_explanation", ""),
            "next_prompt": entry.get("next_refiner_prompt", ""),
        })

    seed_prompt = lineage[0].get("prompt", "") if lineage else extract_prompt(sample.get("input", []))
    summary_row = {
        "sample_id": sample_id,
        "type": sample_type,
        "seed_prompt": seed_prompt,
        "final_grade": scorer_meta.get("grade_label", ""),
        "score": scorer_data.get("value", ""),
        "bypass_iteration": scorer_meta.get("bypass_iteration", ""),
        "best_closeness": scorer_meta.get("best_closeness", ""),
        "total_iterations": scorer_meta.get("total_iterations", ""),
        "explanation": scorer_data.get("explanation", ""),
    }
    return iter_rows, summary_row


# ---------------------------------------------------------------------------
# Write + convert
# ---------------------------------------------------------------------------

def write_csv(path: Path, columns: list, rows: list) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def convert(input_path: Path, output_path: Path) -> None:
    data = load_data(input_path)

    samples = data.get("samples", [])
    if not samples:
        print("No samples found in the log file.")
        sys.exit(1)

    mode = detect_mode(samples)

    if mode == "single_turn":
        rows = [single_turn_row(s) for s in samples]
        write_csv(output_path, SINGLE_COLS, rows)
        print(f"[single-turn] Wrote {len(rows)} rows -> {output_path}")
        grades = {}
        for r in rows:
            grades[r["grade"]] = grades.get(r["grade"], 0) + 1
        print("Grade breakdown:", ", ".join(f"{k}: {v}" for k, v in sorted(grades.items())))

    else:
        all_iter_rows, all_summary_rows = [], []
        for s in samples:
            iter_rows, summary_row = refinement_rows(s)
            all_iter_rows.extend(iter_rows)
            all_summary_rows.append(summary_row)

        iter_path = output_path.with_stem(output_path.stem + "_iterations")
        summary_path = output_path.with_stem(output_path.stem + "_summary")

        write_csv(iter_path, ITER_COLS, all_iter_rows)
        write_csv(summary_path, SUMMARY_COLS, all_summary_rows)

        print(f"[refinement] Wrote {len(all_iter_rows)} iteration rows -> {iter_path}")
        print(f"[refinement] Wrote {len(all_summary_rows)} summary rows  -> {summary_path}")

        grades = {}
        for r in all_summary_rows:
            grades[r["final_grade"]] = grades.get(r["final_grade"], 0) + 1
        print("Final grade breakdown:", ", ".join(f"{k}: {v}" for k, v in sorted(grades.items())))
        bypassed = sum(1 for r in all_summary_rows if r["final_grade"] == "bypassed")
        print(f"Attack success rate: {bypassed}/{len(all_summary_rows)}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert Inspect AI eval log (.json or .eval) to CSV."
    )
    parser.add_argument("log_file", help="Path to .json or .eval log file")
    parser.add_argument(
        "--output", "-o", help="Output CSV path (default: same name as input with .csv)"
    )
    args = parser.parse_args()

    input_path = Path(args.log_file)
    if not input_path.exists():
        print(f"Error: file not found: {input_path}")
        sys.exit(1)

    output_path = Path(args.output) if args.output else input_path.with_suffix(".csv")
    convert(input_path, output_path)


if __name__ == "__main__":
    main()
