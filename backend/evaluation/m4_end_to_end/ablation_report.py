"""Build a deterministic mechanism analysis from saved outcome and judge artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def _read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def build_analysis(outcomes: list[dict], judged: list[dict]) -> tuple[dict, str]:
    dimensions: dict[str, dict[str, Counter[str]]] = {}
    labels: dict[str, Counter[str]] = {}
    for row in judged:
        variant = str(row.get("variant", "unknown"))
        verdict = row.get("interaction_judge", {})
        variant_dimensions = dimensions.setdefault(variant, {})
        for name, value in verdict.get("dimensions", {}).items():
            variant_dimensions.setdefault(name, Counter())[str(value)] += 1
        labels.setdefault(variant, Counter()).update(verdict.get("failure_labels", []))

    serializable = {
        "outcomes": outcomes,
        "interaction": {
            variant: {
                "dimensions": {
                    name: dict(counts) for name, counts in sorted(items.items())
                },
                "failure_labels": dict(labels.get(variant, Counter())),
            }
            for variant, items in sorted(dimensions.items())
        },
    }
    outcome_by_variant = {item["variant"]: item for item in outcomes}
    lines = [
        "# M4 Ablation Analysis",
        "",
        (
            "Environment outcomes are the success authority. The independent LLM Judge is "
            "auxiliary evidence about visible interaction behavior and cannot turn an unmet "
            "goal into a pass."
        ),
        "",
        "## Outcome comparison",
        "",
        "| variant | pass | M3 reached | model calls | tool calls | average latency s |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for item in outcomes:
        lines.append(
            f"| {item['variant']} | {item['passed']}/{item['episodes']} | "
            f"{item['m3_reached']} | {item['model_calls']} | {item['tool_calls']} | "
            f"{item['avg_latency_ms'] / 1000:.2f} |"
        )
    lines.extend(["", "## Interaction dimensions by variant", ""])
    for variant, items in sorted(dimensions.items()):
        lines.extend(
            [
                f"### {variant}",
                "",
                "| dimension | PASS | PARTIAL | FAIL | NOT_APPLICABLE |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for name, counts in sorted(items.items()):
            lines.append(
                f"| {name} | {counts['PASS']} | {counts['PARTIAL']} | "
                f"{counts['FAIL']} | {counts['NOT_APPLICABLE']} |"
            )
        lines.extend(["", f"Failure labels: `{dict(labels.get(variant, Counter()))}`", ""])

    full = outcome_by_variant.get("v2_full", {})
    m2_only = outcome_by_variant.get("v2_m2_only", {})
    fixed = outcome_by_variant.get("v2_fixed_three", {})
    lines.extend(
        [
            "## Strict interpretation",
            "",
            (
                f"- The full closed loop passed {full.get('passed', 0)}/"
                f"{full.get('episodes', 0)} declared environment outcomes."
            ),
            (
                f"- M2-only passed {m2_only.get('passed', 0)}/{m2_only.get('episodes', 0)}; "
                "its unresolved specialist-recovery episode is direct evidence for sparse M3."
            ),
            (
                f"- Fixed-three passed {fixed.get('passed', 0)}/{fixed.get('episodes', 0)}; "
                "adaptive collaboration currently shows only a small cost advantage, not a "
                "success-rate advantage, on this subset."
            ),
            (
                "- Epistemic separation has the clearest interaction evidence: removing it "
                "introduced hypothesis-as-fact failures and reduced epistemic-humility passes."
            ),
            (
                "- User-state control shows a modest interaction benefit but is frequently "
                "ignored; the representation is ahead of the control effect."
            ),
            (
                "- Failure memory preserves visible retry discipline, but this subset does not "
                "show a final-outcome gain. Keep the lightweight control and expand stress cases "
                "before claiming broader benefit."
            ),
            "",
        ]
    )
    return serializable, "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the M4 ablation analysis")
    parser.add_argument("--outcomes", required=True)
    parser.add_argument("--judged", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    outcomes = json.loads(Path(args.outcomes).read_text(encoding="utf-8"))
    payload, report = build_analysis(outcomes, _read_jsonl(Path(args.judged)))
    (output / "ablation_analysis.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output / "M4_ABLATION_ANALYSIS.md").write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
