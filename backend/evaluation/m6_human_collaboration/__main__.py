from __future__ import annotations

import argparse

from .runner import evaluate_cases, save_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run M6 human-collaboration policy eval")
    parser.add_argument(
        "--output-dir",
        default="evaluation/m6_human_collaboration/results/policy",
    )
    args = parser.parse_args()
    results = evaluate_cases()
    save_report(args.output_dir, results)
    print(f"M6 policy: {sum(item['passed'] for item in results)}/{len(results)} passed")


if __name__ == "__main__":
    main()
