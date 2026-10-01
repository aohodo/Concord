# Concord Python Backend

Concord Python Runtime 当前版本为 **M8-B**，研究“受限理性与有噪声沟通条件下的人机协同问题表征与自适应多智能体求解”；具体业务场景作为可替换模块存在。

全项目设计总纲是 **Human behavior provides computational priors for Agent control**：人类行为研究提供注意、深思、停止、搜索控制与经验复用先验，而不是一层拟人化话术。

本阶段采用 Python 模块化单体，产品主链路由 M1–M7 的结构化 Case 控制模块组成：

- LangGraph 共享问题表征与跨轮 checkpoint
- Case-conditioned 用户状态和自适应人类行为策略
- 证据—行动—观察—验证闭环与失败记忆
- 按边际收益动态触发的多 Agent 协作
- Redis 工作记忆、SQLite FTS 非权威召回与 SQLite 持久事实
- 权限、幂等、审计、故障注入和模拟写入工具运行时
- LLM-as-Judge 与客观终态分离的纵向 Episode 评测

## 你可以先看什么

- [项目入口](../README.md)
- [V2 多轮 Episode 评测协议](../evaluation/v2_interaction/README.md)
- [人类行为可证伪契约](../evaluation/v2_interaction/HUMAN_BEHAVIOR_CONTRACT.md)
- [M8-B 架构、证据与复现](evaluation/m8_productization/M8_B_SHOWCASE.md)
- [活动调用链审计](evaluation/m8_productization/CALL_CHAIN_AUDIT.md)

## 快速开始

### 1. 准备环境

- Conda 环境 `concord`
- Docker Desktop（本地 Redis 使用）
- Anthropic API Key，或百炼/OpenAI-compatible API Key

Windows PowerShell 下进入本目录并安装后端依赖：

```powershell
cd backend
conda activate concord
python -m pip install -e ".[dev]"
```

默认示例使用百炼 `qwen3.8-flash`：

```env
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen3.8-flash
OPENAI_API_KEY=your_key
OPENAI_REASONING_EFFORT=low
```

切回 Anthropic 时设置 `LLM_PROVIDER=anthropic`，并配置
`ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL` 和可选的 `ANTHROPIC_BASE_URL`。
两种 provider 的模型 HTTP 客户端都禁止继承系统或终端的
`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`，模型请求固定直连。

LangSmith 是可选观测出口，默认关闭；启用时也默认隐藏输入、输出正文，并使用
不继承代理环境变量的独立 HTTP 会话：

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=your_langsmith_key
LANGSMITH_PROJECT=concord-dev-synthetic
CONCORD_LANGSMITH_INCLUDE_CONTENT=false
CONCORD_LANGSMITH_TRUST_ENV_PROXY=false
```

没有 LangSmith Key 不影响本地运行。权威工具审计仍写入
`data/traces/tool_audit.jsonl`，LangSmith 故障也不会打断业务执行。

### 2. 配置环境变量

复制示例配置：

```powershell
Copy-Item .env.example .env
```

最少确认这些变量可用：

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
REDIS_PASSWORD=concord-local-only
REDIS_URL=redis://:concord-local-only@127.0.0.1:6379/0
RETRIEVAL_INDEX_PATH=./data/retrieval
```

### 3. 启动服务

如需跨进程短期工作记忆，可选启动 Redis：

```powershell
docker compose up -d redis
docker compose ps redis
```

启动交互式 CLI：

```powershell
python main.py --cli
```

启动 FastAPI：

```powershell
python main.py
```

启动后可验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

存储职责是明确分层的：Redis（不可用时进程内降级）只保存带 TTL 的会话工作记忆；
SQLite FTS 保存可召回的历史叙述和内部知识候选，召回内容不具有当前事实权威；SQLite 中的
Case、事件、LangGraph checkpoint、工具审计关联和独立验证结果才是可恢复的业务事实。
因此 Redis 丢失只降低对话连续性，不能改变 Case 结论；历史召回进入模型时会明确标注
“不是当前事实、可能过期”。

需要完整 Docker 编排时：

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f concord
```

### 4. 访问入口

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Nginx: `http://localhost`
- Health: `http://localhost:8000/health`

## 核心功能

### 对话主链路

`POST /chat`

流程是：

```text
读取记忆
-> 轻量 Meta Gate（问候 / 停止 / 转人工）
-> 更新 SharedProblemState
-> 更新 CaseUserState
-> 检测 Interaction Needs
-> 构造 InteractionContract
-> 编译控制先验并选择一个 Primary Move + Modifiers
   -> 信息不足：澄清/校准/修复，等待下一轮
   -> CASE_READY：输出 ResolutionContext，并由同一 /chat 请求交给 M2
      -> M2 独立解决或等待用户
      -> 仅在继续单 Agent 求解不再最优时触发 M3
-> 写回记忆与可回放轨迹
```

`conv_id` 对应一个正在处理的 Case。调用方继续传入相同 `conv_id` 时，
LangGraph 会从 SQLite checkpoint 恢复共享问题状态；开始新问题时应创建新的
`conv_id`。

运行时不会把用户固定分类为某一种人格。`EXPERT / NOVICE / FRAGMENTED /
HALF_EXPERT / FRUSTRATED / DEADLINE` 等只作为评测原型；实际决策使用当前
Case 下的知识程度、表达清晰度、挫败感、耐心、控制感、操作意愿和时间约束。

### 知识库

- `POST /search`
- `POST /knowledge/add`
- `POST /knowledge/upload`
- `GET /knowledge/stats`

### 自适应环境交互运行时

- `GET /runtime/tools`：列出结构化工具契约与运行统计
- `POST /runtime/cases`：创建可重置的规则驱动模拟 Case
- `POST /runtime/tools/execute`：按权限、幂等和审计约束执行工具
- `POST /runtime/faults`：注入超时、冲突、限流或提交后断网等故障
- `GET /trace/tool/{case_id}`：读取某个 Case 的本地权威审计轨迹

运行时闭环是 `Observe -> Act -> Observe -> Verify`。真实文件和知识库只读；
会产生写入的动作默认落在有状态模拟环境中。以后接入真实系统时保持同一
`ToolSpec`，只替换 Adapter，并补真实权限、幂等、审计及集成测试。

### M2 Adaptive Resolution

`/chat` 在 M1 产生 `CASE_READY` 后会自动把同一 Case 交给 M2；也可以使用
`POST /resolution/run` 同步运行，或使用 `POST /resolution/start` 启动后台运行并立即
订阅 SSE 进度。三条入口最终进入同一条 LangGraph 闭环：

```text
声明式安全只读 bootstrap（并行）
→ 暂定解释
→ 选择信息价值/进度收益更高且成本可接受的工具能力
→ 运行时完成权限、风险与参数校验
→ 执行动作
→ 按工具返回的 VerificationRequest 自动并行独立验证
→ 仅在验证完成、预测不符、工具失败或阶段完成时重新规划
→ 独立验证后解决，或等待用户/触发协作
```

M2 使用事件触发规划，而不是在每次机械读取后调用模型。`bootstrap_safe` 只允许
低风险、无权限要求、无副作用的只读工具；状态改变工具返回结构化
`VerificationRequest`，运行时按能力契约自动选择观察工具并逐项验证。工具选择由
能力契约和资源理性排序完成，不是关键词路由；相同条件下的失败调用会被阻止重复。
最终用户回复从已验证成功标准生成；原因只在用户需要解释时以“暂定解释”呈现。
M2 碰到能力、权限、风险或连续无进展边界时输出结构化
`collaboration_handoff` 并进入 M3。

2026-09-28 使用真实 `qwen3.8-flash` 验收同一 VPN 模拟 Case：总耗时
17.74 秒、2 次模型规划、5 次工具调用；`vpn` 和 `access` 均得到独立观察，全部
工具调用保留审计，最终状态为 `resolved`。

同一 `thread_id` 可以携带 `additional_evidence` 和 `user_state_update` 恢复 Case。
`episode_decision_budget` 只约束一次无人值守求解回合；累计模型调用用于观测成本，
不作为整个 Case 的固定终止次数。调用方预先指定 `thread_id` 后，可以在执行期间轮询：

```text
GET /resolution/progress?thread_id=<thread_id>
GET /resolution/stream?thread_id=<thread_id>&actor_id=<actor>&tenant_id=<tenant>
```

返回 checkpoint 中已经发生的观察、动作、验证和最终状态，不暴露模型思维链。
`POST /resolution/events` 可以在运行过程中有序追加用户证据、用户状态变化、目标变化和
环境结果；事件 ID 幂等、SQLite 持久化，动作执行前会再次吸收新事件。短时间连续补充
会作为同一批证据交给规划器。`POST /resolution/cancel` 使用同一事件机制协作式停止，
不会把已执行或尚未验证的动作伪装成失败或成功。

线程首次运行时绑定 `tenant_id + actor_id + case_id`，之后的事件、进度和取消必须保持
相同归属。这里提供的是应用层 Case 隔离；真实部署仍应由 API Gateway 或登录系统提供
不可伪造的用户身份。

图片输入通过 `/chat` 的 `images` 数组提供，最多四张 `data:image/...` 或 HTTPS 图片。
视觉模型只提取可见事实、文字和不确定部分，并标记为
`unverified_visual_observation`；它不会直接诊断原因或覆盖工具验证。

M2 评测采用双轨：`m2_resolution_cases.json` 保存一次性交互 Snapshot Cases；
`m2_longitudinal_cases.json` 保存 12 条纵向轨迹，覆盖快速碎片补充、撤回错误判断、
话题漂移、延迟返回、错误确认、情绪变化、权限变化、目标变化和图片证据。
`--judge-humanity` 会额外运行独立 LLM Judge，分别判断结果导向、认知负担、情绪承接、
进度真实性、失败记忆和认识论克制；它不会把这些维度压成一个总分。

```powershell
# 静态场景
python -m evaluation.m2_resolution --concurrency 4 --output m2_snapshot.jsonl

# 持续补充场景
python -m evaluation.m2_resolution --longitudinal --concurrency 2 `
  --judge-humanity --output m2_longitudinal.jsonl
```

### M3 Adaptive Multi-Agent Collaboration

M3 不按固定领域名单 fan-out，而是按当前 Case 的认知瓶颈动态组织最小团队：

```text
M2 CollaborationHandoff
→ 判断 expected_information_gain 是否高于 coordination_cost
├─ 否：不招募，立即回到 M2/边界处理
└─ 是：按能力、经下游验证的历史可靠性和成本选择协作者
   → Specialist Handoff / Parallel Workers / Independent Review / Diverse Exploration
   → 独立上下文并行贡献
   → FACT / HYPOTHESIS / ACTION / GOAL 冲突分类
   → 形成 advisory_until_m2_verifies 的 ResumePacket
   → 回到 M2，通过工具执行和独立验证
```

M3 不固定拉取多个角色，而是按能力、证据与风险瓶颈决定是否协作、找谁、共享多少
上下文以及何时停止。事实冲突不投票，假设冲突保留
竞争解释，动作冲突比较风险/时间/成本/可逆性/信息价值，目标冲突返回用户目标。
Agent 的自报 confidence 不进入历史可靠性；只有后续实际结果才能通过反馈接口更新。

人类行为启发来自熟练团队的 Case Owner、shared mental model 与 transactive memory：
负责人保持全局状态，团队知道“谁擅长什么”，按需扩张和收缩。工程实现参考
[Google SRE Incident Management](https://sre.google/sre-book/managing-incidents/)、
[Anthropic Multi-Agent Research](https://www.anthropic.com/engineering/multi-agent-research-system)
和 [MAST failure taxonomy](https://arxiv.org/abs/2503.13657)。这些参考被落实为契约、
信息隔离、冲突协议、检查点和停止条件，不只是角色 Prompt。

`/chat` 遇到 M3 时不会同步等待完整协作：它立即返回 `m3_thread_id`，后台完成协作并把
ResumePacket 自动交回原 M2 thread。前端可读取真实进度：

```text
POST /collaboration/run       同步运行一次 M3
POST /collaboration/start     后台启动 M3
GET  /collaboration/progress  轮询 checkpoint
GET  /collaboration/stream    SSE 进度
GET  /collaboration/agents    能力与结果可靠性目录
POST /collaboration/feedback  写入下游验证后的 Agent 结果
```

真实部署必须由认证层保护 feedback 和 Case 访问；当前应用层接口不把调用方自报身份视为
不可伪造身份。M3 不具备生产写权限，所有动作必须回到 M2 的统一工具运行时。

M3 合成评测覆盖低收益拒绝、能力边界、紧急并行、高风险复核、搜索僵化、事实冲突、
目标冲突和错误并行。透明地把“固定三 Agent”作为配置成本参照，不把未实际运行的固定
baseline 冒充质量实验：

```powershell
python -m evaluation.m3_collaboration --concurrency 2 `
  --output m3_collaboration.jsonl
```

2026-09-29 真实 `qwen3.8-flash` 冒烟：低收益 Case 正确返回 `no_benefit`，0 个 Worker，
相对固定三 Agent 避免 3 次 Worker 调用；高风险 Case 选择 `independent_review`，仅招募
Risk Reviewer 与 Evidence Auditor，最终要求补证据，没有把高风险意见当成事实。

#### M3 Phase 2：结果驱动的自适应协作

第二阶段不继续增加角色，而是验证动态协作是否真的产生收益：

- `m2_only / fixed_three / adaptive` 三种拓扑都实际执行，固定拓扑不再只是配置数字；
- 每轮记录模型调用、Token、时延、Worker 失败、结构性重复和共享证据来源；
- `novelty_proxy` 只衡量非重复，不代表正确；只有 M2 明确引用 assignment 且工具结果验证后，才更新协作者可靠性；
- 可靠性按能力和任务类型记录，避免把某一类任务的成功泛化成全局专家；
- 手工 feedback API 默认关闭，防止调用方伪造结果污染可靠性；本地评测需要时可显式设置 `CONCORD_ENABLE_MANUAL_COLLABORATION_FEEDBACK=true`；
- 独立复核和替代探索使用隔离上下文，相同 `basis_refs` 不算独立佐证；
- 第二轮必须来自上一轮明确给出的互补 assignment，所有 Worker 失败则停止；
- 用户补充信息会推进 Case revision，旧 M3 结果不得回写新 Case；
- 支持 `POST /collaboration/cancel`，停止后台协作但保留 Case 和已有贡献。

### M5 Long-Horizon Case Governance

正式 `/chat` 链路中的 M3→M2 不再依赖进程内 `asyncio.create_task`。M5 将 Case、完整
生命周期 Event、异步 Job、模拟环境和工具幂等结果写入 `CONCORD_RUNTIME_DB`；
LangGraph 的 M1/M2/M3 checkpoint 继续写入 `CONCORD_GRAPH_DB`。服务启动时会重新领取
未完成或租约过期的 Job，已经提交的幂等动作由持久化结果重放，不会因重启再次产生
副作用。

```text
M1/M2 请求协作
→ 先持久化 collaborate_then_resume Job
→ Worker 领取带租约的 Job
→ M3 产生 advisory ResumePacket
→ 检查 Case revision，丢弃陈旧结果
→ 原 M2 thread 独立执行和验证
→ 仍需协作时生成下一轮持久化 Job
```

Case API：

```text
GET  /cases/{case_id}          当前紧凑快照、预算和状态
GET  /cases/{case_id}/events   完整 append-only 生命周期
GET  /cases/{case_id}/jobs     可恢复后台 Job
POST /cases/{case_id}/control  pause/resume/cancel/reopen/wait_for_*
PUT  /cases/{case_id}/budget   长任务资源范围
```

用户补充只在目标、证据或行动约束确实改变时使依赖旧状态的 Job 失效；单纯表达偏好变化
不会无条件清空整个 Case。运行时为用户状态、失败记忆和 M3 门控记录
`control_mechanism_evaluated`，区分“未接入”“已评估但未触发”“已触发但没改变行为”和
“已被消费者采用”。只有最后一类样本才允许进入效果消融。

M5 真实模型长 Episode 与正确 M3 对照：

```powershell
# 禁止继承代理后启动
$env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:ALL_PROXY=''; $env:NO_PROXY='*'
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000

python -m evaluation.m5_long_horizon --base-url http://127.0.0.1:8000 `
  --concurrency 4 --output-dir evaluation/m5_long_horizon/results/latest

# 需要 CONCORD_ENABLE_EVALUATION_VARIANTS=true
python -m evaluation.m5_long_horizon.topology_comparison `
  --base-url http://127.0.0.1:8000 --concurrency 4 `
  --output-dir evaluation/m5_long_horizon/results/topology
```

拓扑实验分为 `m2_only / always_on_fixed_three / gated_fixed_three / adaptive`：前两种差异
测量“是否需要协作”的门控收益，后两种差异测量进入 M3 后动态组队与停止的收益，避免
再次把没有进入调用链的机制误判为过度设计。

2026-09-30 真实 `qwen3.8-flash` M5 长任务为 `8/8`，其中 7 个 `resolved`、1 个权限
边界正确停止为 `human_required`，共保留 130 个生命周期事件和 4 个 durable Job。
四组同任务对照中，M2-only、always-on fixed-three、gated fixed-three、adaptive 的平均
时延分别为 `117.1s / 330.8s / 118.4s / 133.6s`，模型调用为 `50 / 73 / 50 / 54`。
always-on 虽然环境目标仍为 8/8，但终态治理为 0/8，并出现 7 次已解决后回退；因此该
结果证明的是无条件协作会增加成本并破坏 Case 治理，不应误写成现实任务解决率 0%。
完整 adaptive 为环境目标和终态治理双 `8/8`，只在 1/8 Case 触发 M3。

已有结果只需重建严格摘要时，不会再次调用模型：

```powershell
python -m evaluation.m5_long_horizon.topology_comparison `
  --output-dir evaluation/m5_long_horizon/results/topology --summarize-only
```

方法、行为映射和研究边界见
[`evaluation/m3_collaboration/M3_PHASE2_METHOD.md`](evaluation/m3_collaboration/M3_PHASE2_METHOD.md)。

真实三拓扑基线：

```powershell
python -m evaluation.m3_collaboration --concurrency 4 `
  --topologies m2_only fixed_three adaptive `
  --output evaluation/m3_collaboration/results/m3_phase2_topology_baseline.jsonl
```

36 个含碎片表达、错误推断、deadline、失败历史和中途纠正的纵向 Episode：

```powershell
python -m evaluation.m3_collaboration --longitudinal --concurrency 3 `
  --output evaluation/m3_collaboration/results/m3_phase2_longitudinal.jsonl
```

独立 LLM Judge 与透明报告：

```powershell
python -m evaluation.m3_collaboration.judge_file `
  --input evaluation/m3_collaboration/results/m3_phase2_topology_baseline.jsonl `
  --output evaluation/m3_collaboration/results/m3_phase2_topology_judged.jsonl

python -m evaluation.m3_collaboration.report `
  --baseline evaluation/m3_collaboration/results/m3_phase2_topology_judged.jsonl `
  --longitudinal evaluation/m3_collaboration/results/m3_phase2_longitudinal.jsonl `
                 evaluation/m3_collaboration/results/m3_phase2_longitudinal_rerun.jsonl `
  --vertical evaluation/m3_collaboration/results/m3_phase2_vertical_smoke_final.jsonl `
             evaluation/m3_collaboration/results/m3_phase2_verified_feedback_smoke.jsonl `
  --output-dir evaluation/m3_collaboration/results
```

2026-09-29 真实 `qwen3.8-flash` Phase 2 结果：8 个边界 Case 下，adaptive 为
`8/8`，固定三 Agent 为 `6/8`，M2-only 为 `3/8`；独立 LLM Judge 对 adaptive
给出 `8 PASS`，固定三 Agent 为 `2 PASS / 4 PARTIAL / 2 FAIL`。36 个纵向 Episode
首轮为 `28/36`；修复通用的工作依赖/权限交接契约后，只复跑失败项，最终为
`36/36`，12 次中途纠正均使旧协作结果失效。原始首轮结果和复跑结果分别保留，
最终合并文件不会覆盖审计记录。以上均为合成环境与辅助 Judge，不是专业人员真值。

两条真实产品链路冒烟分别验证：明确权限边界时 M3 以 0 次模型调用直接交给人类；
以及 M3 建议被 M2 引用、执行并独立观察后，只有对应 assignment 获得一条成功反馈。

### M4 End-to-End Integration and Evidence

M4 不再从 `ResolutionContext` 或 `CollaborationHandoff` 开始测试，而是从真实多轮用户输入通过 `/chat` 进入。系统为同一个 Case 统一关联：

```text
conversation → case_id → M2 thread → M3 collaboration → M2 verification
```

`POST /chat` 现在返回 `case_id`、`case_phase`、`case_revision` 和 `case_progress_url`。异步 M3 完成后，可使用以下接口读取最终 M3→M2 结果，而不只看到最初的“已开始协作”：

```text
GET /cases/{case_id}?user_id=...&tenant_id=local
GET /cases/by-conversation/{conv_id}?user_id=...&tenant_id=local
```

本地 Redis 不可用时，工作记忆自动降级为进程内实现，`GET /health` 会明确返回实际 backend；设置 `CONCORD_REQUIRE_REDIS=true` 可以在需要生产一致性的环境中禁止降级。

M4 扩展数据集包含 `10 个故障环境 × 11 种用户交互状态`，以及 1 条真实
M1→M2→M3→M2 垂直 Episode，共 111 条；全部只通过 HTTP API 运行：

```powershell
$env:HTTP_PROXY=''
$env:HTTPS_PROXY=''
$env:ALL_PROXY=''
$env:NO_PROXY='*'
$env:CONCORD_ENABLE_EVALUATION_VARIANTS='true'

python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
python -m evaluation.m4_end_to_end `
  --base-url http://127.0.0.1:8000 `
  --concurrency 4 `
  --batch-size 4 `
  --resume `
  --output-dir evaluation/m4_end_to_end/results/latest
```

运行器每 4 条保存一次检查点。因主机休眠、网络中断等外部原因需要替换指定样本时，
使用 `--rerun-episode-id <episode_id>`；污染样本必须先移入 `invalid_infrastructure`
保留，不能静默删除。

方法、边界与对照设计见 [`evaluation/m4_end_to_end/M4_METHOD.md`](evaluation/m4_end_to_end/M4_METHOD.md)。

2026-09-30 的第一版真实 `qwen3.8-flash` M4 基线：31 条 Episode 的环境结果验收为
`31/31`，其中 28 条 `resolved`，3 条权限边界正确停止为 `human_required`。
该数字只代表声明过的合成环境，不代表通用行业成功率。7 条同子集机制对照如下：

| variant | pass | average latency | model decisions | tool calls |
|---|---:|---:|---:|---:|
| 完整 Concord | 7/7 | 154.77s | 47 | 34 |
| M2-only | 6/7 | 153.17s | 50 | 35 |
| 固定三 Agent | 7/7 | 157.34s | 49 | 36 |
| 无用户状态 | 7/7 | 142.54s | 45 | 35 |
| 无事实/假设分离 | 7/7 | 175.72s | 50 | 36 |
| 无失败记忆 | 7/7 | 139.40s | 45 | 34 |

M2-only 唯一未解决项是连续工具失败后的专家恢复，说明 M3 应作为稀疏兜底而非固定步骤。
关闭事实/假设分离后，独立交互 Judge 记录到 2 次 `HYPOTHESIS_AS_FACT`；用户状态
显示小幅交互收益，但完整 31 条仍有 18 次 `USER_STATE_IGNORED`。该结果保留为历史
基线；扩展版使用 111 条纵向 Episode 和预先声明的 20 条配对消融子集，检验 M5–M8
接入调用链后的实际收益，而不是根据单个成功 Case 追加结论。

2026-10-02 扩展版真实模型结果：111/111 通过声明环境验收，平均/中位/p95 时延为
`130.42s / 94.90s / 296.04s`；记录 722 次模型决策、583 次工具调用，M3 仅在
15/111 条 Case 中触发。20 条预声明配对子集的结果如下：

| 方案 | 环境验收 | 模型调用 | 工具调用 | 平均时延 |
|---|---:|---:|---:|---:|
| 完整自适应链路 | 20/20 | 131 | 106 | 118.44s |
| 仅 M2 | 17/20 | 129 | 98 | 143.80s |
| 固定三 Agent | 20/20 | 155 | 111 | 139.26s |
| 关闭用户状态控制 | 17/20 | 156 | 108 | 164.96s |
| 关闭事实/推断分离 | 18/20 | 143 | 105 | 156.46s |
| 关闭失败记忆 | 17/20 | 144 | 110 | 130.68s |

完整链路与固定三 Agent 成功率相同，但少 24 次模型调用，平均快 20.82 秒；仅 M2
成本较低但漏掉 3 条需要协作或恢复的 Case。该实验是一轮随机模型下的配对工程观察，
不能解释为总体因果效应；每条原始结果、分层切片和被排除的基础设施污染均保留。

公开方法边界和完整系统结果见：

- [`evaluation/m4_end_to_end/M4_METHOD.md`](evaluation/m4_end_to_end/M4_METHOD.md)
- [`evaluation/m4_end_to_end/results/final_merged/M4_END_TO_END_REPORT.md`](evaluation/m4_end_to_end/results/final_merged/M4_END_TO_END_REPORT.md)
- [`evaluation/m4_end_to_end/results/final_merged/M4_INTERACTION_REPORT.md`](evaluation/m4_end_to_end/results/final_merged/M4_INTERACTION_REPORT.md)

### M6 Adaptive Human Collaboration and Case Console

M6 把“用户状态”从被动记录接入产品调用链。它描述当前 Case、当前轮的交互需要，
不推断永久人格，也不依赖原始关键词枚举：

- `HumanCollaborationState` 将 M1 的语义状态和用户显式偏好编译为
  `minimal / guided / expert`、`agent_led / shared / step_by_step` 和进度节奏；
- `HumanEffortBudget` 约束每轮追问、用户动作、默认阅读长度与静默等待；
- 默认回复按“立即可读的主结论 + 可展开完整细节”分层，细节无损保留；
- 图片、日志、文本文件和语音转写作为有来源、有认识论状态的证据进入 Case；
- Vue Case Console 展示目标、事实、待补证据、行动进度和后台 Job，并支持暂停、
  恢复、取消、重开和 Case 级偏好覆盖；
- 请求进入时先持久化 `turn_received`，长模型调用期间 Console 仍能给出可见进度；
- M5 Job 使用原子调度、租约代际 fencing、执行超时和在途任务中断，避免旧 Worker
  在用户取消或修订目标后覆盖新状态。

相关接口：

- `GET /cases/{case_id}/console`：面向用户的安全 Case 投影；
- `PUT /cases/{case_id}/preferences`：修改当前 Case 的表达、主动性和进度偏好；
- `POST /cases/{case_id}/control/{pause|resume|cancel|reopen}`：生命周期控制；
- `POST /chat` 的 `attachments`：提交文本、日志、文件、语音转写或图片证据。

结构化策略验收：

```powershell
cd backend
D:\anaconda3\envs\concord\python.exe -m evaluation.m6_human_collaboration
```

该契约测试只证明状态会改变行为预算和展示方式；真实模型纵向 Episode 仍以环境结果、
Case 轨迹和工具审计为事实源，不把回复观感当作解决成功。

2026-09-30 验收结果：结构化策略契约 `12/12`，Python 全量测试 `165 passed`；
真实 `qwen3.8-flash` 的 4 条多轮 Episode 为 `4/4`，其中 3 条完成环境恢复并验证，
1 条权限边界正确停在 `human_required`。平均墙钟时间约 139.51 秒，说明交互控制已
接入真实调用链，但模型时延仍是 M7 必须继续优化的产品问题。原始结果和无效配置批次
说明位于 [`evaluation/m6_human_collaboration/results/`](evaluation/m6_human_collaboration/results/)。

M6.5 在进入经验学习前收紧了上述运行时：

- `/chat` 使用 `request_generation` 拒绝旧 M1/M2 结果，并允许用户在处理期间
  继续补充；后续输入会取消并取代旧请求。
- 显式空权限表示撤销全部权限，证据只保留一个当前状态，SQLite 失败时
  回滚内存快照，过期 Worker lease 会在后续 claim 时自动回收。
- 用户问题数、操作数和可见进度按结构化事件计量；静默等待会产生
  心跳，`step_by_step` 会在 ACT 工具前等待显式确认。
- `GET /cases/{case_id}/console` 返回最新进度、当前结果、下一请求、验证状态和
  阻塞原因；`POST /chat` 返回 `stage_latency_ms` 用于区分记忆、视觉、M1、
  M2 与呈现耗时。
- M3 可靠性更新只接受真正的验证证据；权限拒绝、超时和运行时失败
  不再错误证伪专家建议。

M6.5 定向回归为 `183 passed`，Vue 生产构建通过。一条真实文本 Episode
用时 `52.922s`（M1 `33.625s`、M2 `18.953s`）；一条真实合成图片 Episode
用时 `98.438s`（视觉 `7.172s`、M1 `62.250s`、M2 `28.938s`）。这两条
只验证调用链与时延归因，不作为跨领域泛化结论。

另有 10 条真实模型交互条件批次：运行时/预算契约 `10/10`，但独立的负向
内容审查为 `5 PASS / 4 PARTIAL / 1 FAIL`。该 FAIL 反向定位到 M2 finalizer 会用
结构化问题覆盖 result-first 结论；修复后只复测该 Case 并通过，原始失败仍保留。
详细结果见
[`evaluation/m6_human_collaboration/results/m6_5_targeted/`](evaluation/m6_human_collaboration/results/m6_5_targeted/)。

### M7 结果驱动的持续改进

M7 不把原始对话或模型自评直接当经验。`OutcomeVerifier` 只允许环境/工具独立验证的
成功、失败与权限边界进入结构化经验库；M2 检索到的经验只是候选策略，必须在当前 Case
重新观察和验证。重复失败会生成知识、Skill、工具、权限或评测建设候选，但不会直接修改
Prompt、代码或生产配置。

M7.5 将成功判定收紧为“每项用户成功判据必须绑定到真实工具调用或验证目标”，并把经验
生命周期拆成 `retrieved → considered → adopted → verified`。只有实际影响过工具步骤的经验
才能获得后续结果反馈。持久化经验只接收工具契约白名单字段，并执行秘密、邮箱、手机号
脱敏以及深度、条数和字符串长度限制。

运行 8 个领域 × 13 种经验条件 × 3 种方案的控制实验：

```powershell
cd backend
D:\anaconda3\envs\concord\python.exe -m evaluation.m7_continual_improvement
```

当前 104 个场景、312 次变体运行中，结构化验证经验命中 32/32 条有用经验并拒绝
72/72 条状态键/值错配、跨域、未验证、已废弃、过期、权限、能力或工具版本不兼容经验。
较强的粗粒度结构化基线为 64/104，而不是故意设置的弱关键词基线。该结果验证的是检索安全契约，不代表真实
解决率提升；纵向真实收益需要后续累积独立验证的跨 Case 结果。

真实 `qwen3.8-flash` 三段链路额外验证：未授权轨迹不入库；独立验证后的 Case 形成经验；
下一个 Case 检索并引用该经验后，仍重新执行 5 次工具调用并验证两项成功标准。来源经验
收到一次 `supported` 反馈。M7.5 又在企业 IT、教育 IT、物流三个领域各运行一组
“源 Case → 目标 Case”真实模型链：3/3 均完成 retrieved → considered → adopted → verified，
但模型调用数、工具调用数没有下降，目标 Case 平均时延反而由 27.319 秒升至 33.249 秒。
因此这里只主张调用链成立，不主张经验已带来性能收益。当前全量后端回归为 `198 passed`，
前端生产构建通过。

改进候选支持隔离评测、人工批准/拒绝和回滚。管理接口默认关闭；受控环境可设置
`CONCORD_ENABLE_IMPROVEMENT_ADMIN=true`，但批准也不会触发生产 Agent 自行改代码或发布。
跨域访问默认只允许本机 Vite 地址；如需其他前端来源，使用逗号分隔的
`CONCORD_CORS_ORIGINS` 显式配置，不再默认公开 `*`。
结果位于
[`evaluation/m7_continual_improvement/results/latest/`](evaluation/m7_continual_improvement/results/latest/)。

创建一个最小模拟 Case：

```powershell
$case = @{
  case_id = "demo-vpn-001"
  visible_state = @{ vpn = "authentication_failed"; access = "blocked" }
  hidden_state = @{ root_cause = "stale_credential" }
  action_rules = @(
    @{
      action_id = "refresh_credential"
      description = "刷新本地凭据"
      preconditions = @(@{ path = "vpn"; operator = "eq"; value = "authentication_failed" })
      effects = @(@{ path = "vpn"; value = "connected" }, @{ path = "access"; value = "restored" })
      success_observation = "凭据已刷新，等待重新观察连接状态"
      verification_targets = @("vpn", "access")
    }
  )
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Method Post http://127.0.0.1:8000/runtime/cases `
  -ContentType application/json -Body $case

$call = @{
  tool_id = "simulation_execute_action"
  case_id = "demo-vpn-001"
  arguments = @{ action_id = "refresh_credential" }
  idempotency_key = "demo-vpn-001-refresh-1"
  permissions = @("simulation:act")
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post http://127.0.0.1:8000/runtime/tools/execute `
  -ContentType application/json -Body $call
```

### Skills

### 监控与评测

- `GET /monitor`

## 项目结构

```text
main.py                                          对外统一启动入口
api/main.py                                      FastAPI 应用与当前组合根
api/schemas.py                                   HTTP 请求/响应契约
api/routes/continual_improvement.py              默认关闭的研究/管理路由
core/problem_formulation/                        LangGraph 人机协同问题表征与行为策略
core/adaptive_resolution/                        LangGraph M2 闭环诊断、信念更新与进度控制
core/multi_agent_collaboration/                  LangGraph M3 动态招募、信息隔离、冲突与停止控制
core/continual_improvement/                      M7 结果验证、结构化经验、失败挖掘与受控改进
core/tools/                                      稳定工具契约、注册、策略与资源理性选择
infrastructure/memory/conversation_memory.py     Redis 工作记忆 + SQLite FTS 召回
infrastructure/tool_runtime/                      唯一工具执行、权限、幂等、缓存、熔断与审计
infrastructure/retrieval/                         rg 文件检索和 SQLite FTS 知识检索 Adapter
infrastructure/simulation/                        部分可观测模拟状态、虚拟时间、故障与审计
infrastructure/case_events.py                     持久、有序、幂等的运行中 Case 事件收件箱
infrastructure/agent_performance.py               仅由下游结果更新的协作者可靠性记录
infrastructure/knowledge/knowledge_base.py       SQLite FTS 内部知识索引
infrastructure/observability/                     在线监控与可选 LangSmith 轨迹出口
data/                                            持久化数据
```

## 运行时架构

```text
用户请求
  -> /chat
  -> MemoryManager 读取工作记忆、情景记忆、用户画像
  -> LangGraph 更新目标、情境、认识论声明和 Case-conditioned UserState
  -> InteractionNeedDetector + 编译策略
  -> 普通轮次一次 LLM 语义解释；空结构属于抽取失败并允许一次精简语义恢复
  -> 实质矛盾才额外调用 LangChain Behavior Planner
  -> 信息不足时执行自适应交互；CASE_READY 输出 ResolutionContext
  -> 写回 Redis 工作记忆和 SQLite 非权威召回索引
  -> 通过独立评测运行器生成实验结果
```

## 主要端口

| 服务 | 端口 |
|---|---:|
| Concord API | 8000 |
| Redis | 6379 |
| Prometheus | 9090 |
| Nginx | 80 |

## 开发和调试

常用顺序：

```text
1. /health
2. /chat
3. /monitor
```

`POST /chat` 的响应额外暴露 `case_status`、`evidence_sufficiency`、
`interaction_needs`、`interaction_action`、`control_priors`、
`resolution_context`、`m1_model_calls`、`trace_id` 和 `problem_state`，可直接用于 M1
多轮评测与轨迹分析。

`CASE_READY` 不表示所有证据齐全，而表示已经具备可行动的目标和问题锚点。
`resolution_context.evidence_handoff` 会完整携带开放、已解决、不可获得和延后证据；
`action_constraints` 使用白名单语义允许 M2 先做观察、验证和只读诊断，同时让
缺失证据约束依赖它的动作，而不是反过来阻止整个 Case 进入 M2。

如果你只想看项目怎么工作，直接读：

- [统一启动入口](main.py)
- [M1 问题表征](core/problem_formulation/)
- [M2 自适应求解](core/adaptive_resolution/)
- [M3 动态协作](core/multi_agent_collaboration/)

## 当前边界

现有 `/chat` 是可执行主链路；真实外部写入 Adapter 仍未开放，默认只读或写入可重置模拟环境。
