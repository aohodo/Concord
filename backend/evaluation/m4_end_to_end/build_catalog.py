"""Build 110 imperfect multi-turn Episodes from reusable M2 environments."""

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
            "expert_precise": [
                {
                    "message": (
                        f"现象：{scenario.user_report} 可直接观察到：{_visible_fragment(scenario.visible_state)}。"
                    ),
                    "purpose": "provide a concise symptom and directly observed state",
                },
                {
                    "message": f"完成标准是“{scenario.goal}”；请先验证现状，再执行可回退的最小动作。",
                    "purpose": "state a testable goal and an evidence-first action constraint",
                },
            ],
            "half_expert_hypothesis": [
                {
                    "message": (
                        f"{scenario.user_report} "
                        + (
                            f"这个我见过，肯定就是{scenario.user_hypothesis}。"
                            if scenario.user_hypothesis
                            else "这个我见过，肯定是后台链路的问题。"
                        )
                    ),
                    "purpose": "mix useful terminology with an overconfident causal claim",
                },
                {
                    "message": f"我说的是经验判断，不是已经验证的事实；最终还是要{scenario.goal}。",
                    "purpose": "expose the hypothesis while grounding the actual outcome",
                },
            ],
            "procedural_only": [
                {
                    "message": (
                        f"{scenario.user_report} 我照着别人以前发的步骤重新打开、退出再进都做了，"
                        "但我不知道每一步是在查什么。"
                    ),
                    "purpose": "report copied procedures without claiming mechanism knowledge",
                },
                {
                    "message": f"别让我再盲目重复一遍；你按结果判断下一步，目标是{scenario.goal}。",
                    "purpose": "preserve failure memory and request a result-oriented next step",
                },
            ],
            "cross_domain_transfer": [
                {
                    "message": (
                        f"{scenario.user_report} 我平时处理家里路由器都是重启就好，"
                        "所以这次是不是也直接全部重启？"
                    ),
                    "purpose": "transfer a familiar but unverified strategy into this domain",
                },
                {
                    "message": (
                        f"那个只是类比，不一定适用。现在能直接看到的是："
                        f"{_visible_fragment(scenario.visible_state)}；我要{scenario.goal}。"
                    ),
                    "purpose": "separate analogy from observation and restore the main goal",
                },
            ],
            "frustrated_repeat": [
                {
                    "message": (
                        f"{scenario.user_report} 我已经来回试很多次了，还是这样，"
                        "别再让我把刚才那些动作原样做一遍。"
                    ),
                    "purpose": "express frustration and a strong repeated-action constraint",
                },
                {
                    "message": f"先告诉我你在确认什么，然后尽快把“{scenario.goal}”做成。",
                    "purpose": "request visible progress while retaining the outcome goal",
                },
            ],
            "low_control": [
                {
                    "message": (
                        f"{scenario.user_report} 我不太敢继续点，怕把现在的数据或设置弄坏。"
                    ),
                    "purpose": "report low perceived control and a safety concern",
                },
                {
                    "message": f"你可以先做不会改数据的检查；确实安全再推进，目标是{scenario.goal}。",
                    "purpose": "allow low-risk evidence gathering before action",
                },
                {
                    "message": (
                        "如果检查确认是在可重置的模拟环境内、动作可回退，我确认继续；"
                        "如果需要真实生产权限就停下来告诉我。"
                    ),
                    "purpose": "grant conditional confirmation without bypassing a real authority boundary",
                },
            ],
            "low_patience_stream": [
                {
                    "message": "又不行了，能不能快点看？",
                    "purpose": "start with a terse high-pressure request",
                },
                {
                    "message": scenario.user_report,
                    "purpose": "add the actual symptom in a rapid follow-up",
                },
                {
                    "message": f"反正我现在只要{scenario.goal}，先给结果，说明尽量短。",
                    "purpose": "state a low-reading-budget result-first preference",
                },
            ],
            "multi_issue_dump": [
                {
                    "message": (
                        f"我把看到的都说一下：{scenario.user_report}；"
                        f"界面上还有这些东西：{_visible_fragment(scenario.visible_state)}；"
                        "昨天似乎也慢过一次，同事说过可能是网络，但我不确定这些是不是一回事。"
                    ),
                    "purpose": "dump relevant, weakly related and uncertain information together",
                },
                {
                    "message": f"你先抓主线，当前最重要的是{scenario.goal}，其他现象可以先挂着。",
                    "purpose": "prioritize one issue without discarding the rest",
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
