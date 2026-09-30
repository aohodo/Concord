"""Capability and externally observed reliability directory for M3 agents."""

from __future__ import annotations

from collections.abc import Iterable

from .models import AgentPerformance, AgentProfile, AvailableAgent

DEFAULT_AGENT_PROFILES = (
    AgentProfile(
        agent_id="domain_specialist",
        role="领域实施专家",
        mission="识别能力或领域边界内最可行的诊断方向",
        capabilities={"specialized_diagnosis", "boundary_analysis", "domain_reasoning"},
        perspective="从领域约束、依赖和常见失效机制审查 Case",
        base_coordination_cost=0.16,
    ),
    AgentProfile(
        agent_id="evidence_auditor",
        role="证据审计者",
        mission="审查来源可靠性、事实冲突和验证缺口",
        capabilities={"fact_verification", "source_reliability", "conflict_analysis"},
        perspective="只接受可追溯证据，主动寻找反证和缺失证据",
        base_coordination_cost=0.12,
    ),
    AgentProfile(
        agent_id="risk_reviewer",
        role="独立风险复核者",
        mission="在不受多数意见影响的条件下复核高风险方案",
        capabilities={"risk_review", "safety_review", "independent_review"},
        perspective="优先检查不可逆后果、权限边界和失败恢复路径",
        base_coordination_cost=0.14,
    ),
    AgentProfile(
        agent_id="operations_planner",
        role="实施方案规划者",
        mission="比较候选动作的风险、时间、成本、可逆性和信息价值",
        capabilities={"action_comparison", "operational_planning", "time_optimization"},
        perspective="面向可执行和可验证的下一步，不把建议冒充结果",
        base_coordination_cost=0.13,
    ),
    AgentProfile(
        agent_id="alternative_explorer",
        role="替代路径探索者",
        mission="在当前搜索方向僵化时构造有区分度的新解释和检查路径",
        capabilities={"diverse_exploration", "counter_hypothesis", "search_reset"},
        perspective="刻意避开当前锚点，以反事实和竞争解释扩大搜索",
        base_coordination_cost=0.18,
    ),
)


class AgentDirectory:
    def __init__(self, profiles: Iterable[AgentProfile] = DEFAULT_AGENT_PROFILES) -> None:
        self._profiles = {item.agent_id: item for item in profiles}

    def catalog(
        self, performance: dict[str, AgentPerformance] | None = None
    ) -> list[AvailableAgent]:
        observed = performance or {}
        return [
            AvailableAgent(
                profile=profile,
                performance=observed.get(
                    agent_id, AgentPerformance(agent_id=agent_id)
                ),
            )
            for agent_id, profile in sorted(self._profiles.items())
        ]

    def require(self, agent_id: str) -> AgentProfile:
        try:
            return self._profiles[agent_id]
        except KeyError as exc:
            raise ValueError(f"unknown collaboration agent: {agent_id}") from exc

    def select(
        self,
        required_capabilities: set[str],
        performance: dict[str, AgentPerformance] | None = None,
        *,
        exclude: set[str] | None = None,
        contextual_performance: dict[str, AgentPerformance] | None = None,
        task_type: str = "general",
    ) -> AgentProfile | None:
        excluded = exclude or set()
        candidates = [
            item
            for item in self.catalog(performance)
            if item.profile.agent_id not in excluded
            and required_capabilities <= item.profile.capabilities
        ]
        if not candidates:
            return None
        contextual = contextual_performance or {}

        def contextual_score(item: AvailableAgent) -> float:
            samples = [
                contextual.get(f"{item.profile.agent_id}|{capability}|{task_type}")
                for capability in required_capabilities
            ]
            observed = [score for score in samples if score is not None and score.samples]
            if not observed:
                return item.reliability
            reliability = sum(score.reliability for score in observed) / len(observed)
            progress = sum(score.verified_progress_total for score in observed)
            return reliability + max(-0.2, min(0.2, progress / max(5, len(observed)) * 0.05))

        selected = max(
            candidates,
            key=lambda item: (
                contextual_score(item) - item.profile.base_coordination_cost * 0.25,
                -item.profile.base_coordination_cost,
                item.profile.agent_id,
            ),
        )
        return selected.profile


__all__ = ["DEFAULT_AGENT_PROFILES", "AgentDirectory"]
