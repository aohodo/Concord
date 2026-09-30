"""Build a public, synthetic M3 episode catalog with imperfect human inputs."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

OUTPUT = Path(__file__).with_name("data") / "m3_longitudinal_episodes.json"

SCENARIOS = [
    ("vendor-auth", "看不懂厂商认证轨迹", "specialist_handoff", ["ready_for_m2", "evidence_required", "human_required"], 1, ["specialized_diagnosis"]),
    ("urgent-split", "认证与依赖是独立方向", "parallel_workers", ["ready_for_m2", "evidence_required"], 3, ["specialized_diagnosis", "time_optimization"]),
    ("storage-risk", "恢复动作可能造成数据损坏", "independent_review", ["evidence_required", "human_required", "ready_for_m2"], 2, ["risk_review", "fact_verification"]),
    ("stagnation", "连续检查没有推进", "diverse_exploration", ["ready_for_m2", "evidence_required", "exhausted", "no_benefit"], 3, ["diverse_exploration", "counter_hypothesis"]),
    ("version-conflict", "部署版本来源冲突", "independent_review", ["evidence_required", "goal_alignment_required"], 2, ["fact_verification"]),
    ("goal-conflict", "恢复和根因目标冲突", "specialist_handoff", ["goal_alignment_required", "no_benefit"], 2, ["boundary_analysis"]),
    ("serial-job", "必须先读取权威任务状态", "parallel_workers", ["no_benefit", "ready_for_m2"], 1, []),
    ("cache-pending", "已有唯一低风险观察", "independent_review", ["no_benefit", "ready_for_m2", "evidence_required"], 1, []),
    ("partial-worker", "一个协作者不可用但另一方向仍可推进", "parallel_workers", ["ready_for_m2", "evidence_required", "exhausted"], 3, ["fact_verification"]),
    ("shared-source", "多个判断依赖同一条转述", "independent_review", ["evidence_required", "ready_for_m2", "no_benefit"], 2, ["source_reliability"]),
    ("changing-facts", "用户补充事实推翻初始方向", "diverse_exploration", ["ready_for_m2", "evidence_required", "no_benefit"], 3, ["counter_hypothesis"]),
    ("permission-boundary", "需要真实生产权限和责任人", "specialist_handoff", ["human_required", "ready_for_m2", "evidence_required", "no_benefit"], 1, ["boundary_analysis"]),
]

CONDITIONS = ("fragmented", "deadline", "correction")

DETAILS = {
    "vendor-auth": {
        "observed": [{"tool_id": "trace_reader", "observation": "opaque vendor code A17 after token exchange"}],
        "explanations": ["vendor token exchange rejected", "clock skew"],
        "open": "vendor trace semantics",
    },
    "urgent-split": {
        "observed": [{"tool_id": "health", "observation": "application reachable; authentication and dependency states unknown"}],
        "explanations": ["credential path", "dependency path"],
        "open": "two independent read-only checks",
    },
    "storage-risk": {
        "observed": [{"tool_id": "storage_health", "observation": "replica lagging; exact lag unavailable"}],
        "explanations": ["replica metadata corruption", "network partition", "I/O saturation"],
        "open": "authoritative storage and backup state",
    },
    "stagnation": {
        "observed": [{"tool_id": "logs", "observation": "no matching error in the searched time window"}],
        "explanations": ["network jitter"],
        "open": "an observation outside the anchored network path",
    },
    "version-conflict": {
        "observed": [{"tool_id": "provider_api", "observation": "version 8", "reliability": 0.9}],
        "explanations": [],
        "open": "authoritative deployment identity",
    },
    "goal-conflict": {
        "observed": [],
        "explanations": ["restore-first and root-cause-first imply incompatible next actions"],
        "open": "user priority",
    },
    "serial-job": {
        "observed": [{"tool_id": "job_submit", "observation": "accepted but completion unknown"}],
        "explanations": [],
        "open": "provider job state that unlocks all later decisions",
    },
    "cache-pending": {
        "observed": [{"tool_id": "cache_refresh", "observation": "accepted; propagation pending"}],
        "explanations": [],
        "open": "post-refresh health after declared wait window",
    },
    "partial-worker": {
        "observed": [{"tool_id": "dependency_map", "observation": "one vendor endpoint is unavailable"}],
        "explanations": ["vendor outage", "local configuration drift"],
        "open": "independent local evidence despite vendor unavailability",
    },
    "shared-source": {
        "observed": [],
        "explanations": ["three reports may all repeat the same operator message"],
        "open": "independent provider observation",
    },
    "changing-facts": {
        "observed": [{"tool_id": "network_check", "observation": "initial device reports timeout"}],
        "explanations": ["DNS failure", "device-local credential state"],
        "open": "cross-device comparison",
    },
    "permission-boundary": {
        "observed": [{"tool_id": "readonly_status", "observation": "production mitigation requires privileged approval"}],
        "explanations": ["safe mitigation exists but cannot be authorized by the agent"],
        "open": "responsible human approval",
    },
}


def _handoff(slug: str, description: str, mode: str, condition: str) -> dict:
    detail = DETAILS[slug]
    reported = [
        {
            "key": "user_dump",
            "value": "刚才还行 现在不行 我点过两次 反正就卡着",
            "epistemic_status": "reported_observation",
        },
        {
            "key": "user_guess",
            "value": "我觉得肯定是服务器坏了，但我也不确定",
            "epistemic_status": "reported_hypothesis",
        },
    ]
    goal = {"current_outcome": "先让当前事情恢复，再解释必要原因"}
    reason = description
    if condition == "deadline":
        goal["deadline_minutes"] = 12
        reason += "；用户正在催促，只愿意看必要步骤"
    coordination_structure = {
        "parallelism_known": False,
        "independent_workstreams": [],
        "serial_dependencies": [],
        "blocking_evidence": [],
        "human_authority_required": False,
    }
    if slug == "urgent-split":
        coordination_structure.update(
            {
                "parallelism_known": True,
                "independent_workstreams": ["credential path", "dependency path"],
            }
        )
    elif slug in {"serial-job", "cache-pending"}:
        coordination_structure.update(
            {
                "parallelism_known": True,
                "serial_dependencies": ["authoritative observation before later decisions"],
                "blocking_evidence": [detail["open"]],
            }
        )
    elif slug == "permission-boundary":
        coordination_structure["human_authority_required"] = True
    return {
        "case_id": f"m3-long-{slug}-{condition}",
        "thread_id": f"m2:m3-long-{slug}-{condition}",
        "case_revision": 0,
        "reason": reason,
        "goal": goal,
        "action_constraints": {
            "restricted_operation_modes": ["production_write"],
            "prefer_reversible": True,
        },
        "reported_evidence": reported,
        "observed_evidence": detail["observed"],
        "confirmed_facts": [],
        "competing_explanations": [
            {"statement": item, "strength": "low"}
            for item in detail["explanations"]
        ],
        "failed_directions": [
            {"tool_id": "first_attempt", "status": "failed", "observation": "no change"}
        ],
        "prohibited_retries": ["first_attempt:{}"],
        "open_evidence": [
            {"key": detail["open"], "available_via": "read-only adapter"}
        ],
        "permission_boundary": ["production_write"],
        "coordination_structure": coordination_structure,
        "suggested_collaboration_mode": mode,
    }


def build() -> list[dict]:
    rows = []
    for slug, description, mode, statuses, maximum_agents, useful in SCENARIOS:
        for condition in CONDITIONS:
            initial = _handoff(slug, description, mode, condition)
            updated = None
            if condition == "correction":
                updated = deepcopy(initial)
                updated["case_revision"] = 1
                updated["reported_evidence"].append(
                    {
                        "key": "late_correction",
                        "value": "等下，我刚发现之前说反了；另一个设备其实正常",
                        "epistemic_status": "reported_observation",
                    }
                )
                updated["reason"] += "；新事实到达，旧协作输出必须失效"
            rows.append(
                {
                    "episode_id": f"{slug}-{condition}",
                    "description": f"{description} / {condition}",
                    "initial_case": {
                        "case_id": initial["case_id"],
                        "description": description,
                        "handoff": initial,
                        "allowed_statuses": statuses,
                        "expected_modes": [mode],
                        "maximum_agents": maximum_agents,
                        "fixed_baseline_agents": 3,
                    },
                    "updated_handoff": updated,
                    "verified_capabilities": useful,
                    "verification_success": True,
                }
            )
    return rows


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(build(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(build())} episodes to {OUTPUT}")


if __name__ == "__main__":
    main()
