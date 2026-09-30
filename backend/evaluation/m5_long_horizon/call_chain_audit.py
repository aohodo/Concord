"""Audit whether a design was produced, consumed, activated and measured.

The audit deliberately refuses to label a mechanism ineffective when it never
reached a behavior consumer or when the evaluated Cases did not trigger it.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

CONTROL_CHAIN_MANIFEST = {
    "case_user_state": {
        "producer": "core/problem_formulation/models.py:CaseUserState",
        "transfer": "SharedProblemState.to_resolution_context.user_state",
        "consumer": "core/adaptive_resolution/graph.py:_select_tool/_finalize",
        "behavior": "tool latency/burden weights and response load",
    },
    "failure_memory": {
        "producer": "adaptive_resolution.tool_history",
        "transfer": "ResolutionGraphState.tool_history",
        "consumer": "AdaptiveResolutionGraph._same_failed_call",
        "behavior": "block identical failed call until retry condition changes",
    },
    "m3_invocation_policy": {
        "producer": "M2 collaboration_required or explicit control variant",
        "transfer": "durable collaborate_then_resume CaseJob",
        "consumer": "DurableCaseJobWorker",
        "behavior": "avoid or schedule collaboration",
    },
    "adaptive_m3": {
        "producer": "M3 coordinator plan",
        "transfer": "CollaborationPlan and M2ResumePacket",
        "consumer": "M3 workers then M2 independent verification",
        "behavior": "dynamic mode/team/expansion/stopping",
    },
}


def _event_dict(item: Any) -> dict[str, Any]:
    return item if isinstance(item, dict) else item.model_dump(mode="json")


def audit_case_snapshots(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for snapshot in snapshots:
        for raw_event in snapshot.get("events", []):
            event = _event_dict(raw_event)
            if event.get("event_type") != "control_mechanism_evaluated":
                continue
            mechanism = str(event.get("data", {}).get("mechanism", ""))
            if mechanism:
                grouped[mechanism].append(event)

    rows: list[dict[str, Any]] = []
    for mechanism, contract in CONTROL_CHAIN_MANIFEST.items():
        events = grouped.get(mechanism, [])
        activated = [item for item in events if item.get("status") == "activated"]
        behavior_events = [
            item for item in activated if item.get("data", {}).get("behavior_changes")
        ]
        if not events:
            classification = "NOT_EVALUATED_CALL_CHAIN_MISSING"
        elif not activated:
            classification = "EVALUATED_NOT_TRIGGERED"
        elif not behavior_events:
            classification = "ACTIVATED_WITHOUT_BEHAVIOR_CHANGE"
        else:
            classification = "ACTIVATED_AND_CONSUMED"
        if mechanism == "adaptive_m3":
            downstream_outcomes = sum(
                any(
                    _event_dict(event).get("event_type")
                    == "m2_resumed_after_collaboration"
                    for event in snapshot.get("events", [])
                )
                for snapshot in snapshots
            )
            outcome_evidence = "m2_resumed_after_collaboration"
        elif mechanism == "m3_invocation_policy":
            downstream_outcomes = sum(
                any(
                    _event_dict(event).get("event_type") == "durable_job_enqueued"
                    for event in snapshot.get("events", [])
                )
                for snapshot in snapshots
            )
            outcome_evidence = "durable_job_enqueued"
        else:
            downstream_outcomes = sum(
                bool(item.get("data", {}).get("outcome")) for item in behavior_events
            )
            outcome_evidence = "control_event.outcome"
        rows.append(
            {
                "mechanism": mechanism,
                **contract,
                "evaluations": len(events),
                "activations": len(activated),
                "behavior_changes": len(behavior_events),
                "downstream_outcomes": downstream_outcomes,
                "outcome_evidence": outcome_evidence,
                "classification": classification,
                "effectiveness_evaluable": (
                    classification == "ACTIVATED_AND_CONSUMED"
                    and downstream_outcomes > 0
                ),
            }
        )
    return rows


def write_call_chain_report(
    snapshots: list[dict[str, Any]], output_dir: str | Path
) -> list[dict[str, Any]]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rows = audit_case_snapshots(snapshots)
    (output / "call_chain_audit.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output / "call_chain_audit.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# M5 Control Call-Chain Audit",
        "",
        "A mechanism is never judged ineffective merely because it was not triggered.",
        "",
        (
            "| mechanism | evaluated | activated | behavior changed | downstream "
            "outcome | classification | effectiveness evaluable |"
        ),
        "|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['mechanism']} | {row['evaluations']} | {row['activations']} | "
            f"{row['behavior_changes']} | {row['downstream_outcomes']} | "
            f"{row['classification']} | {row['effectiveness_evaluable']} |"
        )
    lines.extend(
        [
            "",
            "Behavior activation alone is insufficient. Effectiveness is evaluable only ",
            "when the downstream consumer also records outcome evidence; cost/benefit ",
            "claims still require a paired control.",
            "",
        ]
    )
    (output / "M5_CALL_CHAIN_AUDIT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )
    return rows


def rebuild_from_long_episode_jsonl(
    input_path: str | Path, output_dir: str | Path
) -> list[dict[str, Any]]:
    snapshots = []
    for line in Path(input_path).resolve().read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        run = json.loads(line)
        snapshot = dict(run.get("episode", {}).get("case_snapshot", {}))
        snapshot["events"] = run.get("lifecycle_events", snapshot.get("events", []))
        snapshots.append(snapshot)
    return write_call_chain_report(snapshots, output_dir)


__all__ = [
    "CONTROL_CHAIN_MANIFEST",
    "audit_case_snapshots",
    "rebuild_from_long_episode_jsonl",
    "write_call_chain_report",
]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild the M5 call-chain audit")
    parser.add_argument("--input", required=True, help="M5 raw_long_episodes.jsonl")
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    rebuild_from_long_episode_jsonl(args.input, args.output_dir)
