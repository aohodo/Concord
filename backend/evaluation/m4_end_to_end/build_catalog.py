"""Build 30 imperfect multi-turn Episodes from the reusable M2 environments."""

from __future__ import annotations

import json

from evaluation.m2_resolution import load_scenario_catalog

from .catalog import DEFAULT_CATALOG


def _visible_fragment(state: dict) -> str:
    items = list(state.items())[:2]
    return "；".join(f"{key}看起来是{value}" for key, value in items)


def build_catalog() -> list[dict]:
    episodes: list[dict] = []
    for scenario in load_scenario_catalog():
        conditions = {
            "fragmented_novice": [
                {
                    "message": "这个东西又不行了，我也不知道应该从哪里说。",
                    "purpose": "start with an underspecified surface symptom",
                },
                {
                    "message": scenario.user_report,
                    "purpose": "add the observed symptom in ordinary language",
                },
                {
                    "message": (
                        f"我主要就是想{scenario.goal}。"
                        + (f"有人说{scenario.user_hypothesis}，但我也不确定。" if scenario.user_hypothesis else "")
                    ),
                    "purpose": "state the outcome while keeping the hypothesis provisional",
                },
            ],
            "deadline_result_first": [
                {
                    "message": f"{scenario.user_report}，现在比较急，先别给我讲太长。",
                    "purpose": "report impact and a low reading budget",
                },
                {
                    "message": f"目标就是{scenario.goal}，先恢复，原因可以后面再看。",
                    "purpose": "set an explicit restore-first goal",
                },
            ],
            "self_correction": [
                {
                    "message": (
                        scenario.user_report
                        + (f" 我觉得肯定是{scenario.user_hypothesis}。" if scenario.user_hypothesis else "")
                    ),
                    "purpose": "mix observation with an overconfident hypothesis",
                },
                {
                    "message": f"刚才原因只是我猜的。能确定看到的是：{_visible_fragment(scenario.visible_state)}。",
                    "purpose": "retract certainty and add direct observations",
                },
                {
                    "message": f"最后以“{scenario.goal}”作为完成标准。",
                    "purpose": "ground the shared goal and success direction",
                },
            ],
        }
        for condition, turns in conditions.items():
            episode_id = f"{scenario.scenario_id}__{condition}"
            # M2 being allowed to collaborate is not evidence that collaboration is
            # required.  Only an explicit environment boundary makes M3 mandatory.
            require_m3 = "handoff_required" in scenario.tags
            allowed = {item.value for item in scenario.expected.allowed_statuses}
            if require_m3:
                allowed.update({"human_required", "evidence_required", "ready_for_m2"})
            episodes.append(
                {
                    "episode_id": episode_id,
                    "case_id": f"m4-{episode_id}",
                    "conv_id": f"m4-conv-{episode_id}",
                    "user_id": f"m4-user-{episode_id}",
                    "domain": scenario.domain,
                    "title": scenario.title,
                    "user_condition": condition,
                    "turns": turns,
                    "visible_state": scenario.visible_state,
                    "hidden_state": scenario.hidden_state,
                    "action_rules": [
                        item.model_dump(mode="json") for item in scenario.action_rules
                    ],
                    "observation_rules": [
                        item.model_dump(mode="json") for item in scenario.observation_rules
                    ],
                    "faults": [item.model_dump(mode="json") for item in scenario.faults],
                    "permissions": scenario.permissions,
                    "goal": scenario.goal,
                    "tags": sorted(scenario.tags | {condition, "m4_end_to_end"}),
                    "expected": {
                        "allowed_case_statuses": sorted(allowed),
                        "require_m2": True,
                        "require_m3": require_m3,
                        "success_criteria": scenario.success_criteria,
                    },
                }
            )
    episodes.append(_vertical_collaboration_episode())
    return episodes


def _vertical_collaboration_episode() -> dict:
    """Exercise the real M1→M2→M3→M2 loop after repeated local failure."""

    episode_id = "specialist_recovery_after_tool_failures__self_correction"
    return {
        "episode_id": episode_id,
        "case_id": f"m4-{episode_id}",
        "conv_id": f"m4-conv-{episode_id}",
        "user_id": f"m4-user-{episode_id}",
        "domain": "platform_operations",
        "title": "本地恢复工具连续失败后寻求互补诊断",
        "user_condition": "self_correction",
        "turns": [
            {
                "message": (
                    "连接器现在是 degraded，依赖健康检查显示 reachable，配置近期没有改过；"
                    "恢复动作连续两次超时。我刚才说肯定是配置坏了，但那只是猜测。"
                    "目标是恢复到 healthy 并实际验证。"
                ),
                "purpose": "provide observations, failure memory, goal, and a retracted hypothesis",
                "wait_for_async_completion": True,
            },
            {
                "message": "能确认的只有状态是 degraded；我已经连续尝试过恢复但都超时了。",
                "purpose": "provide direct observation and failure memory",
            },
            {
                "message": "目标是让连接器恢复 healthy；如果当前方向不再有进展，请换一个独立方向检查。",
                "purpose": "set the goal and permit complementary exploration",
            },
        ],
        "visible_state": {
            "connector": {"status": "degraded", "last_attempt": "timeout"},
            "dependency": {"reachable": True},
        },
        "hidden_state": {"root_cause": "transient_control_plane_failure"},
        "action_rules": [
            {
                "action_id": "recover_connector",
                "description": "执行可重复且可验证的模拟连接器恢复",
                "preconditions": [{"path": "connector.status", "value": "degraded"}],
                "effects": [
                    {"path": "connector.status", "value": "healthy"},
                    {"path": "connector.last_attempt", "value": "succeeded"},
                ],
                "success_observation": "连接器恢复动作已提交",
                "verification_targets": ["connector.status"],
            }
        ],
        "observation_rules": [],
        "faults": [
            {
                "tool_id": "simulation_execute_action",
                "kind": "timeout",
                "phase": "before",
                "message": "transient control-plane timeout",
                "retryable": True,
            }
            for _ in range(1)
        ],
        "permissions": ["simulation:act"],
        "goal": "恢复连接器并独立验证状态",
        "tags": [
            "m4_end_to_end",
            "vertical_m3_resume",
            "failure_feedback",
            "self_correction",
        ],
        "expected": {
            "allowed_case_statuses": ["resolved"],
            "require_m2": True,
            "require_m3": True,
            "success_criteria": ["connector.status=healthy"],
        },
    }


def main() -> None:
    payload = build_catalog()
    DEFAULT_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CATALOG.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(payload)} episodes to {DEFAULT_CATALOG}")


if __name__ == "__main__":
    main()
