"""Concord human-centered adaptive collaboration runtime entrypoint."""

import asyncio
import json
import logging
import os
import pathlib
import sys
import time
import uuid
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

_ROOT = str(pathlib.Path(__file__).parent.parent.resolve())
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from api.routes.continual_improvement import build_continual_improvement_router
from api.schemas import (
    BatchDocInput,
    CaseBudgetInput,
    CaseControlInput,
    CasePreferencesInput,
    ChatRequest,
    ChatResponse,
    CollaborationCancelInput,
    CollaborationFeedbackInput,
    CollaborationRunInput,
    RecentToolTracesResponse,
    ResolutionCancelInput,
    ResolutionEventInput,
    ResolutionRunInput,
    RuntimeFaultInput,
    RuntimeToolInput,
    SimulationCaseInput,
    ToolTraceResponse,
)
from core.case_orchestration import (
    CaseJob,
    CaseJobKind,
    CasePhase,
    CaseRunSnapshot,
    DurableCaseJobWorker,
    DurableCaseRunRegistry,
    StaleCaseResultError,
)
from core.human_collaboration import (
    EvidenceArtifact,
    derive_human_collaboration_state,
    extract_textual_artifact,
    layer_response,
    render_artifacts_for_formulation,
    select_visible_progress,
)
from core.llm_client import LLMConfig, create_llm_client

load_dotenv(pathlib.Path(_ROOT) / ".env")

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BANNER = r"""
   +----------------------+
   |   Concord Runtime    |
   |   Multi-Agent Lab    |
   +----------------------+
"""

# ── 全局组件（lifespan 中初始化）─────────────────────────────────────────────
_memory = None
_tool_runtime = None
_knowledge_base = None
_formulation_service = None
_resolution_service = None
_collaboration_service = None
_case_event_store = None
_agent_performance_store = None
_case_run_registry = None
_case_job_worker = None
_continual_improvement = None
_scenario_runtime = None
_tool_idempotency_store = None
_fault_plan_store = None
_llm_client = None
_llm_model = ""
_resolution_tasks: dict[str, asyncio.Task] = {}
_collaboration_tasks: dict[str, asyncio.Task] = {}
_active_chat_tasks: dict[str, tuple[int, asyncio.Task]] = {}
_chat_progress_tasks: dict[str, tuple[int, asyncio.Task]] = {}
_pending_chat_inputs: dict[str, dict[int, Any]] = {}
_m3_thread_by_m2_thread: dict[str, str] = {}
_m3_revision_by_m2_thread: dict[str, int] = {}
_graph_checkpointer_cm = None


async def _observe_case_for_improvement(
    snapshot: CaseRunSnapshot,
) -> tuple[str, int, int]:
    """Keep learning telemetry off the user-facing critical path on failure."""

    if _continual_improvement is None:
        return "unavailable", 0, 0
    try:
        outcome, signals, proposals = await _continual_improvement.observe_case(snapshot)
        return outcome.verdict.value, len(signals), len(proposals)
    except Exception:
        logger.exception("M7 observation failed case_id=%s", snapshot.case_id)
        return "observation_failed", 0, 0


def _llm_cfg() -> LLMConfig:
    return LLMConfig.from_env()


def _register_active_chat(case_id: str, generation: int, request: Any) -> None:
    """Supersede older synchronous work for the same heavily interactive Case."""

    task = asyncio.current_task()
    if task is None:
        return
    _pending_chat_inputs.setdefault(case_id, {})[generation] = request.model_copy(deep=True)
    previous = _active_chat_tasks.get(case_id)
    _active_chat_tasks[case_id] = (generation, task)
    if previous is not None and previous[1] is not task and not previous[1].done():
        previous[1].cancel()

    def cleanup(completed: asyncio.Task) -> None:
        active = _active_chat_tasks.get(case_id)
        if active is not None and active == (generation, completed):
            _active_chat_tasks.pop(case_id, None)
        progress = _chat_progress_tasks.get(case_id)
        if progress is not None and progress[0] == generation:
            progress[1].cancel()
            _chat_progress_tasks.pop(case_id, None)

    task.add_done_callback(cleanup)


def _start_chat_progress_heartbeat(
    *,
    case_id: str,
    generation: int,
    user_id: str,
    tenant_id: str,
    interval_seconds: float,
    cadence: str,
) -> None:
    if cadence == "blockers_only" or _case_run_registry is None:
        return

    async def publish() -> None:
        while True:
            await asyncio.sleep(max(1.0, interval_seconds))
            try:
                await _case_run_registry.record_runtime_event(
                    case_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    event_type="case_progress_heartbeat",
                    status="working",
                    summary="仍在处理；新的补充信息可以随时继续发送。",
                    expected_request_generation=generation,
                )
            except (KeyError, StaleCaseResultError):
                return

    task = asyncio.create_task(publish(), name=f"case-progress:{case_id}:{generation}")
    prior = _chat_progress_tasks.get(case_id)
    if prior is not None and not prior[1].done():
        prior[1].cancel()
    _chat_progress_tasks[case_id] = (generation, task)


def _coalesce_pending_chat_inputs(case_id: str, generation: int, request: Any) -> Any:
    """Carry every not-yet-formulated user input into the newest execution."""

    pending = _pending_chat_inputs.get(case_id, {})
    ordered = [pending[key] for key in sorted(pending) if key <= generation]
    if len(ordered) <= 1:
        return request
    combined_message = "\n\n".join(
        f"[用户连续补充 {index}]\n{item.message}"
        for index, item in enumerate(ordered, start=1)
        if item.message.strip()
    )
    return request.model_copy(
        update={
            "message": combined_message,
            "attachments": [attachment for item in ordered for attachment in item.attachments],
            "images": [image for item in ordered for image in item.images],
        },
        deep=True,
    )


def _complete_pending_chat_inputs(case_id: str, generation: int) -> None:
    pending = _pending_chat_inputs.get(case_id)
    if pending is None:
        return
    for key in [key for key in pending if key <= generation]:
        pending.pop(key, None)
    if not pending:
        _pending_chat_inputs.pop(case_id, None)


def _interrupt_active_chat(case_id: str) -> bool:
    active = _active_chat_tasks.get(case_id)
    if active is None or active[1].done():
        return False
    active[1].cancel()
    return True


async def _schedule_collaboration_job(
    *,
    case_snapshot: CaseRunSnapshot,
    handoff: Any,
    resolution_context: dict[str, Any],
    permissions: list[str],
    topology: str,
    failure_memory_enabled: bool,
    previous_tool_count: int,
    expected_request_generation: int | None = None,
) -> CaseJob:
    """Persist M3→M2 work before returning control to the caller."""

    if _case_run_registry is None or _case_job_worker is None:
        raise RuntimeError("durable Case job runtime is unavailable")
    existing = await _case_run_registry.find_active_job(
        case_snapshot.case_id,
        CaseJobKind.COLLABORATE_THEN_RESUME,
        case_snapshot.case_revision,
    )
    if existing is not None:
        return existing
    prior_jobs = await _case_run_registry.list_jobs(case_snapshot.case_id)
    loop_index = 1 + sum(item.kind is CaseJobKind.COLLABORATE_THEN_RESUME for item in prior_jobs)
    collaboration_id = str(uuid.uuid4())
    thread_id = f"m3:{case_snapshot.m2_thread_id}:{collaboration_id}"
    handoff_payload = handoff.model_dump(mode="json")
    handoff_payload["case_revision"] = case_snapshot.case_revision
    job = CaseJob(
        case_id=case_snapshot.case_id,
        user_id=case_snapshot.user_id,
        tenant_id=case_snapshot.tenant_id,
        kind=CaseJobKind.COLLABORATE_THEN_RESUME,
        case_revision=case_snapshot.case_revision,
        idempotency_key=(
            f"{case_snapshot.case_id}:m3-resume:{case_snapshot.case_revision}:{loop_index}"
        ),
        payload={
            "collaboration_id": collaboration_id,
            "thread_id": thread_id,
            "m2_thread_id": case_snapshot.m2_thread_id,
            "handoff": handoff_payload,
            "resolution_context": resolution_context,
            "permissions": permissions,
            "topology": topology,
            "failure_memory_enabled": failure_memory_enabled,
            "previous_tool_count": previous_tool_count,
            "loop_index": loop_index,
        },
    )
    stored = await _case_run_registry.schedule_collaboration_job(
        job,
        collaboration_id=collaboration_id,
        thread_id=thread_id,
        expected_request_generation=expected_request_generation,
    )
    _case_job_worker.wake()
    return stored


async def _execute_case_job(job: CaseJob) -> dict[str, Any]:
    """Execute one restart-safe M3→M2 loop from serialized inputs."""

    if _case_run_registry is None or _collaboration_service is None or _resolution_service is None:
        raise RuntimeError("Case services are unavailable")
    current = await _case_run_registry.get(
        job.case_id, user_id=job.user_id, tenant_id=job.tenant_id
    )
    if current is None or current.case_revision != job.case_revision:
        return {"status": "stale", "case_revision": job.case_revision}
    if current.phase in {CasePhase.PAUSED, CasePhase.CANCELLED}:
        return {"status": current.phase.value, "case_revision": job.case_revision}
    exceeded = await _case_run_registry.budget_exceeded(
        job.case_id, user_id=job.user_id, tenant_id=job.tenant_id
    )
    if exceeded:
        await _case_run_registry.record_runtime_event(
            job.case_id,
            user_id=job.user_id,
            tenant_id=job.tenant_id,
            event_type="case_budget_reached",
            status="paused",
            summary="Case paused before collaboration because its resource envelope was reached",
            data={"dimensions": exceeded},
        )
        await _case_run_registry.transition(
            job.case_id,
            user_id=job.user_id,
            tenant_id=job.tenant_id,
            action="pause",
            reason="Resource envelope reached; reliable Case state was preserved.",
        )
        return {"status": "budget_reached", "dimensions": exceeded}

    from core.multi_agent_collaboration import CollaborationTopology

    payload = job.payload
    result = await _collaboration_service.collaborate(
        handoff=payload["handoff"],
        thread_id=payload["thread_id"],
        collaboration_id=payload["collaboration_id"],
        topology=CollaborationTopology(payload["topology"]),
    )
    current = await _case_run_registry.get(
        job.case_id, user_id=job.user_id, tenant_id=job.tenant_id
    )
    if current is not None and current.phase in {CasePhase.PAUSED, CasePhase.CANCELLED}:
        return {"status": current.phase.value, "collaboration_id": result.collaboration_id}
    if current is None or current.case_revision != result.source_case_revision:
        await _case_run_registry.record_stale_collaboration(
            case_id=job.case_id,
            user_id=job.user_id,
            tenant_id=job.tenant_id,
            collaboration_id=result.collaboration_id,
            source_revision=result.source_case_revision,
            current_revision=current.case_revision if current else -1,
        )
        return {"status": "stale", "collaboration_id": result.collaboration_id}
    await _case_run_registry.record_collaboration_completed(
        result, user_id=job.user_id, tenant_id=job.tenant_id
    )
    await _case_run_registry.record_control_activation(
        job.case_id,
        user_id=job.user_id,
        tenant_id=job.tenant_id,
        mechanism="adaptive_m3",
        activated=bool(result.agents_recruited),
        inputs={
            "topology": result.topology.value,
            "trigger": payload["handoff"].get("reason", ""),
        },
        behavior_changes=[f"recruited:{agent_id}" for agent_id in result.agents_recruited],
        outcome={
            "status": result.status.value,
            "model_calls": result.metrics.model_calls,
            "rounds": result.rounds,
        },
    )
    resumed = None
    if result.resume_packet is not None and result.status.value in {
        "ready_for_m2",
        "evidence_required",
    }:
        latest = await _case_run_registry.get(
            job.case_id, user_id=job.user_id, tenant_id=job.tenant_id
        )
        if latest is None or latest.case_revision != job.case_revision:
            return {"status": "stale_before_resume"}
        if latest.phase in {CasePhase.PAUSED, CasePhase.CANCELLED}:
            return {"status": latest.phase.value}
        exceeded = await _case_run_registry.budget_exceeded(
            job.case_id, user_id=job.user_id, tenant_id=job.tenant_id
        )
        if exceeded:
            await _case_run_registry.transition(
                job.case_id,
                user_id=job.user_id,
                tenant_id=job.tenant_id,
                action="pause",
                reason="Resource envelope reached before M2 resumed.",
            )
            return {"status": "budget_reached", "dimensions": exceeded}
        resumed = await _resolution_service.resolve(
            resolution_context=payload["resolution_context"],
            thread_id=payload["m2_thread_id"],
            permissions=payload.get("permissions", []),
            collaboration_advice=result.resume_packet.model_dump(mode="json"),
            failure_memory_enabled=bool(payload.get("failure_memory_enabled", True)),
            episode_decision_budget=4,
            actor_id=job.user_id,
            tenant_id=job.tenant_id,
        )
        latest = await _case_run_registry.get(
            job.case_id, user_id=job.user_id, tenant_id=job.tenant_id
        )
        if latest is None or latest.case_revision != job.case_revision:
            return {"status": "stale_after_resume"}
        if latest.phase in {CasePhase.PAUSED, CasePhase.CANCELLED}:
            return {"status": latest.phase.value}
        resumed_snapshot = await _case_run_registry.record_resolution(
            resumed,
            user_id=job.user_id,
            tenant_id=job.tenant_id,
            resumed_from_collaboration=True,
        )
        await _observe_case_for_improvement(resumed_snapshot)
        await _collaboration_service.record_resolution_outcome(
            collaboration=result,
            resolution_result=resumed,
            previous_tool_count=int(payload.get("previous_tool_count", 0)),
        )
        failure_memory_activations = sum(
            item.get("error_code") == "REPEATED_FAILED_ACTION_BLOCKED"
            for item in resumed.tool_history
        )
        await _case_run_registry.record_control_activation(
            job.case_id,
            user_id=job.user_id,
            tenant_id=job.tenant_id,
            mechanism="failure_memory",
            activated=bool(failure_memory_activations),
            inputs={"enabled": bool(payload.get("failure_memory_enabled", True))},
            behavior_changes=(
                [f"blocked_repeated_failed_calls:{failure_memory_activations}"]
                if failure_memory_activations
                else []
            ),
            outcome={"resolution_status": resumed.status.value},
        )
        if (
            resumed.status.value == "collaboration_required"
            and resumed.collaboration_handoff is not None
        ):
            latest = await _case_run_registry.get(
                job.case_id, user_id=job.user_id, tenant_id=job.tenant_id
            )
            assert latest is not None
            await _schedule_collaboration_job(
                case_snapshot=latest,
                handoff=resumed.collaboration_handoff,
                resolution_context=payload["resolution_context"],
                permissions=payload.get("permissions", []),
                topology=payload["topology"],
                failure_memory_enabled=bool(payload.get("failure_memory_enabled", True)),
                previous_tool_count=len(resumed.tool_history),
            )
    return {
        "status": resumed.status.value if resumed is not None else result.status.value,
        "collaboration_id": result.collaboration_id,
        "agents": result.agents_recruited,
        "loop_index": payload.get("loop_index", 1),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _memory, _tool_runtime
    global _knowledge_base
    global _formulation_service, _resolution_service, _collaboration_service
    global _graph_checkpointer_cm, _case_event_store, _agent_performance_store
    global _case_run_registry, _case_job_worker, _continual_improvement
    global _scenario_runtime, _tool_idempotency_store, _fault_plan_store
    global _llm_client, _llm_model

    print(BANNER, flush=True)

    from infrastructure.agent_performance import AgentPerformanceStore
    from infrastructure.case_events import CaseEventStore
    from infrastructure.knowledge.knowledge_base import KnowledgeBase
    from infrastructure.memory.conversation_memory import MemoryManager
    from infrastructure.observability.tracing import build_trace_sink_from_env
    from infrastructure.retrieval import (
        FilesystemRetrievalAdapter,
        build_filesystem_tools,
        build_knowledge_tool,
    )
    from infrastructure.simulation import (
        AuditLog,
        DurableScenarioRuntime,
        SqliteFaultPlan,
        SqliteIdempotencyStore,
        build_simulation_tools,
    )
    from infrastructure.tool_runtime import AdaptiveToolRuntime

    cfg = _llm_cfg()
    llm_client = create_llm_client(cfg)
    _llm_client = llm_client
    _llm_model = cfg.model
    logger.info(
        "模型 provider=%s model=%s base_url=%s",
        cfg.provider,
        cfg.model,
        cfg.base_url or "(官方)",
    )

    # 唯一正式工具运行时：只读真实数据 + 可重置模拟环境 + 可替换 Adapter。
    trace_sink = build_trace_sink_from_env()
    runtime_db = pathlib.Path(
        os.getenv(
            "CONCORD_RUNTIME_DB",
            str(pathlib.Path(_ROOT) / "data" / "concord_runtime.sqlite3"),
        )
    ).resolve()
    scenario_runtime = DurableScenarioRuntime(runtime_db)
    await scenario_runtime.setup()
    _scenario_runtime = scenario_runtime
    _tool_idempotency_store = SqliteIdempotencyStore(runtime_db)
    await _tool_idempotency_store.setup()
    _fault_plan_store = SqliteFaultPlan(runtime_db)
    await _fault_plan_store.setup()
    _tool_runtime = AdaptiveToolRuntime(
        scenarios=scenario_runtime,
        audit_log=AuditLog(
            os.getenv(
                "CONCORD_TOOL_AUDIT_LOG",
                str(pathlib.Path(_ROOT) / "data" / "traces" / "tool_audit.jsonl"),
            )
        ),
        idempotency=_tool_idempotency_store,
        fault_plan=_fault_plan_store,
        trace_sink=trace_sink,
    )
    filesystem = FilesystemRetrievalAdapter(pathlib.Path(_ROOT).parent)
    for spec in build_filesystem_tools(filesystem):
        _tool_runtime.registry.register(spec)
    for spec in build_simulation_tools(scenario_runtime):
        _tool_runtime.registry.register(spec)

    retrieval_index_path = os.getenv("RETRIEVAL_INDEX_PATH", "./data/retrieval")
    _knowledge_base = KnowledgeBase(index_path=retrieval_index_path)
    _tool_runtime.registry.register(build_knowledge_tool(_knowledge_base))
    logger.info(
        "自适应证据—行动运行时已加载 %d 个工具；LangSmith=%s；知识片段=%d",
        len(_tool_runtime.registry.list()),
        "enabled" if trace_sink.enabled else "disabled",
        await _knowledge_base.doc_count_async(),
    )

    # M1：LangGraph 负责跨轮问题表征、用户状态和人类行为策略。
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from core.problem_formulation.service import ProblemFormulationService

    graph_db = pathlib.Path(
        os.getenv("CONCORD_GRAPH_DB", str(pathlib.Path(_ROOT) / "data" / "concord_graph.sqlite3"))
    ).resolve()
    graph_db.parent.mkdir(parents=True, exist_ok=True)
    _graph_checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(graph_db))
    graph_checkpointer = await _graph_checkpointer_cm.__aenter__()
    await graph_checkpointer.setup()
    _formulation_service = ProblemFormulationService(
        checkpointer=graph_checkpointer,
        client=llm_client,
        model=cfg.model,
        trace_path=os.getenv(
            "CONCORD_FORMULATION_TRACE",
            str(pathlib.Path(_ROOT) / "data" / "traces" / "problem_formulation.jsonl"),
        ),
    )
    from core.adaptive_resolution import AdaptiveResolutionService
    from core.multi_agent_collaboration import AdaptiveCollaborationService

    event_db = pathlib.Path(
        os.getenv(
            "CONCORD_CASE_EVENT_DB",
            str(pathlib.Path(_ROOT) / "data" / "case_events.sqlite3"),
        )
    ).resolve()
    _case_event_store = CaseEventStore(event_db)
    await _case_event_store.setup()

    performance_db = pathlib.Path(
        os.getenv(
            "CONCORD_AGENT_PERFORMANCE_DB",
            str(pathlib.Path(_ROOT) / "data" / "agent_performance.sqlite3"),
        )
    ).resolve()
    _agent_performance_store = AgentPerformanceStore(performance_db)
    await _agent_performance_store.setup()
    _case_run_registry = DurableCaseRunRegistry(runtime_db)
    await _case_run_registry.setup()
    from core.continual_improvement import (
        ContinualImprovementRepository,
        ContinualImprovementService,
    )

    improvement_repository = ContinualImprovementRepository(runtime_db)
    await improvement_repository.setup()
    _continual_improvement = ContinualImprovementService(improvement_repository)

    _collaboration_service = AdaptiveCollaborationService(
        checkpointer=graph_checkpointer,
        performance_store=_agent_performance_store,
        client=llm_client,
        model=cfg.model,
    )

    _resolution_service = AdaptiveResolutionService(
        checkpointer=graph_checkpointer,
        tool_runtime=_tool_runtime,
        client=llm_client,
        model=cfg.model,
        event_store=_case_event_store,
    )
    _case_job_worker = DurableCaseJobWorker(
        _case_run_registry,
        _execute_case_job,
        poll_seconds=float(os.getenv("CONCORD_JOB_POLL_SECONDS", "0.5")),
        lease_seconds=float(os.getenv("CONCORD_JOB_LEASE_SECONDS", "180")),
    )
    await _case_job_worker.start()

    # Redis 保存短期工作记忆；SQLite FTS 只保存非权威历史召回候选。
    _memory = MemoryManager(
        redis_url=os.getenv(
            "REDIS_URL", "redis://:concord-local-only@localhost:6379/0"
        ),
        recall_index_path=retrieval_index_path,
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        model=cfg.model,
        provider=cfg.provider,
        reasoning_effort=cfg.reasoning_effort,
        client=llm_client,
        require_redis=os.getenv("CONCORD_REQUIRE_REDIS", "false").lower() == "true",
    )
    await _memory.setup()

    logger.info("Concord 已就绪")
    yield

    if _case_job_worker is not None:
        await _case_job_worker.stop()
    background_tasks = [
        *list(_resolution_tasks.values()),
        *list(_collaboration_tasks.values()),
    ]
    for task in background_tasks:
        if not task.done():
            task.cancel()
    if background_tasks:
        await asyncio.gather(*background_tasks, return_exceptions=True)
    if _memory is not None:
        await _memory.close()
    if _case_run_registry is not None:
        await _case_run_registry.close()
    if _continual_improvement is not None:
        await _continual_improvement.repository.close()
    if _tool_idempotency_store is not None:
        await _tool_idempotency_store.close()
    if _fault_plan_store is not None:
        await _fault_plan_store.close()
    if _scenario_runtime is not None:
        await _scenario_runtime.close()
    if _graph_checkpointer_cm is not None:
        await _graph_checkpointer_cm.__aexit__(None, None, None)
    await llm_client.close()
    logger.info("Concord 已关闭")


# ── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Concord Adaptive Collaboration Runtime",
    version="M8-A",
    lifespan=lifespan,
    docs_url="/docs",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        item.strip()
        for item in os.getenv(
            "CONCORD_CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        ).split(",")
        if item.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(
    build_continual_improvement_router(lambda: _continual_improvement)
)


# ── 路由 ──────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    if _formulation_service is None:
        raise HTTPException(503, "服务未就绪")
    return {
        "status": "ok",
        "problem_formulation": {
            "status": "ready",
            "runtime": "langgraph",
            "policy": "case_conditioned_human_behavior",
        },
        "adaptive_resolution": {
            "status": "ready" if _resolution_service is not None else "unavailable",
            "runtime": "langgraph",
            "control": "event_triggered_resource_rational_closed_loop",
        },
        "adaptive_collaboration": {
            "status": "ready" if _collaboration_service is not None else "unavailable",
            "runtime": "langgraph",
            "control": "marginal_gain_over_coordination_cost",
        },
        "case_runtime": {
            "status": "ready" if _case_run_registry is not None else "unavailable",
            "storage": (
                _case_run_registry.storage_backend
                if _case_run_registry is not None
                else "unavailable"
            ),
        },
        "continual_improvement": {
            "status": "ready" if _continual_improvement is not None else "unavailable",
            "control": "verified_experience_then_isolated_evaluation",
            "production_self_modification": False,
        },
        "working_memory": {
            "status": "ready" if _memory is not None else "unavailable",
            "backend": (_memory.working_memory_backend if _memory is not None else "unavailable"),
        },
        "recall_index": {
            "status": "ready" if _memory is not None else "unavailable",
            "backend": (
                _memory.recall_index_backend if _memory is not None else "unavailable"
            ),
            "authority": "unverified_retrieval_context",
        },
        "durable_truth": {
            "status": "ready" if _case_run_registry is not None else "unavailable",
            "backend": "sqlite",
            "authority": "case_events_checkpoints_verified_outcomes",
        },
        "tool_runtime": {
            "status": "ready" if _tool_runtime is not None else "unavailable",
            "tools": len(_tool_runtime.registry.list()) if _tool_runtime is not None else 0,
        },
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    try:
        return await _run_chat(req)
    except asyncio.CancelledError as exc:
        raise HTTPException(
            409,
            "该轮处理已被更新的用户输入、暂停或取消操作替代",
        ) from exc


async def _run_chat(req: ChatRequest):
    """
    主对话接口。完整流程：
      记忆读取 → Shared Problem Formulation → 控制先验驱动的行为规划
      → 澄清或独立输出 CASE_READY ResolutionContext → 记忆写入

    """
    if _memory is None or _formulation_service is None:
        raise HTTPException(503, "服务未就绪")

    from core.problem_formulation.models import ExperimentVariant
    from infrastructure.memory.conversation_memory import MsgRole

    try:
        experiment_variant = ExperimentVariant(req.evaluation_variant)
    except ValueError as exc:
        raise HTTPException(422, f"未知 evaluation_variant: {req.evaluation_variant}") from exc
    if (
        experiment_variant is not ExperimentVariant.FULL
        and os.getenv("CONCORD_ENABLE_EVALUATION_VARIANTS", "false").lower() != "true"
    ):
        raise HTTPException(403, "评测消融变体未启用")

    request_started = time.monotonic()
    stage_latency_ms: dict[str, float] = {}
    conv_id = req.conv_id or str(uuid.uuid4())

    request_generation: int | None = None
    if req.case_id and _case_run_registry is not None:
        try:
            accepted = await _case_run_registry.record_turn_received(
                case_id=req.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                conv_id=conv_id,
            )
            request_generation = accepted.request_generation
            _register_active_chat(req.case_id, request_generation, req)
            _start_chat_progress_heartbeat(
                case_id=req.case_id,
                generation=request_generation,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                interval_seconds=(
                    accepted.human_collaboration.budget.max_silent_wait_seconds
                ),
                cadence=accepted.human_collaboration.progress_cadence.value,
            )
            req = _coalesce_pending_chat_inputs(req.case_id, request_generation, req)
        except (PermissionError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc

    # 1. 读取记忆上下文
    stage_started = time.monotonic()
    mem_ctx = await _memory.get_context(req.user_id, conv_id, query=req.message)
    stage_latency_ms["memory_read"] = round(
        (time.monotonic() - stage_started) * 1000, 1
    )

    # 2. Recent conversation history is context, not durable Case truth.
    history = (
        [{"role": m.role.value, "content": m.content} for m in mem_ctx.recent_messages[-5:]]
        if mem_ctx.recent_messages
        else None
    )

    domain_prior = str(req.evaluation_context.get("domain") or "other")
    formulation_message = req.message
    image_evidence = None
    evidence_artifacts: list[EvidenceArtifact] = []
    textual_attachments = [
        item.model_dump(mode="json") for item in req.attachments if item.kind != "image"
    ]
    try:
        evidence_artifacts.extend(extract_textual_artifact(item) for item in textual_attachments)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    rendered_artifacts = render_artifacts_for_formulation(evidence_artifacts)
    if rendered_artifacts:
        formulation_message += "\n\n" + rendered_artifacts

    image_urls = [item.url for item in req.images]
    image_urls.extend(item.url for item in req.attachments if item.kind == "image" and item.url)
    if image_urls:
        from core.problem_formulation.vision import (
            extract_image_evidence,
            render_image_evidence,
        )

        if _llm_client is None:
            raise HTTPException(503, "多模态模型未初始化")
        for image_url in image_urls:
            if not image_url.startswith(("data:image/", "https://")):
                raise HTTPException(422, "图片只接受 data:image 或 HTTPS URL")
            if len(image_url) > 14_000_000:
                raise HTTPException(413, "单张图片过大")
        stage_started = time.monotonic()
        try:
            image_evidence = await extract_image_evidence(
                client=_llm_client,
                model=_llm_model,
                user_message=req.message,
                image_urls=image_urls,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        stage_latency_ms["vision_extraction"] = round(
            (time.monotonic() - stage_started) * 1000, 1
        )
        formulation_message = req.message + "\n\n" + render_image_evidence(image_evidence)
        if rendered_artifacts:
            formulation_message += "\n\n" + rendered_artifacts
        evidence_artifacts.append(
            EvidenceArtifact(
                kind="image",
                name="user-images",
                media_type="image/*",
                source="user",
                extraction_method="model_vision_extraction",
                observations=[str(item) for item in image_evidence.get("observations", [])],
                visible_text=[str(item) for item in image_evidence.get("visible_text", [])],
                uncertainties=[str(item) for item in image_evidence.get("uncertainties", [])],
                metadata={"count": len(image_urls)},
                epistemic_status="unverified_visual_observation",
            )
        )

    stage_started = time.monotonic()
    try:
        formulation = await _formulation_service.process(
            message=formulation_message,
            user_id=req.user_id,
            conv_id=conv_id,
            domain_prior=domain_prior,
            solver_payload={
                "memory_context": "\n\n".join(
                    item
                    for item in [
                        mem_ctx.to_prompt_text(),
                        (
                            "[Synthetic environment affordances]\n"
                            + str(
                                {
                                    "available_actions": req.evaluation_context.get(
                                        "available_actions", []
                                    )
                                }
                            )
                            + "\nWhen resolving, include the selected action as "
                            "<ACTION:action_id>. Do not invent actions outside this catalog."
                            if req.evaluation_context
                            and os.getenv(
                                "CONCORD_ENABLE_EVALUATION_VARIANTS", "false"
                            ).lower()
                            == "true"
                            else ""
                        ),
                    ]
                    if item
                ),
                "history": history,
                # Environment observability is not the same thing as evidence
                # this Case needs. Supplying visible-state keys here leaked the
                # evaluator's answer structure into M1.
                "evidence_catalog": [],
                "image_evidence": image_evidence or {},
                "requested_case_id": req.case_id,
            },
            experiment_variant=experiment_variant,
        )
    except Exception as exc:
        logger.exception("M1 processing failed case_id=%s", req.case_id or "unassigned")
        if req.case_id and request_generation is not None:
            try:
                await _case_run_registry.record_error(
                    case_id=req.case_id,
                    user_id=req.user_id,
                    tenant_id=req.tenant_id,
                    phase=CasePhase.FORMULATION,
                    error_code="M1_RUNTIME_FAILURE",
                    expected_request_generation=request_generation,
                )
            except (KeyError, StaleCaseResultError):
                pass
        raise HTTPException(
            502,
            "问题表征阶段暂时失败，已保留当前 Case，可直接重试或补充信息",
        ) from exc
    stage_latency_ms["m1_formulation"] = round(
        (time.monotonic() - stage_started) * 1000, 1
    )
    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        case_snapshot = await _case_run_registry.record_formulation(
            formulation=formulation,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            conv_id=conv_id,
            expected_request_generation=request_generation,
        )
    except StaleCaseResultError as exc:
        raise HTTPException(409, str(exc)) from exc
    if request_generation is None:
        request_generation = case_snapshot.request_generation
    else:
        _complete_pending_chat_inputs(case_snapshot.case_id, request_generation)
    if evidence_artifacts:
        try:
            case_snapshot = await _case_run_registry.record_evidence_artifacts(
                case_snapshot.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                artifacts=evidence_artifacts,
                expected_request_generation=request_generation,
            )
        except StaleCaseResultError as exc:
            raise HTTPException(409, str(exc)) from exc
    effective_resolution_context = (
        case_snapshot.m1.get("resolution_context") or formulation.resolution_context
    )
    resolution_permissions = (
        list(req.evaluation_context.get("permissions", []))
        if req.evaluation_context
        and os.getenv("CONCORD_ENABLE_EVALUATION_VARIANTS", "false").lower() == "true"
        else []
    )
    experience_matches: list[Any] = []
    if (
        formulation.case_ready
        and effective_resolution_context is not None
        and _continual_improvement is not None
    ):
        try:
            runtime_tool_profile = (
                _resolution_service.runtime_tool_profile(
                    case_id=case_snapshot.case_id,
                    permissions=resolution_permissions,
                    confirmation_granted=req.confirmation_granted,
                    actor_id=req.user_id,
                    tenant_id=req.tenant_id,
                )
                if _resolution_service is not None
                else None
            )
            effective_resolution_context, experience_matches = (
                await _continual_improvement.enrich_resolution_context(
                    effective_resolution_context,
                    tenant_id=req.tenant_id,
                    runtime_tool_profile=runtime_tool_profile,
                )
            )
        except Exception:
            logger.exception(
                "M7 experience retrieval failed case_id=%s", case_snapshot.case_id
            )
    changed_scopes = await _case_run_registry.invalidation_scopes(
        case_snapshot.case_id,
        user_id=req.user_id,
        tenant_id=req.tenant_id,
        revision=case_snapshot.case_revision,
    )
    invalidated_jobs = (
        await _case_run_registry.invalidate_stale_jobs(
            case_snapshot.case_id, case_snapshot.case_revision
        )
        if changed_scopes & {"goal", "evidence", "constraints"}
        else 0
    )
    if invalidated_jobs:
        await _case_run_registry.record_runtime_event(
            case_snapshot.case_id,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            event_type="stale_jobs_invalidated",
            status="stale",
            summary="New Case evidence invalidated older asynchronous work",
            data={
                "count": invalidated_jobs,
                "revision": case_snapshot.case_revision,
                "dependency_scopes": sorted(changed_scopes),
            },
        )
    resolution_result = None
    collaboration_result = None
    collaboration_id = ""
    m3_thread_id = ""
    m3_status = ""
    response_text = formulation.response
    response_details = ""
    resource_budget_blocked = False
    outcome_verdict = ""
    improvement_signal_count = 0
    improvement_proposal_count = 0
    prior_resolution_progress_count = len(
        (case_snapshot.m2 or {}).get("progress_events", [])
    )
    if (
        formulation.case_ready
        and effective_resolution_context is not None
        and _resolution_service is not None
    ):
        exceeded = await _case_run_registry.budget_exceeded(
            case_snapshot.case_id,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
        )
        if exceeded:
            resource_budget_blocked = True
            case_snapshot = await _case_run_registry.transition(
                case_snapshot.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                action="pause",
                reason="Resource envelope reached before the next resolution episode.",
            )
            response_text = "当前资源范围已达到上限，Case 进度已经保留；调整预算后可以继续。"
        stage_started = time.monotonic()
        try:
            resolution_result = (
                None
                if resource_budget_blocked
                else await _resolution_service.resolve(
                    resolution_context=effective_resolution_context,
                    thread_id=case_snapshot.m2_thread_id,
                    permissions=resolution_permissions,
                    confirmation_granted=req.confirmation_granted,
                    failure_memory_enabled=(
                        experiment_variant is not ExperimentVariant.NO_FAILURE_MEMORY
                    ),
                    actor_id=req.user_id,
                    tenant_id=req.tenant_id,
                )
            )
        except Exception as exc:
            logger.exception("M2 processing failed case_id=%s", case_snapshot.case_id)
            try:
                await _case_run_registry.record_error(
                    case_id=case_snapshot.case_id,
                    user_id=req.user_id,
                    tenant_id=req.tenant_id,
                    phase=CasePhase.RESOLUTION,
                    error_code="M2_RUNTIME_FAILURE",
                    expected_request_generation=request_generation,
                )
            except StaleCaseResultError:
                raise HTTPException(409, "该轮 M2 结果已被更新状态替代") from exc
            raise HTTPException(
                502,
                "解决阶段暂时失败，已保留当前 Case，可从现有进度继续",
            ) from exc
        stage_latency_ms["m2_resolution"] = round(
            (time.monotonic() - stage_started) * 1000, 1
        )
        if resolution_result is None:
            resolution_permissions = []
        else:
            try:
                case_snapshot = await _case_run_registry.record_resolution(
                    resolution_result,
                    user_id=req.user_id,
                    tenant_id=req.tenant_id,
                    expected_case_revision=case_snapshot.case_revision,
                    expected_request_generation=request_generation,
                )
            except StaleCaseResultError as exc:
                raise HTTPException(409, str(exc)) from exc
            (
                outcome_verdict,
                improvement_signal_count,
                improvement_proposal_count,
            ) = await _observe_case_for_improvement(case_snapshot)
        user_state_events = [
            item
            for item in (resolution_result.control_events if resolution_result else [])
            if item.get("mechanism") == "case_user_state"
        ]
        if resolution_result is not None:
            await _case_run_registry.record_control_activation(
                case_snapshot.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                mechanism="case_user_state",
                activated=bool(user_state_events),
                inputs=(user_state_events[-1].get("inputs", {}) if user_state_events else {}),
                behavior_changes=(
                    [
                        f"tool_ranking:{key}={value}"
                        for key, value in user_state_events[-1].get("behavior_changes", {}).items()
                    ]
                    if user_state_events
                    else []
                ),
                outcome={"resolution_status": resolution_result.status.value},
            )
        failure_blocks = sum(
            item.get("error_code") == "REPEATED_FAILED_ACTION_BLOCKED"
            for item in (resolution_result.tool_history if resolution_result else [])
        )
        if resolution_result is not None:
            await _case_run_registry.record_control_activation(
                case_snapshot.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                mechanism="failure_memory",
                activated=bool(failure_blocks),
                inputs={"enabled": (experiment_variant is not ExperimentVariant.NO_FAILURE_MEMORY)},
                behavior_changes=(
                    [f"blocked_repeated_failed_calls:{failure_blocks}"] if failure_blocks else []
                ),
                outcome={"resolution_status": resolution_result.status.value},
            )
        if resolution_result is not None:
            response_text = resolution_result.response
        always_on_fixed = resolution_result is not None and (
            experiment_variant is ExperimentVariant.ALWAYS_ON_FIXED_THREE
        )
        m3_requested = (
            resolution_result is not None
            and resolution_result.status.value == "collaboration_required"
            and resolution_result.collaboration_handoff is not None
        )
        if (
            (m3_requested or always_on_fixed)
            and _collaboration_service is not None
            and experiment_variant is not ExperimentVariant.M2_ONLY
        ):
            source_handoff = resolution_result.collaboration_handoff
            if source_handoff is None:
                source_handoff = _resolution_service.build_experimental_collaboration_handoff(
                    result=resolution_result,
                    resolution_context=effective_resolution_context,
                    reason=(
                        "always-on fixed-team experimental control; M2 did not request "
                        "collaboration"
                    ),
                )
            m2_handoff = source_handoff.model_copy(
                update={"case_revision": case_snapshot.case_revision}, deep=True
            )
            await _case_run_registry.record_control_activation(
                case_snapshot.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                mechanism="m3_invocation_policy",
                activated=True,
                inputs={
                    "m2_status": resolution_result.status.value,
                    "reason": resolution_result.collaboration_reason or "",
                    "gate_bypassed": always_on_fixed and not m3_requested,
                },
                behavior_changes=[
                    (
                        "bypassed_gate_for_always_on_control"
                        if always_on_fixed and not m3_requested
                        else "scheduled_durable_collaboration"
                    )
                ],
            )
            from core.multi_agent_collaboration import CollaborationTopology

            collaboration_topology = (
                CollaborationTopology.FIXED_THREE
                if experiment_variant
                in {
                    ExperimentVariant.FIXED_THREE,
                    ExperimentVariant.GATED_FIXED_THREE,
                    ExperimentVariant.ALWAYS_ON_FIXED_THREE,
                }
                else CollaborationTopology.ADAPTIVE
            )
            try:
                job = await _schedule_collaboration_job(
                    case_snapshot=case_snapshot,
                    handoff=m2_handoff,
                    resolution_context=effective_resolution_context,
                    permissions=resolution_permissions,
                    topology=collaboration_topology.value,
                    failure_memory_enabled=(
                        experiment_variant is not ExperimentVariant.NO_FAILURE_MEMORY
                    ),
                    previous_tool_count=len(resolution_result.tool_history),
                    expected_request_generation=request_generation,
                )
            except StaleCaseResultError as exc:
                raise HTTPException(409, str(exc)) from exc
            collaboration_id = str(job.payload.get("collaboration_id", ""))
            m3_thread_id = str(job.payload.get("thread_id", ""))
            m3_status = job.status.value
            response_text = resolution_result.response + " 已开始协作处理，可查看实时进度。"
        elif resolution_result is not None:
            await _case_run_registry.record_control_activation(
                case_snapshot.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                mechanism="m3_invocation_policy",
                activated=False,
                inputs={"m2_status": resolution_result.status.value},
                behavior_changes=["kept_single_owner_resolution"],
                outcome={"coordination_cost_avoided": True},
            )

    stage_started = time.monotonic()
    full_response_text = response_text
    human_state = derive_human_collaboration_state(
        effective_resolution_context,
        overrides=case_snapshot.human_collaboration.preference_overrides,
    )
    episode_progress = (
        [item.model_dump(mode="json") for item in resolution_result.progress_events][
            prior_resolution_progress_count:
        ]
        if resolution_result is not None
        else []
    )
    visible_progress = select_visible_progress(
        episode_progress,
        human_state,
    )
    latest_progress = (
        str(visible_progress[-1].get("message", ""))
        if visible_progress
        else ""
    )
    presentation = layer_response(
        response_text,
        human_state,
        progress_message=latest_progress,
    )
    response_text = presentation.primary_message
    response_details = presentation.details
    try:
        case_snapshot = await _case_run_registry.record_human_effort(
            case_snapshot.case_id,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            presentation=presentation,
            asked_questions=(
                int(bool(formulation.policy.question))
                + len(resolution_result.user_questions)
                if resolution_result is not None
                else int(bool(formulation.policy.question))
            ),
            requested_actions=(
                len(resolution_result.requested_user_actions)
                + int(
                    any(
                        item.get("mechanism") == "initiative_mode"
                        and item.get("behavior_changes", {}).get(
                            "action_requires_user_confirmation"
                        )
                        for item in resolution_result.control_events
                    )
                )
                if resolution_result is not None
                else 0
            ),
            progress_updates=len(visible_progress),
            expected_request_generation=request_generation,
        )
    except StaleCaseResultError as exc:
        raise HTTPException(409, str(exc)) from exc
    stage_latency_ms["presentation_and_accounting"] = round(
        (time.monotonic() - stage_started) * 1000, 1
    )

    # 4. 写入记忆前再次检查代次，避免旧请求污染后续会话上下文。
    if request_generation is not None:
        try:
            await _case_run_registry.assert_current_request(
                case_snapshot.case_id,
                user_id=req.user_id,
                tenant_id=req.tenant_id,
                expected_request_generation=request_generation,
            )
        except StaleCaseResultError as exc:
            raise HTTPException(409, str(exc)) from exc

    # 5. 写入记忆
    memory_message = req.message + (
        f"\n[attached_evidence={len(evidence_artifacts)}]" if evidence_artifacts else ""
    )
    stage_started = time.monotonic()
    await _memory.add_message(req.user_id, conv_id, MsgRole.USER, memory_message)
    await _memory.add_message(req.user_id, conv_id, MsgRole.ASSISTANT, full_response_text)
    stage_latency_ms["memory_write"] = round(
        (time.monotonic() - stage_started) * 1000, 1
    )

    return ChatResponse(
        conv_id=conv_id,
        case_id=formulation.state.case_id,
        case_phase=case_snapshot.phase.value,
        case_revision=case_snapshot.case_revision,
        request_generation=case_snapshot.request_generation,
        case_progress_url=f"/cases/{formulation.state.case_id}",
        request_id=str(uuid.uuid4())[:8],
        trace_id=formulation.trace_id,
        response=response_text,
        response_details=response_details,
        latency_ms=round((time.monotonic() - request_started) * 1000, 1),
        stage_latency_ms=stage_latency_ms,
        case_status=formulation.state.status.value,
        case_ready=formulation.case_ready,
        evidence_sufficiency=formulation.state.evidence_sufficiency.value,
        clarification_triggered=(
            not formulation.case_ready
            and formulation.state.status.value not in {"meta_handled", "formulation_error"}
        ),
        interaction_action=formulation.policy.action.value,
        supporting_actions=[item.value for item in formulation.policy.supporting_actions],
        interaction_needs=[item.type.value for item in formulation.state.interaction_needs],
        addressed_need=(
            formulation.policy.addressed_need.value if formulation.policy.addressed_need else ""
        ),
        target_evidence=formulation.policy.target_evidence or "",
        contract_compliant=formulation.policy.compliance.compliant,
        contract_violations=formulation.policy.compliance.violations,
        interaction_progress=(
            formulation.state.formulation_history[-1].progress.value
            if formulation.state.formulation_history
            else ""
        ),
        evaluation_variant=experiment_variant.value,
        problem_state=formulation.state.model_dump(mode="json"),
        resolution_context=effective_resolution_context,
        m1_model_calls=formulation.m1_model_calls,
        behavior_planning_mode=formulation.policy.planning_mode,
        control_priors=formulation.policy.control_priors.model_dump(mode="json"),
        resolution_status=(resolution_result.status.value if resolution_result is not None else ""),
        resolution_thread_id=(resolution_result.thread_id if resolution_result is not None else ""),
        m2_model_calls=(
            resolution_result.episode_decisions if resolution_result is not None else 0
        ),
        progress_events=(
            [item.model_dump(mode="json") for item in resolution_result.progress_events]
            if resolution_result is not None
            else []
        ),
        collaboration_handoff=(
            resolution_result.collaboration_handoff.model_dump(mode="json")
            if resolution_result is not None and resolution_result.collaboration_handoff is not None
            else None
        ),
        m3_status=m3_status,
        collaboration_id=collaboration_id,
        m3_agents=(
            collaboration_result.agents_recruited if collaboration_result is not None else []
        ),
        m3_rounds=(collaboration_result.rounds if collaboration_result is not None else 0),
        m3_thread_id=m3_thread_id,
        collaboration_result=(
            collaboration_result.model_dump(mode="json")
            if collaboration_result is not None
            else None
        ),
        human_collaboration=case_snapshot.human_collaboration.model_dump(mode="json"),
        evidence_artifacts=[
            item.model_dump(mode="json") for item in case_snapshot.evidence_artifacts
        ],
        visible_progress=(
            visible_progress
        ),
        experience_candidates=[
            {
                "experience_id": item.experience.experience_id,
                "kind": item.experience.kind.value,
                "score": item.score,
                "why_applicable": item.why_applicable,
                "must_reverify": item.must_reverify,
            }
            for item in experience_matches
        ],
        experience_source_ids=(
            list(resolution_result.experience_source_ids)
            if resolution_result is not None
            else []
        ),
        retrieved_experience_ids=(
            list(resolution_result.retrieved_experience_ids)
            if resolution_result is not None
            else []
        ),
        considered_experience_ids=(
            list(resolution_result.considered_experience_ids)
            if resolution_result is not None
            else []
        ),
        adopted_experience_ids=(
            list(resolution_result.adopted_experience_ids)
            if resolution_result is not None
            else []
        ),
        verified_experience_ids=(
            list(resolution_result.verified_experience_ids)
            if resolution_result is not None
            else []
        ),
        outcome_verdict=outcome_verdict,
        improvement_signal_count=improvement_signal_count,
        improvement_proposal_count=improvement_proposal_count,
    )


@app.get("/cases/{case_id}", response_model=CaseRunSnapshot, tags=["M4 Case Runtime"])
async def get_case_progress(case_id: str, user_id: str, tenant_id: str = "local"):
    """Return the unified, chain-of-thought-free M1→M2→M3→M2 Case view."""

    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        snapshot = await _case_run_registry.get(
            case_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if snapshot is None:
        raise HTTPException(404, "Case 不存在或当前进程尚未加载")
    return snapshot


@app.get(
    "/cases/by-conversation/{conv_id}",
    response_model=CaseRunSnapshot,
    tags=["M4 Case Runtime"],
)
async def get_case_by_conversation(conv_id: str, user_id: str, tenant_id: str = "local"):
    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    snapshot = await _case_run_registry.get_by_conversation(
        conv_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )
    if snapshot is None:
        raise HTTPException(404, "Conversation 尚未关联 Case")
    return snapshot


@app.post(
    "/cases/{case_id}/control",
    response_model=CaseRunSnapshot,
    tags=["M5 Long-Horizon Case Runtime"],
)
async def control_case(case_id: str, req: CaseControlInput):
    """Pause, resume, cancel, reopen, or explicitly wait without deleting history."""

    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        snapshot = await _case_run_registry.transition(
            case_id,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            action=req.action,
            reason=req.reason,
        )
        if req.action.strip().lower() == "cancel":
            _interrupt_active_chat(case_id)
            _pending_chat_inputs.pop(case_id, None)
            await _case_run_registry.cancel_case_jobs(case_id)
            if _case_job_worker is not None:
                await _case_job_worker.interrupt_case(case_id)
        elif req.action.strip().lower() == "pause":
            _interrupt_active_chat(case_id)
            await _case_run_registry.pause_case_jobs(case_id)
            if _case_job_worker is not None:
                await _case_job_worker.interrupt_case(case_id)
        elif req.action.strip().lower() in {"resume", "reopen"} and _case_job_worker is not None:
            await _case_run_registry.resume_case_jobs(case_id)
            _case_job_worker.wake()
        return snapshot
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.put(
    "/cases/{case_id}/budget",
    response_model=CaseRunSnapshot,
    tags=["M5 Long-Horizon Case Runtime"],
)
async def update_case_budget(case_id: str, req: CaseBudgetInput):
    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        return await _case_run_registry.set_budget_limits(
            case_id,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            limits=req.limits,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put(
    "/cases/{case_id}/preferences",
    response_model=CaseRunSnapshot,
    tags=["M6 Human Collaboration"],
)
async def update_case_preferences(case_id: str, req: CasePreferencesInput):
    """Update explicit preferences for this Case only, never a permanent persona."""

    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        return await _case_run_registry.set_interaction_preferences(
            case_id,
            user_id=req.user_id,
            tenant_id=req.tenant_id,
            preferences=req.preferences,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/cases/{case_id}/jobs", tags=["M5 Long-Horizon Case Runtime"])
async def list_case_jobs(
    case_id: str,
    user_id: str,
    tenant_id: str = "local",
    limit: int = 100,
):
    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        snapshot = await _case_run_registry.get(case_id, user_id=user_id, tenant_id=tenant_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if snapshot is None:
        raise HTTPException(404, "Case 不存在")
    jobs = (await _case_run_registry.list_jobs(case_id))[-max(1, min(limit, 500)) :]
    return {"case_id": case_id, "jobs": [item.model_dump(mode="json") for item in jobs]}


@app.get("/cases/{case_id}/events", tags=["M5 Long-Horizon Case Runtime"])
async def list_case_events(
    case_id: str,
    user_id: str,
    tenant_id: str = "local",
    after_sequence: int = 0,
    limit: int = 200,
):
    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        snapshot = await _case_run_registry.get(case_id, user_id=user_id, tenant_id=tenant_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if snapshot is None:
        raise HTTPException(404, "Case 不存在")
    return {
        "case_id": case_id,
        "events": await _case_run_registry.list_all_events(
            case_id,
            after_sequence=after_sequence,
            limit=limit,
        ),
    }


@app.get("/cases/{case_id}/console", tags=["M6 Human Collaboration"])
async def get_case_console(
    case_id: str,
    user_id: str,
    tenant_id: str = "local",
    event_limit: int = 80,
):
    """Return a user-facing Case projection without raw hidden reasoning."""

    if _case_run_registry is None:
        raise HTTPException(503, "Case 运行时未就绪")
    try:
        snapshot = await _case_run_registry.get(case_id, user_id=user_id, tenant_id=tenant_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if snapshot is None:
        raise HTTPException(404, "Case 不存在")
    events = await _case_run_registry.list_all_events(
        case_id,
        limit=max(1, min(event_limit, 200)),
        latest=True,
    )
    jobs = await _case_run_registry.list_jobs(case_id)
    terminal = snapshot.phase in {
        CasePhase.RESOLVED,
        CasePhase.CANCELLED,
        CasePhase.HUMAN_REQUIRED,
        CasePhase.ERROR,
        CasePhase.TIMED_OUT,
    }
    verification_events = [
        item
        for item in snapshot.m2.get("tool_history", [])
        if item.get("category") == "verify"
    ]
    latest_verification = verification_events[-1] if verification_events else {}
    next_request = ""
    if snapshot.phase in {CasePhase.WAITING_FOR_USER, CasePhase.HUMAN_REQUIRED}:
        next_request = snapshot.response
    elif snapshot.context_summary.open_evidence:
        next_request = str(
            snapshot.context_summary.open_evidence[0].get("question")
            or snapshot.context_summary.open_evidence[0].get("description")
            or "补充关键证据"
        )
    current_blocker = ""
    if snapshot.phase in {
        CasePhase.WAITING_FOR_USER,
        CasePhase.WAITING_FOR_EXTERNAL,
        CasePhase.HUMAN_REQUIRED,
        CasePhase.ERROR,
        CasePhase.TIMED_OUT,
    }:
        current_blocker = snapshot.status
    confirmation_required = any(
        item.get("mechanism") == "initiative_mode"
        and item.get("behavior_changes", {}).get("action_requires_user_confirmation")
        for item in snapshot.m2.get("control_events", [])
    ) and snapshot.phase is CasePhase.WAITING_FOR_USER
    return {
        "case_id": snapshot.case_id,
        "conversation_id": snapshot.conv_id,
        "phase": snapshot.phase.value,
        "status": snapshot.status,
        "revision": snapshot.case_revision,
        "request_generation": snapshot.request_generation,
        "goal_revision": snapshot.goal_revision,
        "updated_at": snapshot.updated_at,
        "terminal": terminal,
        "partial_result": snapshot.response,
        "latest_result": snapshot.response,
        "next_request": next_request,
        "verification_status": (
            "independently_verified"
            if snapshot.phase is CasePhase.RESOLVED
            else str(latest_verification.get("status") or "not_verified")
        ),
        "current_blocker": current_blocker,
        "confirmation_required": confirmation_required,
        "goal": snapshot.context_summary.goal,
        "confirmed_facts": snapshot.context_summary.confirmed_facts,
        "reported_observations": snapshot.context_summary.reported_observations,
        "provisional_explanations": (snapshot.context_summary.provisional_explanations),
        "open_evidence": snapshot.context_summary.open_evidence,
        "salient_failures": snapshot.context_summary.salient_failures,
        "action_constraints": snapshot.context_summary.action_constraints,
        "human_collaboration": snapshot.human_collaboration.model_dump(mode="json"),
        "resource_budget": {
            "limits": snapshot.budget_limits.model_dump(mode="json"),
            "usage": snapshot.budget_usage.model_dump(mode="json"),
        },
        "evidence_artifacts": [
            item.model_dump(mode="json") for item in snapshot.evidence_artifacts
        ],
        "progress": [
            {
                "sequence": item.get("sequence"),
                "phase": item.get("phase"),
                "type": item.get("event_type"),
                "status": item.get("status"),
                "summary": item.get("summary"),
                "created_at": item.get("created_at"),
            }
            for item in events
        ],
        "jobs": [
            {
                "job_id": item.job_id,
                "kind": item.kind.value,
                "status": item.status.value,
                "attempts": item.attempts,
                "max_attempts": item.max_attempts,
                "available_at": item.available_at,
                "last_error": item.last_error,
            }
            for item in jobs[-20:]
        ],
        "allowed_actions": (
            ["reopen"]
            if terminal
            else (
                ["resume", "cancel"] if snapshot.phase is CasePhase.PAUSED else ["pause", "cancel"]
            )
        ),
    }


@app.get("/monitor")
async def monitor_summary():
    """Runtime tool metrics without a legacy routing feedback loop."""
    if _tool_runtime is None:
        raise HTTPException(503, "服务未就绪")
    return {"tool_stats": _tool_runtime.get_stats()}


@app.get("/trace/tool/{case_id}", response_model=ToolTraceResponse)
async def get_tool_trace(
    case_id: str,
    user_id: str,
    tenant_id: str = "local",
):
    """查看某个 Case 的权威本地工具审计记录。"""
    if _tool_runtime is None or _case_run_registry is None:
        raise HTTPException(503, "服务未就绪")
    try:
        snapshot = await _case_run_registry.get(case_id, user_id=user_id, tenant_id=tenant_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if snapshot is None:
        raise HTTPException(404, "Case 不存在")
    events = await _tool_runtime.audit_log.list(case_id=case_id)
    trace = {"case_id": case_id, "events": [item.model_dump(mode="json") for item in events]}
    return ToolTraceResponse(
        request_id=case_id,
        found=bool(events),
        trace=trace,
    )


@app.get("/trace/tools", response_model=RecentToolTracesResponse)
async def list_recent_tool_traces(limit: int = 20):
    """查看最近 N 条工具审计事件。"""
    if os.getenv("CONCORD_ENABLE_GLOBAL_TRACE_DEBUG", "false").lower() != "true":
        raise HTTPException(403, "全局 trace 调试入口默认关闭")
    if _tool_runtime is None:
        raise HTTPException(503, "服务未就绪")
    events = await _tool_runtime.audit_log.list()
    size = max(1, min(int(limit or 20), 200))
    return RecentToolTracesResponse(
        items=[item.model_dump(mode="json") for item in reversed(events[-size:])]
    )


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus 指标入口。"""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/search")
async def search(query: str, top_k: int = 5):
    """通过正式工具运行时检索整理后的自然语言知识库。"""
    if _tool_runtime is None:
        raise HTTPException(503, "服务未就绪")
    from core.tools import ToolContext, ToolInvocation

    result = await _tool_runtime.execute(
        ToolInvocation(
            tool_id="knowledge_search",
            arguments={"query": query, "top_k": top_k},
            context=ToolContext(case_id=f"search:{uuid.uuid4()}", synthetic=True),
        )
    )
    if not result.success:
        raise HTTPException(503, result.error or "知识库检索失败")
    return result.model_dump(mode="json")


@app.get("/runtime/tools", tags=["Adaptive Tool Runtime"])
async def runtime_tools():
    if _tool_runtime is None:
        raise HTTPException(503, "工具运行时未初始化")
    return {
        "tools": [item.public_definition() for item in _tool_runtime.registry.list()],
        "stats": _tool_runtime.get_stats(),
    }


@app.post("/runtime/cases", tags=["Adaptive Tool Runtime"])
async def create_runtime_case(body: SimulationCaseInput):
    if _tool_runtime is None:
        raise HTTPException(503, "工具运行时未初始化")
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(403, "生产环境禁止通过调试 API 创建模拟 Case")
    from infrastructure.simulation import ScenarioActionRule, ScenarioObservationRule

    case_id = body.case_id or str(uuid.uuid4())
    try:
        case = await _tool_runtime.scenarios.create_case(
            case_id,
            visible_state=body.visible_state,
            hidden_state=body.hidden_state,
            action_rules=[ScenarioActionRule.model_validate(item) for item in body.action_rules],
            observation_rules=[
                ScenarioObservationRule.model_validate(item) for item in body.observation_rules
            ],
            replace=body.replace,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "case_id": case.case_id,
        "visible_state": case.visible_state,
        "actions": await _tool_runtime.scenarios.list_actions(case_id),
    }


@app.get("/runtime/cases/{case_id}", tags=["Adaptive Tool Runtime"])
async def get_runtime_case(case_id: str, include_hidden: bool = False):
    if _tool_runtime is None:
        raise HTTPException(503, "工具运行时未初始化")
    if (
        include_hidden
        and os.getenv("CONCORD_ENABLE_EVALUATION_VARIANTS", "false").lower() != "true"
    ):
        raise HTTPException(403, "隐藏模拟状态只对本地评测开放")
    try:
        return await _tool_runtime.scenarios.snapshot(
            case_id,
            include_hidden=include_hidden,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/runtime/tools/execute", tags=["Adaptive Tool Runtime"])
async def execute_runtime_tool(body: RuntimeToolInput):
    if _tool_runtime is None:
        raise HTTPException(503, "工具运行时未初始化")
    from core.tools import ToolContext, ToolInvocation

    result = await _tool_runtime.execute(
        ToolInvocation(
            tool_id=body.tool_id,
            arguments=body.arguments,
            idempotency_key=body.idempotency_key,
            context=ToolContext(
                case_id=body.case_id,
                thread_id=body.case_id,
                permissions=set(body.permissions),
                confirmation_granted=body.confirmation_granted,
                synthetic=True,
            ),
        )
    )
    return result.model_dump(mode="json")


@app.post("/runtime/faults", tags=["Adaptive Tool Runtime"])
async def inject_runtime_fault(body: RuntimeFaultInput):
    if _tool_runtime is None:
        raise HTTPException(503, "工具运行时未初始化")
    if os.getenv("APP_ENV", "development").lower() == "production":
        raise HTTPException(403, "生产环境禁止故障注入")
    from infrastructure.simulation import FaultKind, SimulatedFault

    try:
        fault = SimulatedFault(
            kind=FaultKind(body.kind),
            phase=body.phase,
            message=body.message,
            retryable=body.retryable,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    await _tool_runtime.fault_plan.add(body.case_id, body.tool_id, fault)
    return {"status": "scheduled", "case_id": body.case_id, "tool_id": body.tool_id}


@app.post("/resolution/run", tags=["M2 Adaptive Resolution"])
async def run_adaptive_resolution(body: ResolutionRunInput):
    """Run one complete M2 closed loop against an M1 ResolutionContext."""
    if _resolution_service is None:
        raise HTTPException(503, "M2 解决运行时未初始化")
    try:
        result = await _resolution_service.resolve(
            resolution_context=body.resolution_context,
            thread_id=body.thread_id,
            permissions=body.permissions,
            confirmation_granted=body.confirmation_granted,
            episode_decision_budget=(body.max_cycles or body.episode_decision_budget),
            additional_evidence=body.additional_evidence,
            user_state_update=body.user_state_update,
            actor_id=body.actor_id,
            tenant_id=body.tenant_id,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    return result.model_dump(mode="json")


@app.post("/resolution/start", tags=["M2 Adaptive Resolution"])
async def start_adaptive_resolution(body: ResolutionRunInput):
    """Start M2 in the background so a web client can consume the SSE stream."""
    if _resolution_service is None:
        raise HTTPException(503, "M2 解决运行时未初始化")
    case_id = str(body.resolution_context.get("case_id", "")).strip()
    if not case_id:
        raise HTTPException(422, "resolution_context.case_id is required")
    thread_id = body.thread_id or f"m2:{case_id}:{uuid.uuid4()}"
    existing = _resolution_tasks.get(thread_id)
    if existing is not None and not existing.done():
        raise HTTPException(409, "该 M2 Case 正在运行")
    try:
        await _resolution_service.prepare_thread(
            thread_id=thread_id,
            case_id=case_id,
            tenant_id=body.tenant_id,
            actor_id=body.actor_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc

    async def run_in_background():
        try:
            await _resolution_service.resolve(
                resolution_context=body.resolution_context,
                thread_id=thread_id,
                permissions=body.permissions,
                confirmation_granted=body.confirmation_granted,
                episode_decision_budget=(body.max_cycles or body.episode_decision_budget),
                additional_evidence=body.additional_evidence,
                user_state_update=body.user_state_update,
                actor_id=body.actor_id,
                tenant_id=body.tenant_id,
            )
        except Exception:
            logger.exception("后台 M2 运行失败 thread_id=%s", thread_id)

    task = asyncio.create_task(run_in_background(), name=f"m2:{thread_id}")
    _resolution_tasks[thread_id] = task
    task.add_done_callback(lambda _: _resolution_tasks.pop(thread_id, None))
    return {
        "thread_id": thread_id,
        "case_id": case_id,
        "status": "started",
        "progress_url": f"/resolution/stream?thread_id={thread_id}",
    }


@app.get("/resolution/progress", tags=["M2 Adaptive Resolution"])
async def get_adaptive_resolution_progress(
    thread_id: str,
    actor_id: str = "anonymous",
    tenant_id: str = "local",
):
    """Poll checkpointed, user-facing M2 progress while a run is active."""
    if _resolution_service is None:
        raise HTTPException(503, "M2 解决运行时未初始化")
    try:
        progress = await _resolution_service.get_progress(
            thread_id,
            actor_id=actor_id,
            tenant_id=tenant_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "未找到该 M2 Case 线程") from exc
    if progress is None:
        raise HTTPException(404, "未找到该 M2 Case 线程")
    return progress


@app.post("/resolution/events", tags=["M2 Adaptive Resolution"])
async def enqueue_adaptive_resolution_event(body: ResolutionEventInput):
    """Append an ordered, idempotent event to an active or paused M2 Case."""
    if _resolution_service is None:
        raise HTTPException(503, "M2 解决运行时未初始化")
    from core.adaptive_resolution import CaseEvent, CaseEventType

    try:
        event = await _resolution_service.enqueue_event(
            CaseEvent(
                event_id=body.event_id or str(uuid.uuid4()),
                thread_id=body.thread_id,
                case_id=body.case_id,
                tenant_id=body.tenant_id,
                actor_id=body.actor_id,
                type=CaseEventType(body.type),
                payload=body.payload,
            )
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "未找到该 M2 Case 线程") from exc
    return event.model_dump(mode="json")


@app.post("/resolution/cancel", tags=["M2 Adaptive Resolution"])
async def cancel_adaptive_resolution(body: ResolutionCancelInput):
    if _resolution_service is None:
        raise HTTPException(503, "M2 解决运行时未初始化")
    try:
        event = await _resolution_service.cancel(
            thread_id=body.thread_id,
            case_id=body.case_id,
            actor_id=body.actor_id,
            tenant_id=body.tenant_id,
            event_id=body.event_id,
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "未找到该 M2 Case 线程") from exc
    return event.model_dump(mode="json")


@app.get("/resolution/stream", tags=["M2 Adaptive Resolution"])
async def stream_adaptive_resolution_progress(
    thread_id: str,
    actor_id: str = "anonymous",
    tenant_id: str = "local",
):
    """Stream checkpointed, grounded progress without exposing chain-of-thought."""
    if _resolution_service is None:
        raise HTTPException(503, "M2 解决运行时未初始化")
    try:
        await _resolution_service.get_progress(thread_id, actor_id=actor_id, tenant_id=tenant_id)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, "未找到该 M2 Case 线程") from exc

    async def event_stream():
        emitted = 0
        empty_polls = 0
        terminal = {
            "resolved",
            "waiting_for_user",
            "collaboration_required",
            "paused",
            "exhausted",
            "cancelled",
            "error",
        }
        while True:
            progress = await _resolution_service.get_progress(
                thread_id, actor_id=actor_id, tenant_id=tenant_id
            )
            if progress is None:
                empty_polls += 1
                if empty_polls > 80:
                    yield 'event: error\ndata: {"message":"case did not start"}\n\n'
                    return
                yield 'event: accepted\ndata: {"status":"starting"}\n\n'
                await asyncio.sleep(0.25)
                continue
            events = progress.get("progress_events", [])
            for event in events[emitted:]:
                yield (
                    "event: progress\ndata: "
                    + json.dumps(event, ensure_ascii=False, default=str)
                    + "\n\n"
                )
            emitted = len(events)
            if progress.get("status") in terminal:
                yield (
                    "event: complete\ndata: "
                    + json.dumps(progress, ensure_ascii=False, default=str)
                    + "\n\n"
                )
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/collaboration/cancel", tags=["M3 Adaptive Collaboration"])
async def cancel_adaptive_collaboration(body: CollaborationCancelInput):
    if _collaboration_service is None:
        raise HTTPException(503, "M3 协作运行时未初始化")
    task = _collaboration_tasks.get(body.thread_id)
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    progress = await _collaboration_service.cancel(body.thread_id)
    if progress is None:
        raise HTTPException(404, "未找到该 M3 协作线程")
    return progress


@app.get("/collaboration/agents", tags=["M3 Adaptive Collaboration"])
async def collaboration_agents():
    """Return capabilities and outcome-based reliability used for dynamic recruitment."""

    if _collaboration_service is None:
        raise HTTPException(503, "M3 协作运行时未初始化")
    items = await _collaboration_service.agent_catalog()
    contextual = await _collaboration_service.contextual_performance()
    return {
        "agents": [
            {
                **item.profile.model_dump(mode="json"),
                "reliability": item.reliability,
                "verified_samples": item.performance.samples,
                "average_latency_ms": item.performance.average_latency_ms,
            }
            for item in items
        ],
        "contextual_outcomes": {
            key: value.model_dump(mode="json") for key, value in contextual.items()
        },
    }


@app.post("/collaboration/run", tags=["M3 Adaptive Collaboration"])
async def run_adaptive_collaboration(body: CollaborationRunInput):
    """Run M3 from an uncertainty-preserving M2 CollaborationHandoff."""

    if _collaboration_service is None:
        raise HTTPException(503, "M3 协作运行时未初始化")
    try:
        result = await _collaboration_service.collaborate(
            handoff=body.handoff,
            thread_id=body.thread_id,
            max_rounds=body.max_rounds,
            max_agents=body.max_agents,
            minimum_gain_margin=body.minimum_gain_margin,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result.model_dump(mode="json")


@app.post("/collaboration/start", tags=["M3 Adaptive Collaboration"])
async def start_adaptive_collaboration(body: CollaborationRunInput):
    """Start M3 without blocking the user-facing request."""

    if _collaboration_service is None:
        raise HTTPException(503, "M3 协作运行时未初始化")
    from core.adaptive_resolution import CollaborationHandoff

    try:
        handoff = CollaborationHandoff.model_validate(body.handoff)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    collaboration_id = str(uuid.uuid4())
    thread_id = body.thread_id or f"m3:{handoff.thread_id}:{collaboration_id}"
    existing = _collaboration_tasks.get(thread_id)
    if existing is not None and not existing.done():
        raise HTTPException(409, "该 M3 协作正在运行")

    async def run_in_background():
        await _collaboration_service.collaborate(
            handoff=handoff,
            thread_id=thread_id,
            collaboration_id=collaboration_id,
            max_rounds=body.max_rounds,
            max_agents=body.max_agents,
            minimum_gain_margin=body.minimum_gain_margin,
        )

    task = asyncio.create_task(run_in_background(), name=f"m3:{collaboration_id}")
    _collaboration_tasks[thread_id] = task
    task.add_done_callback(lambda _: _collaboration_tasks.pop(thread_id, None))
    return {
        "collaboration_id": collaboration_id,
        "thread_id": thread_id,
        "case_id": handoff.case_id,
        "status": "started",
        "progress_url": f"/collaboration/stream?thread_id={thread_id}",
    }


@app.get("/collaboration/progress", tags=["M3 Adaptive Collaboration"])
async def adaptive_collaboration_progress(thread_id: str):
    if _collaboration_service is None:
        raise HTTPException(503, "M3 协作运行时未初始化")
    progress = await _collaboration_service.get_progress(thread_id)
    if progress is None:
        if thread_id in _collaboration_tasks:
            return {
                "thread_id": thread_id,
                "status": "starting",
                "progress_events": [],
            }
        raise HTTPException(404, "未找到该 M3 协作线程")
    return progress


@app.get("/collaboration/stream", tags=["M3 Adaptive Collaboration"])
async def stream_adaptive_collaboration(thread_id: str):
    """Stream grounded M3 stages without exposing agent chain-of-thought."""

    if _collaboration_service is None:
        raise HTTPException(503, "M3 协作运行时未初始化")
    initial = await _collaboration_service.get_progress(thread_id)
    if initial is None and thread_id not in _collaboration_tasks:
        raise HTTPException(404, "未找到该 M3 协作线程")

    async def event_stream():
        emitted = 0
        terminal = {
            "ready_for_m2",
            "evidence_required",
            "goal_alignment_required",
            "human_required",
            "no_benefit",
            "exhausted",
            "cancelled",
            "error",
        }
        while True:
            progress = await _collaboration_service.get_progress(thread_id)
            if progress is None:
                yield 'event: accepted\ndata: {"status":"starting"}\n\n'
                await asyncio.sleep(0.25)
                continue
            events = progress.get("progress_events", [])
            for event in events[emitted:]:
                yield (
                    "event: progress\ndata: "
                    + json.dumps(event, ensure_ascii=False, default=str)
                    + "\n\n"
                )
            emitted = len(events)
            if progress.get("status") in terminal:
                yield (
                    "event: complete\ndata: "
                    + json.dumps(progress, ensure_ascii=False, default=str)
                    + "\n\n"
                )
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/collaboration/feedback", tags=["M3 Adaptive Collaboration"])
async def record_collaboration_feedback(body: CollaborationFeedbackInput):
    """Record downstream verified outcomes; collaborator confidence is not accepted."""

    if _collaboration_service is None:
        raise HTTPException(503, "M3 协作运行时未初始化")
    if os.getenv("CONCORD_ENABLE_MANUAL_COLLABORATION_FEEDBACK", "false").lower() != "true":
        raise HTTPException(
            403,
            "手工协作反馈默认关闭；正式可靠性只接受 M2 验证结果自动回写",
        )
    from core.multi_agent_collaboration import AgentOutcomeFeedback

    try:
        updated = [
            await _collaboration_service.record_outcome(AgentOutcomeFeedback.model_validate(item))
            for item in body.outcomes
        ]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"agents": [item.model_dump(mode="json") for item in updated]}


@app.post("/knowledge/add", tags=["知识库"])
async def add_knowledge(body: BatchDocInput):
    """
    批量导入文档到知识库。

    文档会自动切片并存入本地 SQLite FTS 索引；检索结果只是候选参考，不是 Case 事实。

    示例请求体：
    ```json
    {
      "documents": [
        {"title": "退款政策", "content": "用户在购买后 7 天内可以申请无理由退款..."},
        {"title": "配送说明", "content": "标准配送 3-5 个工作日..."}
      ]
    }
    ```
    """
    if _knowledge_base is None:
        raise HTTPException(503, "知识库未初始化")
    count = await _knowledge_base.add_documents_async(
        [{"title": d.title, "content": d.content} for d in body.documents]
    )
    total = await _knowledge_base.doc_count_async()
    return {"message": f"成功导入 {count} 个文档片段", "added_chunks": count, "total_chunks": total}


@app.post("/knowledge/upload", tags=["知识库"])
async def upload_knowledge(file: Annotated[UploadFile, File(...)]):
    """
    上传文件导入知识库。

    支持格式：
    - `.txt` / `.md`：整个文件作为一篇文档，文件名作为标题
    - `.json`：JSON 数组格式 `[{"title": "...", "content": "..."}, ...]`

    文件大小限制：10MB
    """
    if _knowledge_base is None:
        raise HTTPException(503, "知识库未初始化")

    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(413, "文件大小超过 10MB 限制")

    text = content.decode("utf-8", errors="ignore")
    filename = file.filename or "unknown"

    if filename.endswith(".json"):
        import json as _json

        try:
            docs = _json.loads(text)
            if not isinstance(docs, list):
                raise HTTPException(400, "JSON 文件应为数组格式: [{title, content}, ...]")
        except _json.JSONDecodeError as e:
            raise HTTPException(400, f"JSON 解析失败: {e}")
    else:
        # txt / md：整个文件作为一篇文档
        title = filename.rsplit(".", 1)[0] if "." in filename else filename
        docs = [{"title": title, "content": text}]

    count = await _knowledge_base.add_documents_async(docs)
    total = await _knowledge_base.doc_count_async()
    return {
        "message": f"文件 {filename} 导入成功",
        "added_chunks": count,
        "total_chunks": total,
    }


@app.get("/knowledge/stats", tags=["知识库"])
async def knowledge_stats():
    """查看知识库统计信息（文档片段总数）。"""
    if _knowledge_base is None:
        raise HTTPException(503, "知识库未初始化")
    return {"total_chunks": await _knowledge_base.doc_count_async()}


# ── 交互式 CLI ────────────────────────────────────────────────────────────────
async def run_cli():
    print(BANNER)
    print("Concord CLI — 输入 quit 退出\n")

    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from core.problem_formulation.service import ProblemFormulationService
    from infrastructure.memory.conversation_memory import MemoryManager, MsgRole

    cfg = _llm_cfg()
    llm_client = create_llm_client(cfg)
    mem = MemoryManager(
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        recall_index_path=os.getenv("RETRIEVAL_INDEX_PATH", "./data/retrieval"),
        api_key=cfg.api_key,
        base_url=cfg.base_url,
        model=cfg.model,
        provider=cfg.provider,
        reasoning_effort=cfg.reasoning_effort,
        client=llm_client,
    )

    graph_db = pathlib.Path(
        os.getenv("CONCORD_GRAPH_DB", str(pathlib.Path(_ROOT) / "data" / "concord_graph.sqlite3"))
    ).resolve()
    graph_db.parent.mkdir(parents=True, exist_ok=True)
    checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(graph_db))
    checkpointer = await checkpointer_cm.__aenter__()
    await checkpointer.setup()
    formulation_service = ProblemFormulationService(
        checkpointer=checkpointer,
        client=llm_client,
        model=cfg.model,
        trace_path=os.getenv(
            "CONCORD_FORMULATION_TRACE",
            str(pathlib.Path(_ROOT) / "data" / "traces" / "problem_formulation.jsonl"),
        ),
    )

    user_id, conv_id = "cli_user", str(uuid.uuid4())

    while True:
        try:
            msg = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见 ʕ•ᴥ•ʔ")
            break
        if not msg or msg.lower() in ("quit", "exit", "退出"):
            print("再见 ʕ•ᴥ•ʔ")
            break

        ctx = await mem.get_context(user_id, conv_id, query=msg)
        history = (
            [{"role": m.role.value, "content": m.content} for m in ctx.recent_messages[-5:]]
            if ctx.recent_messages
            else None
        )
        formulation = await formulation_service.process(
            message=msg,
            user_id=user_id,
            conv_id=conv_id,
            domain_prior="other",
            solver_payload={
                "memory_context": ctx.to_prompt_text(),
                "history": history,
            },
        )

        await mem.add_message(user_id, conv_id, MsgRole.USER, msg)
        await mem.add_message(user_id, conv_id, MsgRole.ASSISTANT, formulation.response)

        node = formulation.policy.action.value
        print(f"\nConcord [{node}]: {formulation.response}\n")

    await mem.close()
    await checkpointer_cm.__aexit__(None, None, None)
    await llm_client.close()
